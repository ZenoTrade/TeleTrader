"""
TeleTrader Admin Panel — native tkinter desktop application.

Run:
    python admin_panel.py
"""

import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

sys.path.insert(0, os.path.dirname(__file__))

from tradebot.infrastructure.db import (
    init_db, add_user, update_user, delete_user,
    list_users, get_user, set_master, UserAccount,
    list_traders, set_trader_enabled, delete_trader, upsert_trader, Trader,
)


# ---------------------------------------------------------------------------
#  Form dialog for Add / Edit
# ---------------------------------------------------------------------------
class UserFormDialog(tk.Toplevel):
    """Modal dialog for adding or editing a user."""

    def __init__(self, parent: tk.Tk, title: str = "Add User",
                 user: UserAccount | None = None):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.grab_set()
        self.result: UserAccount | None = None

        pad = dict(padx=10, pady=5)
        row = 0

        # --- Personal info ------------------------------------------------
        ttk.Label(self, text="Personal Information",
                  font=("Segoe UI", 10, "bold")).grid(
            row=row, column=0, columnspan=3, sticky="w", padx=10, pady=(10, 2))
        row += 1

        ttk.Label(self, text="First Name *").grid(row=row, column=0, sticky="e", **pad)
        self.first_name = ttk.Entry(self, width=32)
        self.first_name.grid(row=row, column=1, columnspan=2, sticky="w", **pad)
        row += 1

        ttk.Label(self, text="Last Name *").grid(row=row, column=0, sticky="e", **pad)
        self.last_name = ttk.Entry(self, width=32)
        self.last_name.grid(row=row, column=1, columnspan=2, sticky="w", **pad)
        row += 1

        ttk.Label(self, text="Phone").grid(row=row, column=0, sticky="e", **pad)
        self.phone = ttk.Entry(self, width=32)
        self.phone.grid(row=row, column=1, columnspan=2, sticky="w", **pad)
        row += 1

        ttk.Separator(self, orient="horizontal").grid(
            row=row, column=0, columnspan=3, sticky="ew", pady=8)
        row += 1

        # --- MT5 credentials ---------------------------------------------
        ttk.Label(self, text="MT5 Account Details",
                  font=("Segoe UI", 10, "bold")).grid(
            row=row, column=0, columnspan=3, sticky="w", padx=10, pady=(4, 2))
        row += 1

        ttk.Label(self, text="MT5 Account *").grid(row=row, column=0, sticky="e", **pad)
        self.mt5_account = ttk.Entry(self, width=32)
        self.mt5_account.grid(row=row, column=1, columnspan=2, sticky="w", **pad)
        row += 1

        ttk.Label(self, text="MT5 Password *").grid(row=row, column=0, sticky="e", **pad)
        self.mt5_password = ttk.Entry(self, width=32, show="*")
        self.mt5_password.grid(row=row, column=1, sticky="w", **pad)
        self._show_pw = tk.BooleanVar()
        ttk.Checkbutton(self, text="Show", variable=self._show_pw,
                        command=self._toggle_pw).grid(row=row, column=2, sticky="w")
        row += 1

        ttk.Label(self, text="MT5 Server *").grid(row=row, column=0, sticky="e", **pad)
        self.mt5_server = ttk.Entry(self, width=32)
        self.mt5_server.grid(row=row, column=1, columnspan=2, sticky="w", **pad)
        row += 1

        ttk.Label(self, text="MT5 Path *").grid(row=row, column=0, sticky="e", **pad)
        self.mt5_path = ttk.Entry(self, width=32)
        self.mt5_path.grid(row=row, column=1, sticky="w", **pad)
        ttk.Button(self, text="Browse…",
                   command=self._browse_path).grid(row=row, column=2, sticky="w")
        row += 1

        ttk.Separator(self, orient="horizontal").grid(
            row=row, column=0, columnspan=3, sticky="ew", pady=8)
        row += 1

        # --- Risk settings ------------------------------------------------
        ttk.Label(self, text="Risk Settings",
                  font=("Segoe UI", 10, "bold")).grid(
            row=row, column=0, columnspan=3, sticky="w", padx=10, pady=(4, 2))
        row += 1

        ttk.Label(self, text="Risk Mode *").grid(row=row, column=0, sticky="e", **pad)
        self.risk_mode_var = tk.StringVar(value="risk_pct")
        self.risk_mode = ttk.Combobox(
            self, textvariable=self.risk_mode_var, width=16,
            values=["Risk %", "Fixed Lot"], state="readonly")
        self.risk_mode.grid(row=row, column=1, sticky="w", **pad)
        self.risk_mode.bind("<<ComboboxSelected>>", lambda _: self._on_mode_change())
        row += 1

        self._risk_row = row
        self._risk_label = ttk.Label(self, text="Risk Per Trade (%)")
        self._risk_label.grid(row=row, column=0, sticky="e", **pad)
        self.risk = ttk.Entry(self, width=12)
        self.risk.grid(row=row, column=1, sticky="w", **pad)
        row += 1

        self._lot_row = row
        self._lot_label = ttk.Label(self, text="Fixed Lot Size")
        self._lot_label.grid(row=row, column=0, sticky="e", **pad)
        self.fixed_lot = ttk.Entry(self, width=12)
        self.fixed_lot.grid(row=row, column=1, sticky="w", **pad)
        row += 1

        ttk.Label(self, text="Max Slippage").grid(row=row, column=0, sticky="e", **pad)
        self.slippage = ttk.Entry(self, width=12)
        self.slippage.grid(row=row, column=1, sticky="w", **pad)
        row += 1

        ttk.Label(self, text="Pending timeout (min)").grid(
            row=row, column=0, sticky="e", **pad)
        self.pending_expire = ttk.Entry(self, width=12)
        self.pending_expire.grid(row=row, column=1, sticky="w", **pad)
        ttk.Label(self, text="0 = keep until filled").grid(
            row=row, column=2, sticky="w", **pad)
        row += 1

        self.enabled_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(self, text="Enabled",
                        variable=self.enabled_var).grid(
            row=row, column=1, sticky="w", **pad)
        row += 1

        ttk.Separator(self, orient="horizontal").grid(
            row=row, column=0, columnspan=3, sticky="ew", pady=8)
        row += 1

        # --- Trade management --------------------------------------------
        ttk.Label(self, text="Trade Management  (applied on Master; "
                             "followers inherit via copy-trade)",
                  font=("Segoe UI", 10, "bold")).grid(
            row=row, column=0, columnspan=3, sticky="w", padx=10, pady=(4, 2))
        row += 1

        # Break-even
        self.be_enabled_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(self, text="Auto break-even before TP1",
                        variable=self.be_enabled_var).grid(
            row=row, column=0, columnspan=3, sticky="w", padx=14, pady=(2, 2))
        row += 1

        ttk.Label(self, text="Trigger at (% of dist. to SL)").grid(
            row=row, column=0, sticky="e", **pad)
        self.be_trigger_pct = ttk.Entry(self, width=8)
        self.be_trigger_pct.grid(row=row, column=1, sticky="w", **pad)
        ttk.Label(self, text="e.g. 50 = halfway").grid(
            row=row, column=2, sticky="w", **pad)
        row += 1

        ttk.Label(self, text="Cushion past entry (pts)").grid(
            row=row, column=0, sticky="e", **pad)
        self.be_cushion = ttk.Entry(self, width=8)
        self.be_cushion.grid(row=row, column=1, sticky="w", **pad)
        ttk.Label(self, text="covers spread").grid(
            row=row, column=2, sticky="w", **pad)
        row += 1

        # Trailing
        self.trail_enabled_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(self, text="Trailing stop after TP1",
                        variable=self.trail_enabled_var).grid(
            row=row, column=0, columnspan=3, sticky="w", padx=14, pady=(6, 2))
        row += 1

        ttk.Label(self, text="Tightness").grid(row=row, column=0, sticky="e", **pad)
        self.trail_mode_var = tk.StringVar(value="Tight")
        self.trail_mode = ttk.Combobox(
            self, textvariable=self.trail_mode_var, width=12,
            values=["Tight", "Medium", "Loose"], state="readonly")
        self.trail_mode.grid(row=row, column=1, sticky="w", **pad)
        ttk.Label(self, text="0.5× / 1.0× / 1.5× ATR(M5,14)").grid(
            row=row, column=2, sticky="w", **pad)
        row += 1

        ttk.Label(self, text="Min SL step (pts)").grid(
            row=row, column=0, sticky="e", **pad)
        self.trail_min_step = ttk.Entry(self, width=8)
        self.trail_min_step.grid(row=row, column=1, sticky="w", **pad)
        ttk.Label(self, text="avoids tiny modifies").grid(
            row=row, column=2, sticky="w", **pad)
        row += 1

        # --- Buttons ------------------------------------------------------
        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=row, column=0, columnspan=3, pady=14)
        ttk.Button(btn_frame, text="Save", command=self._save).pack(
            side="left", padx=10)
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(
            side="left", padx=10)

        # Pre-fill when editing
        self._editing_id: int | None = None
        self._preserve_master = bool(user.is_master) if user else False
        if user:
            self._editing_id = user.id
            self.first_name.insert(0, user.first_name)
            self.last_name.insert(0, user.last_name)
            self.phone.insert(0, user.phone)
            self.mt5_account.insert(0, str(user.mt5_account))
            self.mt5_password.insert(0, user.mt5_password)
            self.mt5_server.insert(0, user.mt5_server)
            self.mt5_path.insert(0, user.mt5_path)
            self.risk.insert(0, f"{user.risk_per_trade * 100:.2f}")
            self.fixed_lot.insert(0, f"{user.fixed_lot:.2f}")
            self.slippage.insert(0, str(user.max_slippage))
            self.pending_expire.insert(
                0, str(int(getattr(user, "pending_expire_minutes", 0) or 0)))
            self.enabled_var.set(user.enabled)
            if user.risk_mode == "fixed_lot":
                self.risk_mode_var.set("Fixed Lot")
            else:
                self.risk_mode_var.set("Risk %")
            self.be_enabled_var.set(bool(getattr(user, "be_enabled", False)))
            self.be_trigger_pct.insert(
                0, f"{float(getattr(user, 'be_trigger_pct', 50.0)):.1f}")
            self.be_cushion.insert(
                0, str(int(getattr(user, "be_cushion_points", 5) or 5)))
            self.trail_enabled_var.set(
                bool(getattr(user, "trail_enabled", False)))
            self.trail_mode_var.set(
                (getattr(user, "trail_mode", "tight") or "tight").capitalize())
            self.trail_min_step.insert(
                0, str(int(getattr(user, "trail_min_step_points", 5) or 5)))
        else:
            self.risk.insert(0, "1.00")
            self.fixed_lot.insert(0, "0.01")
            self.slippage.insert(0, "20")
            self.pending_expire.insert(0, "30")
            self.mt5_path.insert(0, "C:/Program Files/MetaTrader 5/terminal64.exe")
            self.be_trigger_pct.insert(0, "50.0")
            self.be_cushion.insert(0, "5")
            self.trail_min_step.insert(0, "5")
        self._on_mode_change()

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.wait_visibility()
        self.focus_set()
        self.first_name.focus()

    # --- helpers ----------------------------------------------------------
    def _on_mode_change(self):
        """Show/hide risk vs fixed-lot fields based on selected mode."""
        is_fixed = self.risk_mode_var.get() == "Fixed Lot"
        if is_fixed:
            self._risk_label.grid_remove()
            self.risk.grid_remove()
            self._lot_label.grid()
            self.fixed_lot.grid()
        else:
            self._lot_label.grid_remove()
            self.fixed_lot.grid_remove()
            self._risk_label.grid()
            self.risk.grid()

    def _toggle_pw(self):
        self.mt5_password.configure(show="" if self._show_pw.get() else "*")

    def _browse_path(self):
        path = filedialog.askopenfilename(
            title="Select MT5 terminal64.exe",
            filetypes=[("Executable", "*.exe"), ("All files", "*.*")],
        )
        if path:
            self.mt5_path.delete(0, "end")
            self.mt5_path.insert(0, path)

    def _save(self):
        fn = self.first_name.get().strip()
        ln = self.last_name.get().strip()
        acct = self.mt5_account.get().strip()
        pw = self.mt5_password.get().strip()
        srv = self.mt5_server.get().strip()
        pth = self.mt5_path.get().strip()

        if not all([fn, ln, acct, pw, srv, pth]):
            messagebox.showerror("Validation",
                                 "All fields marked * are required.",
                                 parent=self)
            return
        try:
            acct_int = int(acct)
        except ValueError:
            messagebox.showerror("Validation",
                                 "MT5 Account must be a number.",
                                 parent=self)
            return

        is_fixed = self.risk_mode_var.get() == "Fixed Lot"
        mode = "fixed_lot" if is_fixed else "risk_pct"

        try:
            risk_val = float(self.risk.get().strip() or "1.0") / 100.0
        except ValueError:
            messagebox.showerror("Validation",
                                 "Risk must be a number.",
                                 parent=self)
            return
        try:
            lot_val = float(self.fixed_lot.get().strip() or "0.01")
            if lot_val <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Validation",
                                 "Fixed Lot must be a positive number.",
                                 parent=self)
            return
        try:
            slip_val = int(self.slippage.get().strip() or "20")
        except ValueError:
            messagebox.showerror("Validation",
                                 "Slippage must be an integer.",
                                 parent=self)
            return
        try:
            pend_val = int(self.pending_expire.get().strip() or "0")
            if pend_val < 0 or pend_val > 10080:
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "Validation",
                "Pending timeout must be an integer from 0 to 10080 (0 = disabled).",
                parent=self,
            )
            return

        try:
            be_trig = float(self.be_trigger_pct.get().strip() or "50")
            if not (0.0 <= be_trig <= 100.0):
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "Validation",
                "BE trigger must be a number between 0 and 100 (percent).",
                parent=self,
            )
            return
        try:
            be_cush = int(self.be_cushion.get().strip() or "5")
            if be_cush < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "Validation",
                "BE cushion must be a non-negative integer (points).",
                parent=self,
            )
            return
        try:
            trail_step = int(self.trail_min_step.get().strip() or "5")
            if trail_step < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "Validation",
                "Trail min step must be a non-negative integer (points).",
                parent=self,
            )
            return
        trail_mode_db = self.trail_mode_var.get().strip().lower() or "tight"
        if trail_mode_db not in ("tight", "medium", "loose"):
            trail_mode_db = "tight"

        self.result = UserAccount(
            id=self._editing_id,
            first_name=fn,
            last_name=ln,
            phone=self.phone.get().strip(),
            mt5_account=acct_int,
            mt5_password=pw,
            mt5_server=srv,
            mt5_path=pth,
            risk_mode=mode,
            risk_per_trade=risk_val,
            fixed_lot=lot_val,
            max_slippage=slip_val,
            pending_expire_minutes=pend_val,
            be_enabled=bool(self.be_enabled_var.get()),
            be_trigger_pct=be_trig,
            be_cushion_points=be_cush,
            trail_enabled=bool(self.trail_enabled_var.get()),
            trail_mode=trail_mode_db,
            trail_min_step_points=trail_step,
            enabled=self.enabled_var.get(),
            is_master=self._preserve_master,
        )
        self.destroy()


