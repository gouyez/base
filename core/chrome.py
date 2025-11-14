"""
chrome.py — Chrome / CDP session helpers (extracted from single-file)
Includes cdp_navigate helper used by plugins.
"""

import os, socket, time, json, shutil, subprocess
from pathlib import Path
from dataclasses import dataclass
import requests

try:
    import websocket
except Exception:
    websocket = None

from core.config import MASTER_CHROME_DIR, CHROMES_DIR, PROFILES_DIR, resource_path
from core.utils import safe_print

def _find_free_port_tcp():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port

def _http_json(url, timeout=6):
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None

def _wait_for_debug_endpoint(port: int, timeout=12):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"http://127.0.0.1:{port}/json", timeout=1)
            if r.status_code == 200:
                return True
        except Exception:
            time.sleep(0.2)
    return False

def _create_new_tab_and_get_ws(port: int, initial_url: str = "about:blank"):
    try:
        info = _http_json(f"http://127.0.0.1:{port}/json/new?{initial_url}", timeout=6)
        if info and info.get("webSocketDebuggerUrl"):
            return info["webSocketDebuggerUrl"]
        info_list = _http_json(f"http://127.0.0.1:{port}/json", timeout=4)
        if not info_list:
            return None
        for entry in info_list:
            if entry.get("webSocketDebuggerUrl"):
                return entry["webSocketDebuggerUrl"]
    except Exception:
        return None
    return None

