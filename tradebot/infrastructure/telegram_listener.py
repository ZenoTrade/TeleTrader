# tradebot/infrastructure/telegram_listener.py

"""Telegram gateway that turns raw messages into MT5 orders."""
from loguru import logger
from telegram.ext import (
    ApplicationBuilder, ContextTypes, MessageHandler, filters
)
from telegram import Update

from config import settings
from tradebot.domain.ports import (
    SignalParserPort, 
    TradingEnginePort,
    OrderPort,
    NotificationPort,
)
from tradebot.domain.models import Order, OrderResult, Signal
from tradebot.infrastructure.sl_manager import SignalSLManager
from tradebot.infrastructure.copy_syncer import CopyTradeSyncer
from tradebot.infrastructure.pending_expirer import PendingOrderExpirer
from tradebot.infrastructure.db import upsert_trader, is_trader_allowed


class TelegramSignalListener:
    """Listen to a signal channel, parse, generate orders, execute them."""

    def __init__(
            self,
            parser: SignalParserPort,
            engine: TradingEnginePort,
            order_generator: OrderPort,
            notifier: NotificationPort,
            sl_manager: SignalSLManager | None = None,
            copy_syncer: CopyTradeSyncer | None = None,
            pending_expirer: PendingOrderExpirer | None = None,
            startup_message: str = "",
            shutdown_message: str = "",
    ):
        self.parser = parser
        self.engine = engine
        self.generator = order_generator
        self.notifier = notifier
        self.sl_manager = sl_manager
        self.copy_syncer = copy_syncer
        self.pending_expirer = pending_expirer
        self._startup_msg = startup_message
        self._shutdown_msg = shutdown_message

        self.app = (ApplicationBuilder()
                    .token(settings.telegram_token)
                    .build())

        # Restrict to the configured channel only
        allowed_chat = filters.Chat(settings.signal_chat_id)
        self.app.add_handler(MessageHandler(allowed_chat, self._handle))
        self.app.add_error_handler(self._error_handler)

    # ------------------------------------------------------------------
    async def _handle(self, upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
        text = upd.effective_message.text or ""
        logger.debug("incoming text: {}", text.replace("\n", " ")[:100])

        sig: Signal | None = self.parser.parse(text)
        if sig is None:
            logger.info("parser returned None")
            return

        trader_name = sig.comment.strip() if sig.comment else ""

        if trader_name:
            upsert_trader(trader_name)
            if not is_trader_allowed(trader_name):
                logger.info(f"Signal from trader '{trader_name}' — not enabled, skipping")
                return
        else:
            logger.info("Signal has no Trader tag — skipping")
            return

        logger.info(f"Processing signal from trader '{trader_name}': "
                     f"{sig.side.upper()} {sig.symbol}")

        orders: list[Order] = self.generator.generate_orders(sig)
        if not orders:
            await upd.effective_message.reply_text("Signal parsed but generated no orders.")
            return

        responses: list[str] = []
        for order in orders:
            results: list[OrderResult] = self.engine.execute_order(order)
            logger.debug(f"Executing order: {order}")

            for res in results:
                status = "OK" if res.success else f"FAIL ({res.message})"
                acc_id = res.data.get("account_id", "N/A") if res.data else "N/A"
                responses.append(
                    f"Acc {acc_id}: {order.side.upper()} {order.risk*100:.1f}% "
                    f"{order.symbol} -> TP {order.tp} : {status}"
                )

        await upd.effective_message.reply_text("\n".join(responses))

    # ------------------------------------------------------------------
    async def _error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        logger.exception(f"Unhandled exception: {context.error}")
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text("Internal error _ see logs.")
        # Notify admin channel of critical error
        # try:
        #     self.notifier.notify(f"Bot error: {context.error}")
        # except Exception:
        #     logger.error("Failed to send notification on error")
        try:
            await self.notifier.notify(f"Bot error: {context.error}")
        except Exception:
            logger.error("Failed to send notification on error")

    # ------------------------------------------------------------------
    async def _post_init(self, application) -> None:
        """Called by python-telegram-bot after the app is initialized."""
        await application.bot.delete_webhook(drop_pending_updates=True)
        try:
            await application.bot.get_updates(offset=-1, timeout=1)
        except Exception:
            pass
        logger.info("Cleared stale Telegram connection")

        if self._startup_msg:
            try:
                await self.notifier.notify(self._startup_msg)
                logger.info("Startup notification sent")
            except Exception:
                logger.warning("Could not send startup notification")

    async def _post_shutdown(self, application) -> None:
        """Called by python-telegram-bot when the app shuts down."""
        if self._shutdown_msg:
            try:
                await self.notifier.notify(self._shutdown_msg)
                logger.info("Shutdown notification sent")
            except Exception:
                logger.warning("Could not send shutdown notification")

    # ------------------------------------------------------------------
    def run(self):
        logger.info("Starting Telegram polling …")
        self.app.post_init = self._post_init
        self.app.post_shutdown = self._post_shutdown
        if self.sl_manager:
            self.sl_manager.start()
        if self.copy_syncer:
            self.copy_syncer.start()
        if self.pending_expirer:
            self.pending_expirer.start()
        try:
            self.app.run_polling(drop_pending_updates=True)
        finally:
            if self.pending_expirer:
                self.pending_expirer.stop()
            if self.copy_syncer:
                self.copy_syncer.stop()
            if self.sl_manager:
                self.sl_manager.stop()
