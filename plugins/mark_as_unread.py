import re
"""
Plugin: Mark as unread
"""

from plugins.base import Plugin
from core.gmail_api import search_messages

class MarkAsUnreadPlugin(Plugin):
    name = "Mark as unread"
    group = "api"

    def build_ui(self, parent):
        return {}

    def run(self, context):
        log = context.get('log', print)
        svc = context.get('service')
        search_terms = context.get('search_terms', []) or []

        if not search_terms:
            log("[Mark as unread] No search terms provided; skipping.")
            return
        if not svc:
            log("[Mark as unread] Gmail service unavailable; skipping.")
            return

        mapping = {"add": ["UNREAD"], "remove": []}

        for term in search_terms:
            term = term.strip()
            if not term:
                continue
            subterms = [t.strip() for t in term.split(';') if t.strip()]
            for subterm in subterms:
                q = f'(from:"{subterm}" OR subject:"{subterm}") in:inbox'
                msgs = search_messages(svc, q, max_results=500, log_fn=log)
                if not msgs:
                    log(f"[{self.name}] No messages found for '{subterm}'")
                    continue
                for m in msgs:
                        mid = m.get('id')
                        body = {"addLabelIds": mapping["add"]}
                        try:
                            svc.users().messages().modify(userId='me', id=mid, body=body).execute()
                            log(f"[Mark as unread] {mid} -> added {mapping['add']}")
                        except Exception as e:
                            log(f"[Mark as unread][ERROR] {mid}: {e}")
        