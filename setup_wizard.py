"""Zeno Copier first-run setup.

Asks for the license key and the customer's own Telegram bot (used for
execution reports), finds the chat id automatically, and writes .env.
MT5 accounts are then added in the admin panel.

Run:
    python setup_wizard.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import httpx

sys.path.insert(0, os.path.dirname(__file__))

from tradebot.infrastructure import license_guard as lg  # noqa: E402

DEFAULT_SERVER = os.environ.get("ZENO_LICENSE_SERVER", "https://api.zenotrade.example")
ENV_PATH = Path(os.path.dirname(os.path.abspath(sys.argv[0]))) / ".env"


# ---------------------------------------------------------------------------
#  Pure helpers (tested)
# ---------------------------------------------------------------------------

def chat_id_from_updates(updates: list[dict]) -> int | None:
    """Return the private chat id of the most recent message sent to the bot."""
    for upd in reversed(updates):
        msg = upd.get("message") or {}
        chat = msg.get("chat") or {}
        if chat.get("type") == "private" and "id" in chat:
            return int(chat["id"])
    return None


def products_in_key(key: str, public_key: str) -> list[str]:
    """Products the key unlocks, or [] if no public key is embedded (owner build)."""
    if not public_key:
        return []
    return list(lg.verify_offline(key, public_key).get("prd", []))


# Common prop-firm rule sets. Customers can fine-tune GUARD_* in .env afterwards.
GUARD_PRESETS: dict[str, dict[str, str] | None] = {
    "Off": None,
    "Two-step challenge (5% daily, 10% max)": {
        "GUARD_DAILY_LOSS_PCT": "5", "GUARD_MAX_DRAWDOWN_PCT": "10",
        "GUARD_TRAILING_DRAWDOWN": "false"},
    "One-step challenge (4% daily, 6% trailing)": {
        "GUARD_DAILY_LOSS_PCT": "4", "GUARD_MAX_DRAWDOWN_PCT": "6",
        "GUARD_TRAILING_DRAWDOWN": "true"},
    "Funded account (5% daily, 10% max)": {
        "GUARD_DAILY_LOSS_PCT": "5", "GUARD_MAX_DRAWDOWN_PCT": "10",
        "GUARD_TRAILING_DRAWDOWN": "false"},
}


def guard_env(preset: str, initial_balance: float) -> dict[str, str]:
    """The GUARD_* lines for a preset; Off writes GUARD_ENABLED=false only."""
    rules = GUARD_PRESETS[preset]
    if rules is None:
        return {"GUARD_ENABLED": "false"}
    if initial_balance <= 0:
        raise ValueError("starting balance must be above zero")
    return {"GUARD_ENABLED": "true", **rules,
            "GUARD_INITIAL_BALANCE": f"{initial_balance:g}"}


def render_env(*, license_key: str, server: str, bot_token: str, chat_id: int,
               signal_source: str, risk_per_trade: float, existing: str = "",
               extra: dict[str, str] | None = None) -> str:
    """Write our keys into an .env, keeping any other lines the customer added."""
    values = {
        "ZENO_LICENSE_KEY": license_key.strip(),
        "ZENO_LICENSE_SERVER": server.rstrip("/"),
        "TELEGRAM_TOKEN": bot_token.strip(),
        "SIGNAL_CHAT_ID": str(chat_id),
        "SIGNAL_SOURCE": signal_source,
        "RISK_PER_TRADE": f"{risk_per_trade:g}",
        **(extra or {}),
    }
    kept = [line for line in existing.splitlines()
            if line.split("=", 1)[0].strip() not in values]
    return "\n".join(kept + [f"{k}={v}" for k, v in values.items()]) + "\n"


# ---------------------------------------------------------------------------
#  UI
# ---------------------------------------------------------------------------

class Wizard(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Zeno Copier setup")
        self.resizable(False, False)
        pad = dict(padx=10, pady=6)
        public_key = lg.EMBEDDED_PUBLIC_KEY or os.environ.get("ZENO_LICENSE_PUBLIC_KEY", "")
        self.public_key = public_key

        ttk.Label(self, text="1. License key (from the Zeno bot: /key)",
                  font=("Segoe UI", 10, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", **pad)
        self.key = ttk.Entry(self, width=70)
        self.key.grid(row=1, column=0, columnspan=2, sticky="we", **pad)

        ttk.Label(self, text="2. Your own Telegram bot for trade reports",
                  font=("Segoe UI", 10, "bold")).grid(row=2, column=0, columnspan=2, sticky="w", **pad)
        ttk.Label(self, wraplength=520, justify="left", text=(
            "Open @BotFather in Telegram, send /newbot, and paste the token here. "
            "Then open your new bot, press Start, and click Find my chat.")).grid(
            row=3, column=0, columnspan=2, sticky="w", **pad)
        self.token = ttk.Entry(self, width=70)
        self.token.grid(row=4, column=0, columnspan=2, sticky="we", **pad)
        ttk.Button(self, text="Find my chat", command=self.find_chat).grid(row=5, column=0, sticky="w", **pad)
        self.chat_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.chat_var).grid(row=5, column=1, sticky="w", **pad)

        ttk.Label(self, text="3. Risk per signal (% of balance)",
                  font=("Segoe UI", 10, "bold")).grid(row=6, column=0, sticky="w", **pad)
        self.risk = ttk.Spinbox(self, from_=0.1, to=5.0, increment=0.1, width=8)
        self.risk.set("1.0")
        self.risk.grid(row=6, column=1, sticky="w", **pad)

        ttk.Label(self, text="4. Zeno Guard (prop-firm rules)",
                  font=("Segoe UI", 10, "bold")).grid(row=7, column=0, sticky="w", **pad)
        self.guard_preset = ttk.Combobox(self, values=list(GUARD_PRESETS), state="readonly", width=42)
        self.guard_preset.set("Off")
        self.guard_preset.grid(row=7, column=1, sticky="w", **pad)
        ttk.Label(self, text="Account starting balance").grid(row=8, column=0, sticky="w", **pad)
        self.guard_balance = ttk.Entry(self, width=14)
        self.guard_balance.grid(row=8, column=1, sticky="w", **pad)
        ttk.Label(self, wraplength=520, justify="left", text=(
            "Guard closes every trade and locks the account for the day before your "
            "firm's daily loss or max drawdown limit is reached, and blocks new trades "
            "whose stop would cross it. Needs a license that includes Guard.")).grid(
            row=9, column=0, columnspan=2, sticky="w", **pad)

        ttk.Label(self, wraplength=520, foreground="#a33", justify="left", text=(
            "Trading is risky. Start on a demo account or with low risk until you have "
            "seen how the Copier behaves.")).grid(row=10, column=0, columnspan=2, sticky="w", **pad)

        ttk.Button(self, text="Save and add my MT5 account", command=self.save).grid(
            row=11, column=0, columnspan=2, sticky="e", **pad)
        self.chat_id: int | None = None

    def find_chat(self) -> None:
        token = self.token.get().strip()
        if not token:
            messagebox.showerror("Zeno", "Paste your bot token first.")
            return
        try:
            data = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15).json()
        except httpx.HTTPError as exc:
            messagebox.showerror("Zeno", f"Could not reach Telegram: {exc}")
            return
        if not data.get("ok"):
            messagebox.showerror("Zeno", "Telegram rejected that token. Check it in @BotFather.")
            return
        chat = chat_id_from_updates(data.get("result", []))
        if chat is None:
            messagebox.showinfo("Zeno", "Open your bot in Telegram, press Start, then try again.")
            return
        self.chat_id = chat
        self.chat_var.set(f"Found chat {chat}")

    def save(self) -> None:
        key = self.key.get().strip()
        try:
            products = products_in_key(key, self.public_key)
        except ValueError:
            messagebox.showerror("Zeno", "That license key is not valid. Copy it again from /key.")
            return
        if self.chat_id is None:
            messagebox.showerror("Zeno", "Click Find my chat first.")
            return
        try:
            risk = float(self.risk.get()) / 100
        except ValueError:
            messagebox.showerror("Zeno", "Risk must be a number like 1.0")
            return
        try:
            balance_text = self.guard_balance.get().replace(",", "").strip()
            guard = guard_env(self.guard_preset.get(), float(balance_text or 0))
        except ValueError:
            messagebox.showerror("Zeno", "Enter the account's starting balance, like 100000.")
            return
        source = "zeno" if "signals" in products else "telegram"
        existing = ENV_PATH.read_text() if ENV_PATH.exists() else ""
        ENV_PATH.write_text(render_env(
            license_key=key, server=DEFAULT_SERVER, bot_token=self.token.get(),
            chat_id=self.chat_id, signal_source=source, risk_per_trade=risk, existing=existing,
            extra=guard))
        note = ("Zeno Signals will be executed automatically."
                if source == "zeno" else
                "Set SIGNAL_CHAT_ID in .env to the channel you want to copy, and add your bot to it.")
        messagebox.showinfo("Zeno", f"Saved. {note}\n\nNext, add your MT5 account.")
        self.destroy()
        _open_admin_panel()


def _open_admin_panel() -> None:
    here = Path(os.path.dirname(os.path.abspath(sys.argv[0])))
    exe = here / "ZenoAdmin.exe"
    if exe.exists():
        subprocess.Popen([str(exe)], cwd=here)
    else:
        subprocess.Popen([sys.executable, str(here / "admin_panel.py")], cwd=here)


if __name__ == "__main__":
    Wizard().mainloop()
