"""
gmail_api.py — Gmail / People API helpers + OAuth loopback (multi-account safe, final)
"""

import os, time, http.server, threading, urllib.parse, socket
from core.config import resource_path
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

CREDENTIALS_FILE = "credentials.json"
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/contacts",
]
CREDENTIALS_PATH = resource_path(CREDENTIALS_FILE)


# ---------- Token path ----------
def token_path_for(email: str) -> str:
    safe = "".join(c for c in email if c.isalnum() or c in ("@", ".", "_", "-")).replace("@", "_at_")
    return os.path.join("emails", f"{safe}.json")


# ---------- OAuth callback handler ----------
class _OAuthHandler(http.server.BaseHTTPRequestHandler):
    """Handles OAuth redirect callbacks to http://127.0.0.1."""
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        if parsed.path not in ("/", "/callback"):
            self.send_error(404)
            return

        code = qs.get("code", [None])[0]
        error = qs.get("error", [None])[0]

        # Get per-server shared dict
        shared = getattr(self.server, "shared", None)
        if shared is None:
            # Safety fallback (should never happen)
            self.send_error(500, "Missing shared context")
            return

        if error:
            shared["error"] = error
            html = "<h2>❌ Authorization denied.</h2>"
        elif code:
            shared["code"] = code
            html = (
                "<html><body style='font-family:sans-serif;text-align:center;padding:40px;'>"
                "<h2>✅ Authorization complete!</h2>"
                "<p>You can close this tab. Redirecting to Gmail…</p>"
                "<script>setTimeout(()=>{location.replace('https://mail.google.com/mail/u/0/#inbox');},2000);</script>"
                "</body></html>"
            )
        else:
            html = "<h2>⚠️ No authorization code received.</h2>"

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

        # Shutdown server asynchronously after responding
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, *args):
        return


class _ThreadedServer(http.server.ThreadingHTTPServer):
    daemon_threads = True


# ---------- Utility ----------
def _find_free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------- OAuth flow ----------
def _oauth_once_with_session(email, sess, log_fn):
    """Run OAuth flow inside the given Chrome session and return credentials."""
    if not os.path.exists(CREDENTIALS_PATH):
        raise FileNotFoundError("Missing credentials.json next to the app.")

    port = _find_free_port()
    redirect_uri = f"http://127.0.0.1:{port}/callback"

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CREDENTIALS_PATH), SCOPES, redirect_uri=redirect_uri
    )
    auth_url, _ = flow.authorization_url(
        access_type="offline", prompt="consent", include_granted_scopes="true"
    )

    # Per-thread shared dict for this OAuth session
    local_shared = {}

    # Create threaded HTTP server bound to this dict
    server = _ThreadedServer(("127.0.0.1", port), _OAuthHandler)
    server.shared = local_shared

    # Start background server thread
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    # Wait until port is open before launching Chrome
    deadline = time.time() + 3
    ready = False
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                ready = True
                break
        except OSError:
            time.sleep(0.1)
    if not ready:
        raise RuntimeError(f"OAuth callback server failed to start on port {port}")

    # Navigate Chrome to authorization URL
    from core.chrome import cdp_navigate
    cdp_navigate(sess.ws_url, auth_url, wait_load=False, log_fn=log_fn)
    log_fn(f"[OAUTH] waiting for callback on port {port} for {email} …")

    # Wait for callback
    deadline = time.time() + 600
    while time.time() < deadline:
        if "code" in local_shared:
            flow.fetch_token(code=local_shared["code"])
            creds = flow.credentials
            tok_path = token_path_for(email)
            os.makedirs(os.path.dirname(tok_path), exist_ok=True)
            with open(tok_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
            log_fn(f"[TOKEN] saved for {email}: {tok_path}")
            return creds, True

        if "error" in local_shared:
            raise RuntimeError(f"OAuth error for {email}: {local_shared['error']}")

        time.sleep(0.25)

    raise TimeoutError(f"Timed out waiting for OAuth callback for {email}")


# ---------- Public wrappers ----------
def oauth_first_login_in_session(email, sess, log_fn, also_open_gmail_ui=False):
    try:
        return _oauth_once_with_session(email, sess, log_fn)
    except Exception as e:
        log_fn(f"[OAuth] first attempt failed: {e}; retrying …")
        time.sleep(2)
        return _oauth_once_with_session(email, sess, log_fn)


def load_credentials_for(email, log_fn):
    tok = token_path_for(email)
    creds = None

    if os.path.exists(tok):
        try:
            creds = Credentials.from_authorized_user_file(tok, SCOPES)
        except Exception as e:
            log_fn(f"[TOKEN] parse failed for {email}: {e}")
            creds = None

    if creds and creds.valid:
        log_fn("[TOKEN] valid")
        return creds

    if creds and creds.expired and creds.refresh_token:
        log_fn("[TOKEN] expired -> refreshing")
        try:
            creds.refresh(Request())
            with open(tok, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
            log_fn("[TOKEN] refreshed")
            return creds
        except Exception as e:
            log_fn(f"[TOKEN] refresh failed: {e}")
            raise

    raise RuntimeError("No valid token for this account.")


def build_gmail_service(creds):
    return build("gmail", "v1", credentials=creds)


def build_people_service(creds):
    return build("people", "v1", credentials=creds)


def search_messages(service, query, max_results=500, log_fn=None):
    messages = []
    try:
        req = service.users().messages().list(
            userId="me", q=query, maxResults=100, includeSpamTrash=True
        )
        while req:
            resp = req.execute()
            if resp.get("messages"):
                messages.extend(resp["messages"])
                if log_fn:
                    log_fn(f"[SEARCH] {len(messages)} found so far…")
            req = service.users().messages().list_next(req, resp)
            if len(messages) >= max_results:
                break
    except Exception as e:
        if log_fn:
            log_fn(f"[SEARCH][ERROR] {e}")
    return messages[:max_results]


def get_message_full(service, msg_id):
    return service.users().messages().get(userId="me", id=msg_id, format="full").execute()

def mark_as_read(service, msg_id, log_fn=None):
    """
    Mark a Gmail message as read by removing the UNREAD label.
    """
    try:
        service.users().messages().modify(
            userId="me",
            id=msg_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()
        if log_fn:
            log_fn(f"[GMAIL] marked as read: {msg_id}")
        return True
    except Exception as e:
        if log_fn:
            log_fn(f"[GMAIL][ERROR] mark_as_read({msg_id}): {e}")
        return False
