import os

# config.Settings requires these at import time; tests never talk to Telegram.
os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("SIGNAL_CHAT_ID", "1")
