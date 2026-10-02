from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_MODELS = {
    "gemini": "gemini-3.5-flash",   # free tier available; use gemini-3.1-flash-lite if you hit quota limits
    "anthropic": "claude-opus-5-5",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Agent connects ONLY as the read-only role.
    database_url: str = "postgresql://analyst_ro:analyst_ro_dev_pw@localhost:5433/shop"
    # Admin URL is used solely by the eval harness to run gold SQL and by tests; never by the agent.
    admin_database_url: str = "postgresql://shop_admin:shop_admin_dev_pw@localhost:5433/shop"

    llm_provider: Literal["gemini", "anthropic"] = "gemini"
    llm_model: str = ""               # empty -> DEFAULT_MODELS[llm_provider]
    llm_effort: str = "low"           # low | medium | high — SQL generation rarely needs more
    llm_rate_limit_retries: int = 4   # backoff retries on 429/5xx (free tiers have low per-minute quotas)
    max_retries: int = 3              # total generation attempts per question
    row_limit: int = 1000             # LIMIT injected when the query has none
    summary_rows: int = 50            # rows shown to the summarizer
    statement_timeout_ms: int = 5000

    @model_validator(mode="after")
    def _default_model(self) -> "Settings":
        if not self.llm_model:
            self.llm_model = DEFAULT_MODELS[self.llm_provider]
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
