import random
import time
import re
import urllib.parse
import requests
import websocket
import json
from tkinter import StringVar, Entry
from plugins.base import Plugin
from core.chrome import cdp_navigate, close_chrome_session


def _send_cdp_cmd(ws, method, params=None):
    """Send a Chrome DevTools Protocol command and return the command ID."""
    if params is None:
        params = {}
    cmd = {"id": int(time.time() * 1000) % 1000000, "method": method, "params": params}
    ws.send(json.dumps(cmd))
    return cmd["id"]


def _activate_window(ws_url, log_fn=print):
    """Force Chrome to bring current tab/window to foreground."""
    try:
        ws = websocket.create_connection(ws_url, timeout=5)
        _send_cdp_cmd(ws, "Page.bringToFront")
        ws.close()
        log_fn("[CDP] Brought YouTube tab to front.")
    except Exception as e:
        log_fn(f"[CDP][WARN] could not focus window: {e}")


class PlayShortsPlugin(Plugin):
    name = "Play YouTube Shorts"
    group = "chrome"

    SHORTS_SEARCH_QUERIES = [
        "shorts", "trending shorts", "viral shorts", "funny shorts", "music shorts",
        "gaming shorts", "tech shorts", "life hacks shorts", "satisfying shorts",
        "fails shorts", "football shorts", "basketball shorts", "soccer shorts",
    ]
    SHORTS_REGEX = re.compile(r"/shorts/([A-Za-z0-9_-]{8,})")

    def build_ui(self, parent):
        self.count_var = StringVar(value="3")
        lbl = Entry(
            parent,
            textvariable=self.count_var,
            width=6,
            bg="#3c3f41",
            fg="#FFFFFF",
            relief="flat",
            state="disabled",
        )
        lbl.pack(side="left", padx=6)
        return {"count": self.count_var, "_entry": lbl}

    def _fetch(self, max_links=50, log=print):
        found_urls = []
        queries = self.SHORTS_SEARCH_QUERIES[:]
        random.shuffle(queries)
        for q in queries:
            try:
                url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(q)}"
                r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
                if r.status_code != 200:
                    continue
                matches = self.SHORTS_REGEX.findall(r.text)
                for sid in matches:
                    found_urls.append(f"https://www.youtube.com/shorts/{sid}")
                    if len(found_urls) >= max_links:
                        return found_urls
            except Exception:
                continue
        return found_urls

    def _wait_for_video_end(self, ws_url, timeout=40, log_fn=print):
        """Wait until YouTube Shorts video ends or timeout expires."""
        try:
            ws = websocket.create_connection(ws_url, timeout=8)
            _send_cdp_cmd(ws, "Runtime.enable")

            start_time = time.time()
            video_found = False

            while True:
                elapsed = time.time() - start_time
                if elapsed > timeout:
                    log_fn("[SHORTS] timeout reached (40s); moving to next video.")
                    break

                js = """
                (function() {
                    var v = document.querySelector('video');
                    if (!v) return {ready:false, pos:0, dur:0, ended:false};
                    return {ready:true, pos:v.currentTime, dur:v.duration, ended:v.ended};
                })();
                """
                cmd_id = _send_cdp_cmd(
                    ws,
                    "Runtime.evaluate",
                    {"expression": js, "awaitPromise": True, "returnByValue": True},
                )

                deadline = time.time() + 2
                while time.time() < deadline:
                    try:
                        msg = ws.recv()
                        data = json.loads(msg)
                        if data.get("id") == cmd_id:
                            res = data.get("result", {}).get("result", {}).get("value", {})
                            if isinstance(res, dict):
                                if not res.get("ready", False):
                                    break
                                video_found = True
                                pos = res.get("pos", 0)
                                dur = res.get("dur", 0)
                                ended = res.get("ended", False)
                                if ended or (dur > 0 and pos >= dur - 1):
                                    log_fn("[SHORTS] video ended.")
                                    ws.close()
                                    return
                            break
                    except Exception:
                        break

                if not video_found:
                    time.sleep(1.0)
                    continue

                time.sleep(2.0)

            ws.close()
        except Exception as e:
            log_fn(f"[SHORTS][ERROR] wait_for_video_end: {e}")

    def _reset_player(self, ws_url, log_fn=print):
        """Clear previous video softly before loading next one."""
        try:
            ws = websocket.create_connection(ws_url, timeout=8)
            js = """
            (function() {
                var v = document.querySelector('video');
                if (v) {
                    try { v.pause(); v.remove(); } catch(e){}
                }
            })();
            """
            _send_cdp_cmd(ws, "Runtime.evaluate", {"expression": js})
            ws.close()
        except Exception as e:
            log_fn(f"[SHORTS][WARN] could not reset player: {e}")

    def run(self, context):
        log = context["log"]
        sess = context["session"]

        try:
            n = max(0, int(self.count_var.get()))
        except Exception:
            n = 3

        if n <= 0 or not sess or not sess.ws_url:
            return

        links = self._fetch(max_links=max(50, n * 6), log=log)
        if not links:
            log("[SHORTS] no results")
            return

        random.shuffle(links)
        links = links[:n]

        # Open YouTube in current tab, then bring it to front
        first_url = "https://www.youtube.com"
        log("[SHORTS] Opening YouTube main page (foreground)…")
        cdp_navigate(sess.ws_url, first_url, wait_load=True, timeout=15, log_fn=log)
        _activate_window(sess.ws_url, log_fn=log)
        time.sleep(2.0)

        for i, url in enumerate(links, start=1):
            log(f"[SHORTS] ({i}/{n}) opening {url}")
            if i > 1:
                self._reset_player(sess.ws_url, log_fn=log)
                time.sleep(1.0)
            cdp_navigate(sess.ws_url, url, wait_load=True, timeout=20, log_fn=log)
            time.sleep(2.0)
            self._wait_for_video_end(sess.ws_url, timeout=40, log_fn=log)
            time.sleep(1.0)

        try:
            log("[SHORTS] All videos finished — closing Chrome cleanly.")
            close_chrome_session(sess, log_fn=log)
        except Exception as e:
            log(f"[SHORTS][WARN] graceful close failed: {e}")
