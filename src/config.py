"""Central settings loaded from environment variables / .env file."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Storage
    data_dir: str = "./data"

    # Scraper
    scraper_lookback_days: int = 3
    scraper_max_results: int = 50

    # Scorer
    scorer_min_score: float = 0.4

    # Claude
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-20250514"

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # X / Twitter
    x_api_key: str = ""
    x_api_secret: str = ""
    x_access_token: str = ""
    x_access_secret: str = ""

    # Poster
    poster_min_interval_min: int = 30

    # Logging
    log_level: str = "INFO"

    @property
    def db_path(self) -> str:
        return f"{self.data_dir}/optix.db"


settings = Settings()
