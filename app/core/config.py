from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Loads environment variables from .env at project root
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Kalshi Trade API base URL
    KALSHI_BASE_URL: str = "https://api.elections.kalshi.com/trade-api/v2"

    # Kalshi auth (optional for now)
    KALSHI_API_KEY_ID: str | None = None
    KALSHI_PRIVATE_KEY_PEM: str | None = None

    # 🔹 Add this line
    ODDS_API_KEY: str


settings = Settings()