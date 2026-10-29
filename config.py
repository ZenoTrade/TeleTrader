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

    # Zeno licensing (customer builds). See tradebot/infrastructure/license_guard.py
    zeno_license_key: str    = Field("", alias="ZENO_LICENSE_KEY")
    zeno_license_server: str = Field("https://api.zenotrade.example", alias="ZENO_LICENSE_SERVER")
    zeno_license_public_key: str = Field("", alias="ZENO_LICENSE_PUBLIC_KEY")
    # "telegram": read SIGNAL_CHAT_ID; "zeno": poll the Zeno signals feed.
    signal_source: str       = Field("telegram", alias="SIGNAL_SOURCE")
    # Signals without an "@... Trader:Name" tag are skipped unless this is off.
    require_trader_tag: bool = Field(True, alias="REQUIRE_TRADER_TAG")

    # Optional LLM fallback for channels that don't use the Zeno format.
    llm_api_key: str         = Field("", alias="LLM_API_KEY")
    llm_model: str           = Field("gpt-4o-mini", alias="LLM_MODEL")
    llm_base_url: str        = Field("https://api.openai.com/v1", alias="LLM_BASE_URL")
    llm_min_confidence: float = Field(0.7, ge=0, le=1, alias="LLM_MIN_CONFIDENCE")

    model_config = SettingsConfigDict(
        env_file = ".env",
        extra    = "ignore",
        populate_by_name = True
    )


settings = Settings()
