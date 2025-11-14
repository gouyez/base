# plugins/add_contacts.py
from tkinter import Frame, Label, Entry, StringVar
from plugins.base import Plugin

class AddContactsPlugin(Plugin):
    name = "Add contacts:"
    group = "api"
    no_checkbox = True   # show without checkbox
    order = -998         # ensure it appears right after SearchFilterPlugin

    def build_ui(self, parent):
        # Create a row containing a label and an entry box
        row = Frame(parent, bg="#2b2b2b")
        row.pack(fill="x", pady=2)

        Label(
            row,
            text=self.name,
            bg="#2b2b2b",
            fg="#FFFFFF",
            anchor="w"
        ).pack(side="left", padx=(2, 6))

        self.contacts_var = StringVar()
        entry = Entry(
            row,
            textvariable=self.contacts_var,
            width=40,
            bg="#3c3f41",
            fg="#FFFFFF",
            insertbackground="#FFFFFF",
            relief="flat",
        )
        entry.pack(side="left", fill="x", expand=True, padx=(0, 2))
        self.entry = entry

        return {"entry": entry}

    def run(self, context):
        """
        Use the entered contacts and add them via the People API.
        """
        people_service = context.get("people_service")
        log = context.get("log", print)

        if not people_service:
            log("[CONTACTS][ERROR] People API service not available.")
            return

        raw = self.contacts_var.get().strip()
        if not raw:
            log("[CONTACTS] No emails entered; skipping.")
            return

        emails = [x.strip() for x in raw.split(";") if x.strip()]
        if not emails:
            log("[CONTACTS] No valid emails found.")
            return

        added = 0
        for addr in emails:
            try:
                people_service.people().createContact(
                    body={"emailAddresses": [{"value": addr}]}
                ).execute()
                log(f"[CONTACTS] added: {addr}")
                added += 1
            except Exception as e:
                log(f"[CONTACTS][ERROR] {addr}: {e}")

        log(f"[CONTACTS] Done — {added} added.")
