#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gmail_hybrid_manager.py — launcher / GUI (plugin loader)
Now starts Chrome **only when required** by a plugin.
"""

import os
import sys
import time
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog

# Make core/plugins package importable
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from core.config import APP_VERSION, ensure_master_extracted, ensure_tokens_dir
from core.chrome import start_chrome_session, close_chrome_session
from core.gmail_api import (
    load_credentials_for,
    oauth_first_login_in_session,
    build_gmail_service,
    build_people_service,
)
from plugins import discover_plugins, Plugin


class GmailHybridApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Gmail Hybrid Manager (Windows)")
        self.geometry("1100x780")
        self.configure(bg="#2b2b2b")

        ensure_tokens_dir()

        self.accounts_file = None
        self.plugins = discover_plugins()
        self.enabled_vars = {}
        self.plugin_ui = {}
        self.shared_search_term = ""  # Shared search term from SearchFilterPlugin

        self._setup_styles()
        self._create_widgets()

        if not ensure_master_extracted(self._log_console):
            messagebox.showerror(
                "Chrome master not found",
                "Could not find embedded 'chrome_master'.\n\n"
                "Dev mode: put a 'chrome_master' folder next to this .py/.exe.\n"
                'Build mode: include with PyInstaller --add-data "chrome_master;chrome_master".',
            )

    def _setup_styles(self):
        style = ttk.Style(self)
        style.theme_use("default")
        style.configure(
            "Vertical.TScrollbar",
            background="#3c3f41",
            darkcolor="#3c3f41",
            lightcolor="#3c3f41",
            troughcolor="#2b2b2b",
            bordercolor="#2b2b2b",
            arrowcolor="#FFFFFF",
        )

    def _create_widgets(self):
        main_frame = tk.Frame(self, bg="#2b2b2b")
        main_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        # Left side (accounts + log)
        left = tk.Frame(main_frame, bg="#2b2b2b")
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        ttk.Label(left, text="Gmail accounts (one per line):",
                  background="#2b2b2b", foreground="#EEEEEE").pack(anchor="w")
        self.accounts_box = scrolledtext.ScrolledText(
            left, height=8, bg="#3c3f41", fg="#FFFFFF", insertbackground="#FFFFFF", relief="flat"
        )
        self.accounts_box.pack(fill=tk.X, padx=2, pady=4)

        # File row
        accounts_file_row = tk.Frame(left, bg="#2b2b2b")
        accounts_file_row.pack(fill=tk.X, pady=(2, 0))
        self.btn_browse_accounts = tk.Button(
            accounts_file_row, text="Browse…", command=self._browse_accounts_file,
            bg="#3c3f41", fg="#FFFFFF", relief="flat", padx=10, pady=2
        )
        self.btn_browse_accounts.pack(side="left")
        self.accounts_file_label = tk.Label(
            accounts_file_row, text="", bg="#2b2b2b", fg="#AAAAAA", anchor="w"
        )
        self.accounts_file_label.pack(side="left", padx=8, expand=True, fill=tk.X)
        self.btn_clear_accounts = tk.Button(
            accounts_file_row, text="Clear file", command=self._clear_accounts_file,
            bg="#3c3f41", fg="#FFFFFF", relief="flat", padx=10, pady=2
        )
        self.btn_clear_accounts.pack_forget()

        # Right side (actions)
        right = tk.Frame(main_frame, bg="#2b2b2b")
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=6)

        chrome_group = tk.LabelFrame(right, text="Chrome actions", bg="#2b2b2b",
                                     fg="#EEEEEE", padx=8, pady=8, relief="groove", bd=2)
        chrome_group.pack(fill=tk.X, pady=(0, 10))

        api_group = tk.LabelFrame(right, text="API actions", bg="#2b2b2b",
                                  fg="#EEEEEE", padx=8, pady=8, relief="groove", bd=2)
        api_group.pack(fill=tk.X)

        # Concurrency section
        cc_frame = tk.LabelFrame(right, text="Concurrency", bg="#2b2b2b",
                                 fg="#EEEEEE", padx=8, pady=8, relief="groove", bd=2)
        cc_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(10, 0))
        
        cc_row = tk.Frame(cc_frame, bg="#2b2b2b")
        cc_row.pack(fill=tk.X, pady=(0, 2))
        
        tk.Label(
            cc_row,
            text="Max concurrent Chrome sessions (per batch of 10):",
            bg="#2b2b2b",
            fg="#FFFFFF"
        ).pack(side="left", anchor="w")
        
        self.concurrent_var = tk.StringVar(value="10")
        tk.Spinbox(
            cc_row,
            from_=1,
            to=50,
            textvariable=self.concurrent_var,
            width=5,
            bg="#3c3f41",
            fg="#FFFFFF",
            insertbackground="#FFFFFF",
            relief="flat"
        ).pack(side="left", padx=(8, 0))


        # Buttons
        btn = tk.Frame(left, bg="#2b2b2b")
        btn.pack(fill=tk.X, pady=6)
        self.start_btn = tk.Button(btn, text="Start Processing", command=self.on_start,
                                   bg="#3c3f41", fg="#FFFFFF", relief="flat")
        self.start_btn.pack(side="left")
        tk.Button(btn, text="Clear Log", command=self.clear_log,
                  bg="#3c3f41", fg="#FFFFFF", relief="flat").pack(side="left", padx=6)

        ttk.Label(left, text="Log:", background="#2b2b2b", foreground="#EEEEEE").pack(anchor="w", pady=(4, 0))
        self.log_box = scrolledtext.ScrolledText(left, height=18, bg="#3c3f41", fg="#FFFFFF",
                                                 insertbackground="#FFFFFF", relief="flat")
        self.log_box.pack(fill=tk.BOTH, expand=True, pady=4)

        # Sort and position plugins
        api_plugins = [p for p in self.plugins if p.group == "api"]
        chrome_plugins = [p for p in self.plugins if p.group == "chrome"]

        api_plugins.sort(key=lambda p: getattr(p, "order", 0))
        special = [p for p in api_plugins if getattr(p, "no_checkbox", False)]
        api_plugins = special + [p for p in api_plugins if p not in special]

        # Build plugin UI
        for p in api_plugins + chrome_plugins:
            target_box = chrome_group if p.group == "chrome" else api_group
            self._add_plugin_row(p, target_box)

    def _add_plugin_row(self, plugin, parent_box):
        """Add a plugin to the appropriate section (API or Chrome)."""
        # ✅ Skip creating a checkbox entirely if plugin requests it
        if getattr(plugin, "skip_checkbox", False):
            try:
                ui = plugin.build_ui(parent_box) or {}
                self.plugin_ui[plugin] = ui
                # Mark as always enabled (since it has no master toggle)
                self.enabled_vars[plugin] = tk.BooleanVar(value=True)
                plugin._var = self.enabled_vars[plugin]
                return
            except Exception as e:
                self._log_console(f"[PLUGIN][ERROR] build_ui {plugin.name or 'unnamed'}: {e}")
                return

        # ✅ Existing logic for normal plugins (with checkboxes)
        if getattr(plugin, "no_checkbox", False):
            try:
                ui = plugin.build_ui(parent_box) or {}
                self.plugin_ui[plugin] = ui
                self.enabled_vars[plugin] = tk.BooleanVar(value=True)
                plugin._var = self.enabled_vars[plugin]

                if "entry" in ui:
                    entry = ui["entry"]

                    def update_term(event=None):
                        self.shared_search_term = entry.get().strip()
                    entry.bind("<KeyRelease>", update_term)
                    entry.bind("<FocusOut>", update_term)
                    entry.focus_set()
                return
            except Exception as e:
                self._log_console(f"[PLUGIN][ERROR] build_ui {plugin.name}: {e}")
                return

        # ✅ Normal plugin (creates checkbox)
        row = tk.Frame(parent_box, bg="#2b2b2b")
        row.pack(fill=tk.X, pady=2)
        var = tk.BooleanVar(value=False)
        chk = tk.Checkbutton(
            row, text=plugin.name, variable=var,
            bg="#2b2b2b", fg="#FFFFFF", selectcolor="#3c3f41",
            activebackground="#3c3f41", activeforeground="#FFFFFF", relief="flat"
        )
        chk.pack(side=tk.LEFT, anchor="w")

        ui = {}
        try:
            ui = plugin.build_ui(row) or {}
        except Exception as e:
            self._log_console(f"[PLUGIN][ERROR] build_ui {plugin.name}: {e}")

        # Disable all UI widgets initially
        for v in ui.values():
            try:
                if hasattr(v, "config"):
                    v.config(state=tk.DISABLED)
            except Exception:
                pass

        def _toggle():
            state = tk.NORMAL if var.get() else tk.DISABLED
            for v in ui.values():
                try:
                    if hasattr(v, "config"):
                        v.config(state=state)
                except Exception:
                    pass

        chk.config(command=_toggle)
        self.enabled_vars[plugin] = var
        plugin._var = var
        self.plugin_ui[plugin] = ui 

    def _browse_accounts_file(self):
        path = filedialog.askopenfilename(title="Select Accounts File", filetypes=[("Text Files", "*.txt"), ("All", "*.*")])
        if path:
            self.accounts_file = path
            try:
                self.accounts_box.config(state=tk.DISABLED)
            except Exception:
                pass
            self.accounts_file_label.config(text=f"Loaded from: {path}  (textarea disabled)")
            self.btn_clear_accounts.pack(side="right")

    def _clear_accounts_file(self):
        self.accounts_file = None
        self.accounts_file_label.config(text="")
        try:
            self.accounts_box.config(state=tk.NORMAL)
        except Exception:
            pass
        self.btn_clear_accounts.pack_forget()

    def _log_console(self, msg): print(msg)
    def log(self, msg):
        self.log_box.insert(tk.END, msg + "\n")
        self.log_box.see(tk.END)
        self.update_idletasks()
    def log_threadsafe(self, msg): self.after(0, lambda: self.log(msg))
    def clear_log(self): self.log_box.delete("1.0", tk.END)

    def on_start(self):
        self.start_btn.config(state=tk.DISABLED)
        threading.Thread(target=self._run_processing_parallel_worker, daemon=True).start()

    def _run_processing_parallel_worker(self):
        try:
            self.run_processing_parallel()
        finally:
            self.after(0, lambda: self.start_btn.config(state=tk.NORMAL))

    def run_processing_parallel(self):
        """Collect accounts, determine active plugins, and run them in parallel."""
        accounts = []
        if self.accounts_file:
            try:
                with open(self.accounts_file, "r", encoding="utf-8") as f:
                    accounts = [l.strip() for l in f if l.strip()]
            except Exception as e:
                self.log(f"[ERROR] Cannot read accounts file: {e}")
                return
        else:
            text = self.accounts_box.get("1.0", tk.END)
            accounts = [l.strip() for l in text.splitlines() if l.strip()]

        if not accounts:
            self.log("[INPUT] No accounts provided.")
            return

        self.log(f"[INPUT] Loaded {len(accounts)} account(s).")

        # Collect only enabled plugins
        enabled = [p for p in self.plugins if self.enabled_vars.get(p) and self.enabled_vars[p].get()]
        if not enabled:
            self.log("[PLUGIN] No actions selected.")
            return

        max_concurrent = 1
        try:
            max_concurrent = max(1, int(self.concurrent_var.get()))
        except Exception:
            pass

        self.log(f"[BATCH] Running up to {max_concurrent} accounts in parallel.")

        with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            futures = [executor.submit(self._process_one_account, email, enabled, self.log_threadsafe) for email in accounts]
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as e:
                    self.log(f"[THREAD][ERROR] {e}")
                    
        # ✅ When all futures complete successfully:
        self.log(f"\n=== Finished processing {len(accounts)} account(s) ===")
        self.after(0, lambda: messagebox.showinfo("All Done!", f"✅ Finished processing {len(accounts)} account(s)."))

    def _process_one_account(self, email, plugins, log_fn):
        log_fn(f"--- Processing: {email} ---")

        # Determine if Chrome is required
        requires_chrome = any(getattr(p, "group", "") == "chrome" for p in plugins)

        sess = None
        if requires_chrome:
            sess = start_chrome_session(email, log_fn=log_fn)
            if not sess:
                log_fn("[SESSION][FATAL] could not start chrome for account.")
                return
        else:
            log_fn(f"[SESSION] Chrome not required for {email}.")

        try:
            creds = load_credentials_for(email, log_fn)
        except Exception as e:
            log_fn(f"[TOKEN] missing/invalid: {e}")
            if requires_chrome and sess:
                try:
                    creds, _ = oauth_first_login_in_session(email, sess, log_fn, also_open_gmail_ui=True)
                except Exception as e2:
                    log_fn(f"[OAUTH][FATAL] {e2}")
                    close_chrome_session(sess, log_fn)
                    return
            else:
                return

        try:
            gmail_service = build_gmail_service(creds)
        except Exception as e:
            log_fn(f"[GMAIL][ERROR] service build: {e}")
            if sess:
                close_chrome_session(sess, log_fn)
            return

        try:
            people_service = build_people_service(creds)
        except Exception:
            people_service = None

        keep_open = False
        for p in plugins:
            try:
                raw_search = getattr(self, "shared_search_term", "").strip()
                search_terms = [s.strip() for s in raw_search.split(",") if s.strip()]
                
                ctx = {
                    "email": email,
                    "log": log_fn,
                    "session": sess,
                    "service": gmail_service,
                    "people_service": people_service,
                    "ui": self.plugin_ui.get(p, {}),
                    "app": self,
                    "raw_search": raw_search,
                    "search_terms": search_terms,  # ✅ add parsed search terms list
                }
                p.run(ctx)
                keep_open = keep_open or getattr(p, "keep_open_after_run", False)
            except Exception as e:
                log_fn(f"[ERROR] Plugin {p.name} failed: {e}")

        if sess:
            if keep_open:
                log_fn(f"[SESSION] kept open for {email} (requested by plugin)")
            else:
                close_chrome_session(sess, log_fn)
                log_fn(f"[SESSION] closed for {email} (UI not required)")
        else:
            log_fn(f"[SESSION] no Chrome session to close for {email}")

        log_fn(f"--- Done: {email} ---\n")


if __name__ == "__main__":
    try:
        app = GmailHybridApp()
        app.mainloop()
    except Exception as e:
        print("Fatal error:", e)
        sys.exit(1)

