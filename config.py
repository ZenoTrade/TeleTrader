"""
Centralised runtime configuration (pydantic v2).
Reads environment variables or .env automatically.
"""

from pydantic import Field, BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List


class MT5Account(BaseModel):
    """Legacy model — kept for backward compatibility with accounts.json workflows."""
    account: int
    password: str
    server: str
    path: str


class Settings(BaseSettings):
    telegram_token: str      = Field(..., alias="TELEGRAM_TOKEN")
    signal_chat_id: int      = Field(..., alias="SIGNAL_CHAT_ID")

    # User accounts are now managed via SQLite DB (see admin_panel.py).
    # This list is kept for backward-compat but is no longer auto-loaded.
    mt_accounts: List[MT5Account] = []

    risk_per_trade: float    = Field(0.01, ge=0, le=1)
    max_slippage: int        = Field(20)
    magic_number: int        = Field(32001)
    db_path: str             = Field("users.db")

    model_config = SettingsConfigDict(
        env_file = ".env",
        extra    = "ignore"
    )


settings = Settings()