# ---------------------------------------------------------------------------
#  Main admin panel window
# ---------------------------------------------------------------------------
class AdminPanel:
    COLUMNS = (
        "id", "name", "phone", "mt5_account",
        "server", "risk_mode", "risk_value", "slippage", "pend_max",
        "role", "status",
    )
    COL_HEADINGS = {
        "id": "ID",
        "name": "Name",
        "phone": "Phone",
        "mt5_account": "MT5 Account",
        "server": "Server",
        "risk_mode": "Mode",
        "risk_value": "Risk / Lot",
        "slippage": "Slip",
        "pend_max": "Pend min",
        "role": "Role",
        "status": "Status",
    }
    COL_WIDTHS = {
        "id": 40,
        "name": 150,
        "phone": 100,
        "mt5_account": 100,
        "server": 130,
        "risk_mode": 70,
        "risk_value": 75,
        "slippage": 50,
        "pend_max": 62,
        "role": 100,
        "status": 100,
    }

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("TeleTrader — Admin Panel")
        self.root.geometry("1060x560")
        self.root.minsize(820, 400)

        style = ttk.Style()
        style.theme_use("clam")

        init_db()

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=6, pady=(6, 0))

        # --- Users tab ---
        users_frame = ttk.Frame(self.notebook)
        self.notebook.add(users_frame, text="  Users  ")
        self._build_users_tab(users_frame)

        # --- Traders tab ---
        traders_frame = ttk.Frame(self.notebook)
        self.notebook.add(traders_frame, text="  Traders  ")
        self._build_traders_tab(traders_frame)

        self._build_statusbar()

        self.refresh()
        self.refresh_traders()

    # === USERS TAB ========================================================

    def _build_users_tab(self, parent):
        bar = ttk.Frame(parent, padding=(8, 6))
        bar.pack(fill="x")
        for text, cmd in [
            ("Add User",        self.on_add),
            ("Edit",            self.on_edit),
            ("Delete",          self.on_delete),
            ("Toggle Enable",   self.on_toggle),
            ("Set as Master",   self.on_set_master),
            ("Test Connection", self.on_test),
            ("Test All",        self.on_test_all),
            ("Refresh",         self.refresh),
        ]:
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=4)

        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, padx=8, pady=(0, 4))

        self.tree = ttk.Treeview(
            frame, columns=self.COLUMNS, show="headings",
            selectmode="browse",
        )
        for col in self.COLUMNS:
            self.tree.heading(col, text=self.COL_HEADINGS[col])
            anchor = "center" if col in (
                "id", "slippage", "pend_max", "role", "status") else "w"
            self.tree.column(col, width=self.COL_WIDTHS[col], minwidth=40,
                             anchor=anchor)

        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", lambda _: self.on_edit())

    def _build_statusbar(self):
        self._status = tk.StringVar(value="Ready")
        ttk.Label(
            self.root, textvariable=self._status, relief="sunken",
            padding=(6, 3),
        ).pack(fill="x", padx=8, pady=(0, 6))

    def _set_status(self, msg: str):
        self._status.set(msg)
        self.root.update_idletasks()

    # --- helpers ----------------------------------------------------------
    def _selected_id(self) -> int | None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Select", "Please select a user first.")
            return None
        return int(sel[0])

    # --- actions ----------------------------------------------------------
    def refresh(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        users = list_users()
        for u in users:
            role = "\U0001F451 Master" if u.is_master else "\U0001F464 Follower"
            status = "\u2705 Enabled" if u.enabled else "\u26D4 Disabled"
            if u.risk_mode == "fixed_lot":
                mode_label = "Fixed"
                risk_display = f"{u.fixed_lot:.2f}"
            else:
                mode_label = "Risk%"
                risk_display = f"{u.risk_per_trade * 100:.1f}%"
            pm = int(getattr(u, "pending_expire_minutes", 0) or 0)
            pend_disp = "—" if pm == 0 else str(pm)
            self.tree.insert("", "end", iid=str(u.id), values=(
                u.id,
                u.full_name,
                u.phone,
                u.mt5_account,
                u.mt5_server,
                mode_label,
                risk_display,
                u.max_slippage,
                pend_disp,
                role,
                status,
            ))
        self._set_status(f"{len(users)} user(s) loaded")

    def on_add(self):
        dlg = UserFormDialog(self.root, title="Add User")
        self.root.wait_window(dlg)
        if dlg.result is None:
            return
        try:
            add_user(dlg.result)
            self.refresh()
            self._set_status(f"User {dlg.result.full_name} added")
        except Exception as exc:
            messagebox.showerror("Error", str(exc))

    def on_edit(self):
        uid = self._selected_id()
        if uid is None:
            return
        user = get_user(uid)
        if not user:
            return
        dlg = UserFormDialog(self.root, title="Edit User", user=user)
        self.root.wait_window(dlg)
        if dlg.result is None:
            return
        try:
            update_user(dlg.result)
            self.refresh()
            self._set_status(f"User {dlg.result.full_name} updated")
        except Exception as exc:
            messagebox.showerror("Error", str(exc))

    def on_delete(self):
        uid = self._selected_id()
        if uid is None:
            return
        user = get_user(uid)
        if not user:
            return
        if not messagebox.askyesno(
            "Confirm Delete",
            f"Delete user {user.full_name} (MT5 {user.mt5_account})?",
        ):
            return
        delete_user(uid)
        self.refresh()
        self._set_status(f"User {user.full_name} deleted")

    def on_toggle(self):
        uid = self._selected_id()
        if uid is None:
            return
        user = get_user(uid)
        if not user:
            return
        user.enabled = not user.enabled
        update_user(user)
        self.refresh()
        state = "enabled" if user.enabled else "disabled"
        self._set_status(f"User {user.full_name} {state}")

    def on_set_master(self):
        uid = self._selected_id()
        if uid is None:
            return
        user = get_user(uid)
        if not user:
            return
        if not messagebox.askyesno(
            "Set Master",
            f"Set {user.full_name} (MT5 {user.mt5_account}) as the master account?\n"
            "All other users will become followers.",
        ):
            return
        set_master(uid)
        self.refresh()
        self._set_status(f"{user.full_name} is now the master account")

    def on_test(self):
        uid = self._selected_id()
        if uid is None:
            return
        user = get_user(uid)
        if not user:
            return
        self._set_status(f"Testing connection for {user.full_name}…")

        def _test():
            try:
                import MetaTrader5 as mt5
                from tradebot.infrastructure._mt5_utils import ensure_mt5

                ensure_mt5(user.mt5_path)
                ok = mt5.login(user.mt5_account, user.mt5_password,
                               user.mt5_server)
                if ok:
                    info = mt5.account_info()
                    msg = (f"Connected: {info.login} @ {info.server}\n"
                           f"Balance: {info.balance}")
                    self.root.after(0, lambda: messagebox.showinfo(
                        "Success", msg))
                    self.root.after(0, lambda: self._set_status(
                        f"Connection OK — {user.full_name}"))
                else:
                    err = mt5.last_error()
                    self.root.after(0, lambda: messagebox.showerror(
                        "Failed", f"Login failed: {err}"))
                    self.root.after(0, lambda: self._set_status(
                        f"Connection FAILED — {user.full_name}"))
            except Exception as exc:
                self.root.after(0, lambda: messagebox.showerror(
                    "Error", str(exc)))
                self.root.after(0, lambda: self._set_status(
                    "Connection test error"))

        threading.Thread(target=_test, daemon=True).start()

    def on_test_all(self):
        users = list_users()
        if not users:
            messagebox.showinfo("Test All", "No users in database.")
            return

        self._set_status("Testing all connections…")

        # Set all rows to "Checking…" immediately
        for u in users:
            iid = str(u.id)
            if self.tree.exists(iid):
                vals = list(self.tree.item(iid, "values"))
                vals[-1] = "Checking…"
                self.tree.item(iid, values=vals)
        self.root.update_idletasks()

        def _test_all():
            import MetaTrader5 as mt5
            from tradebot.infrastructure._mt5_utils import ensure_mt5

            ok_count = 0
            fail_count = 0
            failed_names: list[str] = []

            for u in users:
                iid = str(u.id)
                try:
                    ensure_mt5(u.mt5_path)
                    ok = mt5.login(u.mt5_account, u.mt5_password,
                                   u.mt5_server)
                    if ok:
                        ok_count += 1
                        result_text = "OK"
                    else:
                        fail_count += 1
                        failed_names.append(
                            f"{u.full_name} ({u.mt5_account})")
                        result_text = "FAIL"
                except Exception:
                    fail_count += 1
                    failed_names.append(
                        f"{u.full_name} ({u.mt5_account})")
                    result_text = "FAIL"

                def _update(iid=iid, txt=result_text):
                    if self.tree.exists(iid):
                        vals = list(self.tree.item(iid, "values"))
                        vals[-1] = txt
                        self.tree.item(iid, values=vals)
                self.root.after(0, _update)

            def _summary():
                summary = f"{ok_count} OK, {fail_count} FAILED"
                self._set_status(summary)
                if failed_names:
                    detail = "Failed accounts:\n\n" + "\n".join(failed_names)
                    messagebox.showwarning("Test All Results",
                                           f"{summary}\n\n{detail}")
                else:
                    messagebox.showinfo("Test All Results",
                                        f"All {ok_count} accounts connected successfully.")
            self.root.after(0, _summary)

        threading.Thread(target=_test_all, daemon=True).start()

    # === TRADERS TAB ======================================================

    TRADER_COLUMNS = ("id", "name", "status", "first_seen", "last_seen")
    TRADER_HEADINGS = {
        "id": "ID", "name": "Trader Name", "status": "Status",
        "first_seen": "First Seen", "last_seen": "Last Seen",
    }
    TRADER_WIDTHS = {
        "id": 40, "name": 200, "status": 80,
        "first_seen": 180, "last_seen": 180,
    }

    def _build_traders_tab(self, parent):
        bar = ttk.Frame(parent, padding=(8, 6))
        bar.pack(fill="x")
        for text, cmd in [
            ("Scan Channel", self.on_scan_channel),
            ("Enable",       self.on_trader_enable),
            ("Disable",      self.on_trader_disable),
            ("Delete",       self.on_trader_delete),
            ("Refresh",      self.refresh_traders),
        ]:
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=4)

        ttk.Label(bar, text="  (Scan channel or run bot to discover traders)",
                  foreground="gray").pack(side="left", padx=12)

        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, padx=8, pady=(0, 4))

        self.trader_tree = ttk.Treeview(
            frame, columns=self.TRADER_COLUMNS, show="headings",
            selectmode="extended",
        )
        for col in self.TRADER_COLUMNS:
            self.trader_tree.heading(col, text=self.TRADER_HEADINGS[col])
            anchor = "center" if col in ("id", "status") else "w"
            self.trader_tree.column(col, width=self.TRADER_WIDTHS[col],
                                    minwidth=40, anchor=anchor)

        vsb = ttk.Scrollbar(frame, orient="vertical",
                             command=self.trader_tree.yview)
        self.trader_tree.configure(yscrollcommand=vsb.set)
        self.trader_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.trader_tree.tag_configure("enabled",
                                        background="#d4edda", foreground="#155724")
        self.trader_tree.tag_configure("disabled",
                                        background="#f8d7da", foreground="#721c24")

    def on_scan_channel(self):
        """Scan the Telegram channel for trader names from pending messages."""
        self._set_status("Scanning Telegram channel…")

        def _scan():
            import re
            import asyncio
            from telegram import Bot
            from config import settings

            _TRADER_RX = re.compile(r"Trader:\s*(.+)", re.I)

            async def _fetch():
                bot = Bot(token=settings.telegram_token)
                async with bot:
                    updates = await bot.get_updates(
                        limit=100, timeout=0,
                        allowed_updates=["channel_post", "message"],
                    )
                    return updates

            try:
                updates = asyncio.run(_fetch())
            except Exception as exc:
                self.root.after(0, lambda: messagebox.showerror(
                    "Scan Error", str(exc)))
                self.root.after(0, lambda: self._set_status("Scan failed"))
                return

            found: set[str] = set()
            for upd in updates:
                msg = upd.effective_message
                if not msg or not msg.text:
                    continue
                for line in msg.text.splitlines():
                    m = _TRADER_RX.search(line)
                    if m:
                        name = re.sub(r"[^A-Za-z0-9\+ ]", "",
                                      m.group(1)).strip()
                        if name:
                            found.add(name)

            for name in found:
                upsert_trader(name)

            def _done():
                self.refresh_traders()
                if found:
                    self._set_status(
                        f"Scan complete — found {len(found)} trader(s): "
                        f"{', '.join(sorted(found))}")
                else:
                    self._set_status(
                        "Scan complete — no pending messages with trader names. "
                        "Run the bot to discover traders from live signals.")
            self.root.after(0, _done)

        threading.Thread(target=_scan, daemon=True).start()

    def refresh_traders(self):
        for item in self.trader_tree.get_children():
            self.trader_tree.delete(item)
        traders = list_traders()
        enabled_count = 0
        for t in traders:
            if t.enabled:
                status = "Enabled"
                tag = "enabled"
                enabled_count += 1
            else:
                status = "Disabled"
                tag = "disabled"
            first = (t.first_seen or "")[:19].replace("T", " ")
            last = (t.last_seen or "")[:19].replace("T", " ")
            self.trader_tree.insert("", "end", iid=str(t.id), values=(
                t.id, t.name, status, first, last,
            ), tags=(tag,))
        self._set_status(
            f"{len(traders)} trader(s) — {enabled_count} enabled, "
            f"{len(traders) - enabled_count} disabled")

    def _selected_trader_ids(self) -> list[int]:
        sel = self.trader_tree.selection()
        if not sel:
            messagebox.showinfo("Select", "Please select one or more traders.")
            return []
        return [int(s) for s in sel]

    def on_trader_enable(self):
        ids = self._selected_trader_ids()
        for tid in ids:
            set_trader_enabled(tid, True)
        if ids:
            self.refresh_traders()
            self._set_status(f"Enabled {len(ids)} trader(s)")

    def on_trader_disable(self):
        ids = self._selected_trader_ids()
        for tid in ids:
            set_trader_enabled(tid, False)
        if ids:
            self.refresh_traders()
            self._set_status(f"Disabled {len(ids)} trader(s)")

    def on_trader_delete(self):
        ids = self._selected_trader_ids()
        if not ids:
            return
        if not messagebox.askyesno("Confirm",
                                    f"Delete {len(ids)} trader(s)?"):
            return
        for tid in ids:
            delete_trader(tid)
        self.refresh_traders()
        self._set_status(f"Deleted {len(ids)} trader(s)")

    # --- run --------------------------------------------------------------
    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    AdminPanel().run()
