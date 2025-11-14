import re
"""
Plugin: Mark as important
"""

from plugins.base import Plugin
from core.gmail_api import search_messages

class MarkAsImportantPlugin(Plugin):
    name = "Mark as important"
    group = "api"

    def build_ui(self, parent):
        return {}

    def run(self, context):
        log = context.get('log', print)
        svc = context.get('service')
        search_terms = context.get('search_terms', []) or []

        if not search_terms:
            log("[Mark as important] No search terms provided; skipping.")
            return
        if not svc:
            log("[Mark as important] Gmail service unavailable; skipping.")
            return

        mapping = {"add": ["IMPORTANT"], "remove": []}

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
                            log(f"[Mark as important] {mid} -> added {mapping['add']}")
                        except Exception as e:
                            log(f"[Mark as important][ERROR] {mid}: {e}")
        