def _find_chrome_executable(base_dir: Path):
    candidates = [
        base_dir / "chrome.exe",
        base_dir / "Application" / "chrome.exe",
        base_dir / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    for c in candidates:
        if c.exists():
            return c
    for root, _, files in os.walk(base_dir):
        if "chrome.exe" in files:
            return Path(root) / "chrome.exe"
    return None

def _safe_email_token(email: str) -> str:
    return "".join(c for c in email.lower() if c.isalnum() or c in ("@", ".", "_", "-")).replace("@", "_at_").replace(".", "_")

def cloned_install_dir_for(email: str) -> Path:
    return CHROMES_DIR / _safe_email_token(email)

def profile_dir_for(email: str) -> Path:
    return PROFILES_DIR / _safe_email_token(email)

@dataclass
class ChromeSession:
    email: str
    port: int
    proc: subprocess.Popen
    ws_url: str

def _find_chrome_in_clone(dest: Path):
    # search for chrome.exe in dest
    for root, _, files in os.walk(dest):
        if "chrome.exe" in files:
            return Path(root) / "chrome.exe"
    return None

def start_chrome_session(email, log_fn=print):
    if websocket is None:
        raise RuntimeError("Missing websocket-client")
    port = _find_free_port_tcp()
    dest = cloned_install_dir_for(email)
    exe = _find_chrome_executable(dest) or _find_chrome_executable(Path("C:/Program Files/Google/Chrome/Application"))
    if not exe or not exe.exists():
        log_fn("[SESSION][ERROR] chrome.exe not found for cloning; ensure chrome_master or Chrome installed.")
        return None

    pdir = profile_dir_for(email)
    pdir.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(exe),
        f"--user-data-dir={str(pdir)}",
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-default-apps",
        "--disable-translate",
        "--autoplay-policy=no-user-gesture-required",
        "--ignore-certificate-errors",
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        log_fn(f"[SESSION][ERROR] start chrome: {e}")
        return None

    if not _wait_for_debug_endpoint(port, timeout=12):
        log_fn(f"[SESSION][ERROR] debug endpoint not ready on port {port}")
        try:
            proc.terminate()
        except Exception:
            pass
        return None

    ws_url = None
    deadline = time.time() + 10
    while time.time() < deadline and not ws_url:
        ws_url = _create_new_tab_and_get_ws(port, initial_url="about:blank")
        if not ws_url:
            time.sleep(0.25)
    if not ws_url:
        try:
            proc.terminate()
        except Exception:
            pass
        return None
    log_fn(f"[SESSION] started for {email} (port {port})")
    return ChromeSession(email=email, port=port, proc=proc, ws_url=ws_url)

def close_chrome_session(sess, log_fn=print, wait_timeout: float = 5.0):
    """
    Gracefully close a Chrome session launched via start_chrome_session.
    - Tries Browser.close via websocket (preferred)
    - Falls back to sending window.close() to flush cookies
    - Waits briefly for graceful shutdown before forcing terminate()
    """

    if not sess:
        log_fn("[SESSION][WARN] close_chrome_session called with None")
        return

    port = getattr(sess, "port", None)
    ws_main = None

    # Step 1: get main debugger websocket if available
    try:
        info = _http_json(f"http://127.0.0.1:{port}/json/version", timeout=3)
        if info and info.get("webSocketDebuggerUrl"):
            ws_main = info["webSocketDebuggerUrl"]
    except Exception:
        ws_main = None

    # Step 2: try graceful close using Browser.close or window.close()
    if ws_main and websocket is not None:
        try:
            ws = websocket.create_connection(ws_main, timeout=5)
            # 1) ask Chrome to close all windows
            _id = 1
            def send(method, params=None):
                nonlocal _id
                _id += 1
                payload = {"id": _id, "method": method}
                if params:
                    payload["params"] = params
                ws.send(json.dumps(payload))

            try:
                send("Runtime.enable")
                send("Runtime.evaluate", {"expression": "window.close()"})
            except Exception:
                pass

            log_fn(f"[SESSION] waiting {wait_timeout}s for Chrome to flush cookies/state…")
            time.sleep(wait_timeout)

            # 2) try Browser.close (clean exit)
            try:
                send("Browser.close")
                log_fn("[SESSION] sent Browser.close command.")
            except Exception as e:
                log_fn(f"[SESSION][WARN] Browser.close failed: {e}")

            try:
                ws.close()
            except Exception:
                pass

        except Exception as e:
            log_fn(f"[SESSION][WARN] websocket graceful close failed: {e}")

    else:
        log_fn("[SESSION][WARN] no websocket debugger; using process terminate fallback.")

    # Step 3: wait for process to end (up to 6s)
    try:
        sess.proc.wait(timeout=6.0)
        log_fn("[SESSION] Chrome exited cleanly.")
        return
    except Exception:
        pass

    # Step 4: fallback to terminate/kill
    try:
        log_fn("[SESSION] Chrome not exiting — forcing terminate.")
        sess.proc.terminate()
        sess.proc.wait(timeout=2.0)
    except Exception:
        try:
            log_fn("[SESSION][WARN] terminate() failed — forcing kill().")
            sess.proc.kill()
        except Exception as e:
            log_fn(f"[SESSION][ERROR] could not kill process: {e}")


# ----- New helper exported for plugins -----
def cdp_navigate(ws_url: str, url: str, wait_load=True, timeout=12, log_fn=print) -> bool:
    """
    Navigate the current Chrome tab to the given URL using CDP (via ws_url).
    Returns True if navigation produced a Page.loadEventFired within timeout (when wait_load=True).
    """
    if websocket is None:
        log_fn("[CDP] websocket-client missing.")
        return False
    try:
        ws = websocket.create_connection(ws_url, timeout=8)
    except Exception as e:
        log_fn(f"[CDP] connect failed: {e}")
        return False

    ok = False
    try:
        # enable page domain and navigate
        msg_enable = {"id": 1, "method": "Page.enable", "params": {}}
        ws.send(json.dumps(msg_enable))
        nav_id = 2
        nav_msg = {"id": nav_id, "method": "Page.navigate", "params": {"url": url}}
        ws.send(json.dumps(nav_msg))
        if wait_load:
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    raw = ws.recv()
                except Exception:
                    time.sleep(0.05)
                    continue
                try:
                    m = json.loads(raw)
                except Exception:
                    continue
                if m.get("method") == "Page.loadEventFired":
                    ok = True
                    break
        else:
            ok = True
    except Exception as e:
        log_fn(f"[CDP] navigate error: {e}")
        ok = False
    finally:
        try:
            ws.close()
        except Exception:
            pass
    if ok:
        time.sleep(0.2)
    return ok
