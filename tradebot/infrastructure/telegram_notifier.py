from telegram import Bot
from tradebot.domain.ports import NotificationPort

class TelegramNotifier(NotificationPort):
    """Send notifications to a Telegram chat."""
    def __init__(self, token: str, chat_id: int):
        self._bot = Bot(token=token)
        self._chat_id = chat_id

    async def notify(self, message: str) -> None:
        await self._bot.send_message(chat_id=self._chat_id, text=message)