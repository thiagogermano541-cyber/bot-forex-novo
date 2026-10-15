from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+asyncpg://jarvis:jarvis@localhost:5432/jarvis"
    redis_url: str = "redis://localhost:6379/0"
    app_origin: str = "http://localhost:3000"
    jwt_secret: SecretStr = SecretStr("")
    admin_email: str = "admin@jarvis.local"
    admin_password: SecretStr = SecretStr("")
    cookie_secure: bool = False
    anthropic_api_key: SecretStr = SecretStr("")
    claude_enabled: bool = True
    claude_pause_reason: str = ""
    claude_model: str = ""
    minimum_setup_rr: float = Field(default=1.5, ge=1, le=5, allow_inf_nan=False)
    claude_max_analysis_attempts: int = Field(default=1, ge=1, le=2)
    claude_max_tokens: int = Field(default=3000, ge=1000, le=16000)
    claude_input_token_limit: int = Field(default=18000, ge=1000, le=100000)
    claude_input_usd_per_million: float = Field(default=0, ge=0, allow_inf_nan=False)
    claude_output_usd_per_million: float = Field(default=0, ge=0, allow_inf_nan=False)
    daily_ai_budget_usd: float = Field(default=5, ge=0, allow_inf_nan=False)
    daily_ai_budget_enabled: bool = False
    analysis_on_demand_only: bool = True
    analysis_interval_seconds: int = Field(default=300, ge=60)
    stale_after_seconds: int = Field(default=90, ge=10, le=600)
    market_refresh_seconds: int = Field(default=30, ge=15, le=120)
    verify_btc_on_start: bool = False
    verify_manual_repair_id: str = ""
    verify_cached_response_at: str = ""
    kraken_enabled: bool = True
    oanda_token: SecretStr = SecretStr("")
    oanda_account_id: str = ""
    oanda_environment: str = "practice"
    fred_api_key: SecretStr = SecretStr("")
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_chat_ids: str = ""
    telegram_enabled: bool = False
    evaluation_horizon_minutes: int = Field(default=60, ge=1, le=1440)

    @field_validator("database_url", mode="before")
    @classmethod
    def async_database_driver(cls, value):
        # Railway provides a standard PostgreSQL URL; SQLAlchemy needs asyncpg.
        if isinstance(value, str):
            for prefix in ("postgres://", "postgresql://"):
                if value.startswith(prefix):
                    return "postgresql+asyncpg://" + value[len(prefix):]
        return value

    @property
    def claude_ready(self):
        return bool(self.claude_enabled and self.anthropic_api_key.get_secret_value() and self.claude_model
                    and self.claude_input_usd_per_million > 0 and self.claude_output_usd_per_million > 0)

    @property
    def enforce_daily_budget(self):
        # Automatic operation always requires a daily spending limit.
        return self.daily_ai_budget_enabled or not self.analysis_on_demand_only

    @property
    def oanda_ready(self):
        return bool(self.oanda_token.get_secret_value() and self.oanda_account_id)


@lru_cache
def settings():
    return Settings()
