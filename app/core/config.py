from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Loads environment variables from .env at project root
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Kalshi Trade API base URL
    KALSHI_BASE_URL: str = "https://api.elections.kalshi.com/trade-api/v2"

    # Kalshi auth (optional for now)
    KALSHI_API_KEY_ID: str | None = None
    KALSHI_PRIVATE_KEY_PEM: str | None = None

    # Odds API key
    ODDS_API_KEY: str
    ODDS_API_KEY_FIFA: str | None = None
    ODDS_API_KEY_MLB: str | None = None

    # Minimum EV threshold for signal classification
    MIN_EV_SIGNAL: float = 0.01

    # Dev mode — must be False in production
    DEV_MODE: bool = False


settings = Settings()


# ─────────────────────────────────────────────────────────────────────────────
# Sports configuration
#
# Single source of truth for sport metadata. Each sport maps to its Kalshi
# series ticker, Odds API sport key, market structure, and lifecycle status.
#
# To add a new sport later, add a new entry below — no other code changes
# are required for snapshot ingestion or backward-compatible routing.
#
# Status meanings:
#   "live"        — currently in season, polling active
#   "coming_soon" — season hasn't started, show countdown
#   "off_season"  — between seasons, show "back when X starts"
#   "archive"     — historical, data frozen
# ─────────────────────────────────────────────────────────────────────────────

SPORTS_CONFIG: dict[str, dict] = {
    "fifa": {
        "name": "FIFA World Cup",
        "short_name": "FIFA",
        "kalshi_series_ticker": "KXWCGAME",
        "odds_api_sport_key": "soccer_fifa_world_cup",
        "market_type": "3way",            # home / draw / away
        "status": "archive",
        "active_through": "2026-07-19",   # World Cup final
    },
    "mlb": {
        "name": "Major League Baseball",
        "short_name": "MLB",
        "kalshi_series_ticker": "KXMLBGAME",
        "odds_api_sport_key": "baseball_mlb",
        "market_type": "2way",            # home / away (moneyline)
        "status": "live",                 # season in progress
        "active_through": "2026-11-05",   # ~World Series end
    },
}

# Default sport for backward-compatible behavior in routes and helpers
DEFAULT_SPORT = "fifa"



def get_odds_api_key(sport: str) -> str:
    """Return the Odds API key for a sport.

    Prefers a sport-specific key (ODDS_API_KEY_<SPORT>) if set, otherwise
    falls back to the shared ODDS_API_KEY. This lets each sport run on its
    own free-tier key while keeping unconfigured sports working.
    """
    per_sport = getattr(settings, f"ODDS_API_KEY_{sport.upper()}", None)
    return per_sport or settings.ODDS_API_KEY