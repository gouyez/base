# plugins/search_filter.py
from tkinter import Frame, Label, Entry, StringVar
from plugins.base import Plugin

class SearchFilterPlugin(Plugin):
    name = "Search terms: "
    group = "api"
    no_checkbox = True  # ensures it's shown without a checkbox
    order = -999        # always appears first in API actions

    def build_ui(self, parent):
        # Frame to contain label + entry side by side
        row = Frame(parent, bg="#2b2b2b")
        row.pack(fill="x", pady=2)

        # Label on the left
        Label(
            row,
            text=self.name,
            bg="#2b2b2b",
            fg="#FFFFFF",
            anchor="w"
        ).pack(side="left", padx=(2, 6))

        # Text entry on the right
        self.value_var = StringVar()
        entry = Entry(
            row,
            textvariable=self.value_var,
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
        # Sync current value into the app shared_search_term
        app = context.get("app")
        if app:
            app.shared_search_term = self.value_var.get().strip()
