import asyncio

from telegram import Bot
from tradebot.domain.ports import NotificationPort

class TelegramNotifier(NotificationPort):
    """Send notifications to a Telegram chat."""
    def __init__(self, token: str, chat_id: int):
        self._bot = Bot(token=token)
        self._chat_id = chat_id

    async def notify(self, message: str) -> None:
        await self._bot.send_message(chat_id=self._chat_id, text=message)

def notify_from_thread(token: str, chat_id: int, message: str) -> None:
    """Send from a worker thread, which has no event loop of its own."""
    try:
        asyncio.run(Bot(token=token).send_message(chat_id=chat_id, text=message))
    except Exception:
        from loguru import logger
        logger.warning("Could not send Telegram notification")
