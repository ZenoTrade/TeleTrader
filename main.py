"""
Entrypoint.
"""
import os
import sys

import MetaTrader5 as mt5
from loguru import logger


PID_FILE = os.path.join(os.path.dirname(__file__) or ".", ".tt_bot.pid")


def _kill_zombie_bots() -> None:
    """Kill any previous bot instance using the PID lock file."""
    import signal

    if os.path.exists(PID_FILE):
        try:
            old_pid = int(open(PID_FILE).read().strip())
            if old_pid != os.getpid():
                os.kill(old_pid, signal.SIGTERM)
                logger.warning(f"Killed previous bot instance (PID {old_pid})")
                import time
                time.sleep(1)
        except (ValueError, OSError):
            pass

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

from config import settings
from tradebot.application.parser import BasicSignalParser
from tradebot.application.order_generator import SimpleOrderGenerator
from tradebot.application.risk import FiboRiskManager
from tradebot.infrastructure.mt5_engine import MetaTraderEngine
from tradebot.infrastructure.telegram_listener import TelegramSignalListener
from tradebot.infrastructure.telegram_notifier import TelegramNotifier
from tradebot.infrastructure.sl_manager import SignalSLManager
from tradebot.infrastructure.copy_syncer import CopyTradeSyncer
from tradebot.infrastructure.pending_expirer import PendingOrderExpirer
from tradebot.infrastructure.db import init_db, get_enabled_users, get_master_user
from tradebot.infrastructure._mt5_utils import ensure_mt5


def bootstrap():

    logger.add("tradebot.log", rotation="25 MB", level="DEBUG")

    _kill_zombie_bots()

    # Ensure the user database exists
    init_db(settings.db_path)

    parser           = BasicSignalParser()
    engine           = MetaTraderEngine()
    risk_manager     = FiboRiskManager(reverse=True)
    order_generator  = SimpleOrderGenerator(risk_manager=risk_manager)
    notifier         = TelegramNotifier(settings.telegram_token,
                                        settings.signal_chat_id)
    sl_manager       = SignalSLManager(interval_sec=5)
    copy_syncer      = CopyTradeSyncer(interval_sec=5)
    pending_expirer  = PendingOrderExpirer(interval_sec=30)

    # Validate that at least one enabled user exists and can log in
    users = get_enabled_users(settings.db_path)
    if not users:
        logger.error(
            "No enabled users in database. "
            "Run  python admin_panel.py  to add users first.")
        sys.exit(1)

    ready_count = 0
    failed_names: list[str] = []
    for user in users:
        try:
            ensure_mt5(user.mt5_path)
            if not mt5.login(user.mt5_account, user.mt5_password,
                             user.mt5_server):
                logger.warning(
                    f"MT5 login failed for {user.full_name} "
                    f"(account {user.mt5_account}) on {user.mt5_server}: "
                    f"{mt5.last_error()} — skipping")
                failed_names.append(
                    f"{user.full_name} ({user.mt5_account})")
                continue
            info = mt5.account_info()
            if not info:
                logger.warning(
                    f"MT5 account_info() is None for {user.full_name} "
                    f"(account {user.mt5_account}) — skipping")
                failed_names.append(
                    f"{user.full_name} ({user.mt5_account})")
                continue
            role = "MASTER" if user.is_master else "follower"
            logger.info(
                f"MT5 ready — {user.full_name} [{role}] | "
                f"account {info.login} @ {info.server}")
            ready_count += 1
        except Exception as e:
            logger.warning(
                f"MT5 init error for {user.full_name} "
                f"(account {user.mt5_account}): {e} — skipping")
            failed_names.append(
                f"{user.full_name} ({user.mt5_account})")

    if failed_names:
        logger.warning(f"Failed accounts: {', '.join(failed_names)}")
    if ready_count == 0:
        logger.error("No accounts could log in. Fix credentials and retry.")
        sys.exit(2)

    master = get_master_user()
    if not master:
        logger.warning(
            "No master account set. Copy-trade sync will be inactive. "
            "Use admin_panel.py to set a master.")

    # --- Build notification messages ---
    accounts_info = "\n".join(
        f"  {'👑' if u.is_master else '👤'} {u.full_name} — {u.mt5_account}"
        for u in users if u.full_name not in [n.split(" (")[0] for n in failed_names]
    )
    fail_info = ""
    if failed_names:
        fail_info = "\n⚠️ Failed: " + ", ".join(failed_names)

    startup_msg = (
        f"🟢 TT Bot Started\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 Accounts ({ready_count}):\n"
        f"{accounts_info}{fail_info}\n"
        f"🔄 Copy-Trade: {'Active' if master else 'Inactive'}\n"
        f"🛡️ SL Manager: Active\n"
        f"⏱️ Pending expiry: per user (admin) — bot pendings only\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"✅ Listening for signals…"
    )

    shutdown_msg = (
        "🔴 TT Bot Stopped\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "⛔ Trading halted. No signals will be processed.\n"
        "🔄 Restart with: python -m main"
    )

    gateway = TelegramSignalListener(
        parser,
        engine,
        order_generator,
        notifier,
        sl_manager=sl_manager,
        copy_syncer=copy_syncer if master else None,
        pending_expirer=pending_expirer,
        startup_message=startup_msg,
        shutdown_message=shutdown_msg,
    )
    gateway.run()

if __name__ == "__main__":
    bootstrap()
