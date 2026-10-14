from functools import lru_cache

from pydantic import Field, SecretStr
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
    claude_model: str = ""
    claude_max_tokens: int = Field(default=3000, ge=1000, le=16000)
    claude_input_token_limit: int = Field(default=26000, ge=1000, le=100000)
    claude_input_usd_per_million: float = Field(default=0, ge=0, allow_inf_nan=False)
    claude_output_usd_per_million: float = Field(default=0, ge=0, allow_inf_nan=False)
    daily_ai_budget_usd: float = Field(default=5, ge=0, allow_inf_nan=False)
    analysis_interval_seconds: int = Field(default=300, ge=60)
    stale_after_seconds: int = Field(default=90, ge=10, le=600)
    kraken_enabled: bool = True
    oanda_token: SecretStr = SecretStr("")
    oanda_account_id: str = ""
    oanda_environment: str = "practice"
    fred_api_key: SecretStr = SecretStr("")
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_chat_ids: str = ""
    telegram_enabled: bool = False
    evaluation_horizon_minutes: int = Field(default=60, ge=1, le=1440)

    @property
    def claude_ready(self):
        return bool(self.anthropic_api_key.get_secret_value() and self.claude_model
                    and self.claude_input_usd_per_million > 0 and self.claude_output_usd_per_million > 0)

    @property
    def oanda_ready(self):
        return bool(self.oanda_token.get_secret_value() and self.oanda_account_id)


@lru_cache
def settings():
    return Settings()
