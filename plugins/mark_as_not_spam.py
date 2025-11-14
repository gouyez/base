import re
"""
Plugin: Mark as not spam
"""

from plugins.base import Plugin
from core.gmail_api import search_messages

class MarkAsNotSpamPlugin(Plugin):
    name = "Mark as not spam"
    group = "api"

    def build_ui(self, parent):
        return {}

    def run(self, context):
        log = context.get('log', print)
        svc = context.get('service')
        search_terms = context.get('search_terms', []) or []

        if not search_terms:
            log("[Mark as not spam] No search terms provided; skipping.")
            return
        if not svc:
            log("[Mark as not spam] Gmail service unavailable; skipping.")
            return

        mapping = {"add": ["INBOX"], "remove": ["SPAM"]}

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
                        add_labels = list(dict.fromkeys(mapping.get('add', [])))
                        remove_labels = list(dict.fromkeys(mapping.get('remove', [])))
                        body = {}
                        if add_labels:
                            body['addLabelIds'] = add_labels
                        if remove_labels:
                            body['removeLabelIds'] = remove_labels
                        try:
                            svc.users().messages().modify(userId='me', id=mid, body=body).execute()
                            log(f"[Mark as not spam] {mid} -> add={add_labels} remove={remove_labels}")
                        except Exception as e:
                            log(f"[Mark as not spam][ERROR] {mid}: {e}")
