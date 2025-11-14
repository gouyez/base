# plugins/click_links.py
import time
import json
import re
import base64
import urllib.parse
from typing import List, Tuple, Optional

from plugins.base import Plugin
from tkinter import StringVar, Entry

import requests
try:
    import websocket
except Exception:
    websocket = None

from core.gmail_api import search_messages, get_message_full, mark_as_read
from core.chrome import cdp_navigate, close_chrome_session  # assume these exist


class ClickLinksPlugin(Plugin):
    name = "Click links (from unread emails)"
    group = "chrome"

    def build_ui(self, parent):
        self.count_var = StringVar(value="3")
        entry = Entry(
            parent,
            textvariable=self.count_var,
            width=6,
            bg="#3c3f41",
            fg="#FFFFFF",
            relief="flat",
            state="disabled",
        )
        entry.pack(side="left", padx=6)
        return {"count": self.count_var, "_entry": entry}

    @staticmethod
    def _is_valid_web_link(url: str) -> bool:
        url_l = url.lower()
        if not url_l.startswith("http"):
            return False
        bad_ext = (
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".svg",
            ".webp",
            ".mp4",
            ".mov",
            ".avi",
            ".mkv",
            ".webm",
            ".gifv",
        )
        base = url_l.split("?", 1)[0]
        return not any(base.endswith(ext) for ext in bad_ext)

    def _extract_links_from_payload(self, payload) -> List[str]:
        texts = []

        def decode_b64(s):
            try:
                return base64.urlsafe_b64decode(s.encode("ASCII")).decode("utf-8", errors="replace")
            except Exception:
                return ""

        def walk(part):
            mime = (part.get("mimeType") or "").lower()
            body = part.get("body", {})
            data = body.get("data")
            if mime in ("text/html", "text/plain") and data:
                texts.append(decode_b64(data))
            for p in part.get("parts", []) or []:
                walk(p)

        walk(payload)
        all_text = "\n".join(texts)
        links = re.findall(r"(https?://[^\s\"'<>]+)", all_text)
        cleaned = [u.rstrip(").,;'\"!?]") for u in links]
        seen = set()
        uniq = []
        for u in cleaned:
            if u not in seen:
                seen.add(u)
                uniq.append(u)
        return uniq

    def _open_tab_via_debug_http(
        self, port: int, ws_url: str, target_url: str, log
    ) -> Optional[Tuple[str, dict]]:
        """Create a new tab in the *specific Chrome instance* associated with this session."""
        if websocket is None:
            log("[LINKS][ERROR] websocket-client missing.")
            return None

        try:
            ws = websocket.create_connection(ws_url, timeout=6)
        except Exception as e:
            log(f"[LINKS][ERROR] cannot connect to CDP session ({port}): {e}")
            return None

        try:
            msg_id = 1000

            def send(method, params=None):
                nonlocal msg_id
                msg_id += 1
                payload = {"id": msg_id, "method": method}
                if params is not None:
                    payload["params"] = params
                ws.send(json.dumps(payload))
                return msg_id

            call_id = send("Target.createTarget", {"url": target_url})

            # Wait for targetId
            deadline = time.time() + 4.0
            target_id = None
            while time.time() < deadline:
                try:
                    raw = ws.recv()
                except Exception:
                    continue
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if msg.get("id") == call_id:
                    target_id = (msg.get("result") or {}).get("targetId")
                    break

            try:
                ws.close()
            except Exception:
                pass

            if not target_id:
                log(f"[LINKS][WARN] Target.createTarget returned no targetId on port {port}.")
                return None

            # Match via /json/list
            try:
                resp = requests.get(f"http://127.0.0.1:{port}/json/list", timeout=3)
                if resp.status_code != 200:
                    log(f"[LINKS][WARN] /json/list returned {resp.status_code} (port {port})")
                    return None
                for entry in resp.json():
                    if entry.get("id") == target_id:
                        ws_tab = entry.get("webSocketDebuggerUrl")
                        if ws_tab:
                            return ws_tab, entry
            except Exception as e:
                log(f"[LINKS][WARN] /json/list lookup failed (port {port}): {e}")
                return None

            log(f"[LINKS][WARN] created target not found in /json/list (port {port})")
            return None

        except Exception as e:
            log(f"[LINKS][ERROR] Target.createTarget flow failed ({port}): {e}")
            try:
                ws.close()
            except Exception:
                pass
            return None

    def _tab_ready_state(self, ws_url: str, log, timeout=4.0) -> str | None:
        if websocket is None:
            return None
        try:
            ws_conn = websocket.create_connection(ws_url, timeout=6)
        except Exception:
            return None
        try:
            _id = 1000

            def send(msg):
                nonlocal _id
                _id += 1
                msg["id"] = _id
                ws_conn.send(json.dumps(msg))
                return _id

            send({"method": "Runtime.enable", "params": {}})
            eval_id = send(
                {
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": "document.readyState",
                        "awaitPromise": False,
                        "returnByValue": True,
                    },
                }
            )
            end = time.time() + timeout
            while time.time() < end:
                try:
                    raw = ws_conn.recv()
                except Exception:
                    time.sleep(0.05)
                    continue
                try:
                    m = json.loads(raw)
                except Exception:
                    continue
                if m.get("id") == eval_id:
                    val = (m.get("result") or {}).get("result", {}).get("value")
                    return val
            return None
        finally:
            try:
                ws_conn.close()
            except Exception:
                pass

    def run(self, context):
        log = context.get("log", print)
        svc = context.get("service")
        sess = context.get("session")  # unique ChromeSession per account

        raw_search = context.get("raw_search", "").strip() if context.get("raw_search") else ""
        if not raw_search and hasattr(self, "app") and getattr(self.app, "search_entry", None):
            try:
                raw_search = self.app.search_entry.get().strip()
            except Exception:
                raw_search = ""

        try:
            max_links = max(0, int(self.count_var.get()))
        except Exception:
            max_links = 3
        if max_links <= 0:
            return

        if not svc:
            log("[LINKS][ERROR] Gmail service missing in context.")
            return
        if not sess or not getattr(sess, "ws_url", None):
            log("[LINKS][ERROR] No Chrome session.")
            return

        # Build Gmail search query
        keywords = [k.strip() for k in (raw_search or "").split(";") if k.strip()]
        if keywords:
            parts = [f'from:"{kw}" OR subject:"{kw}"' for kw in keywords]
            gmail_query = "(" + " OR ".join(parts) + ") is:unread in:inbox"
        else:
            gmail_query = "is:unread in:inbox"

        log(f"[LINKS] query: {gmail_query}")

        # Fetch messages
        msgs = []
        try:
            msgs = search_messages(svc, gmail_query, max_results=200, log_fn=log)
        except Exception as e:
            log(f"[LINKS][ERROR] search failed: {e}")
        if not msgs:
            log("[LINKS] No unread messages found; nothing to open.")
            return

        # Extract URLs
        url_items: List[Tuple[str, str]] = []
        for m in msgs:
            if len(url_items) >= max_links:
                break
            mid = m.get("id")
            try:
                full = get_message_full(svc, mid)
            except Exception as e:
                log(f"[LINKS][ERROR] fetch {mid}: {e}")
                continue
            for u in self._extract_links_from_payload(full.get("payload", {})):
                if self._is_valid_web_link(u):
                    url_items.append((mid, u))
                    if len(url_items) >= max_links:
                        break

        if not url_items:
            log("[LINKS] no valid web URLs found.")
            return

        log(f"[LINKS] Found {len(url_items)} link(s) — opening them 2s apart…")

        # Open Gmail inbox first
        try:
            cdp_navigate(sess.ws_url, "https://mail.google.com/mail/u/0/#inbox", wait_load=False, log_fn=log)
            time.sleep(0.8)
        except Exception as e:
            log(f"[LINKS][WARN] could not navigate inbox: {e}")

        opened_tabs = []
        for idx, (mid, u) in enumerate(url_items):
            if idx > 0:
                time.sleep(2.0)
            res = self._open_tab_via_debug_http(sess.port, sess.ws_url, u, log)
            if res:
                ws_url, meta = res
                opened_tabs.append((mid, ws_url, u))
                log(f"[LINKS] opened: {u}")
            else:
                log(f"[LINKS][WARN] could not open via CDP: {u}")
                continue

            # Mark message as read after opening
            try:
                mark_as_read(svc, mid, log_fn=log)
            except Exception as e:
                log(f"[LINKS][WARN] could not mark {mid} read: {e}")

        if not opened_tabs:
            log("[LINKS] No tabs actually opened.")
            return

        # Wait for tabs to load
        log("[LINKS] waiting on all tabs to load…")
        start_time = time.time()
        total_timeout = 30.0
        while time.time() - start_time < total_timeout:
            for tup in opened_tabs[:]:
                mid, ws_url, page_url = tup
                rs = self._tab_ready_state(ws_url, log, timeout=2.0)
                if rs == "complete":
                    log(f"[LINKS] tab loaded: {page_url}")
                    opened_tabs.remove(tup)
            if not opened_tabs:
                break
            time.sleep(0.5)

        log("[LINKS] Done waiting. (closing websockets if needed)")
        return
