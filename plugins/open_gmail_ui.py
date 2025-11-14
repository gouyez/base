from plugins.base import Plugin
from core.chrome import cdp_navigate

class OpenGmailUIPlugin(Plugin):
    name = "Open Gmail UI (always)"
    group = "chrome"
    keep_open_after_run = True

    def run(self, context):
        log = context["log"]; sess = context["session"]
        if not sess or not sess.ws_url:
            log("[UI][ERROR] No Chrome session."); return
        cdp_navigate(sess.ws_url, "https://mail.google.com/mail/u/0/#inbox", wait_load=True, timeout=8, log_fn=log)
        log("[UI] Inbox opened and will be kept open.")
