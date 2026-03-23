import httpx
import time
from app.core.config import settings


class OddsClient:
    """
    Client for The Odds API - FIFA World Cup 3-way markets.
    Includes caching to reduce API usage.
    """

    BASE_URL = "https://api.the-odds-api.com/v4/sports/soccer_fifa_world_cup/odds"

    # --- Cache variables ---
    _cache_data = None
    _cache_timestamp = 0
    _cache_ttl = 900  # seconds (15 minutes)

    async def fetch_events(self):
        """
        Fetch all sportsbook events.
        Uses caching to prevent excessive API calls.
        """

        now = time.time()

        # 1️⃣ Return cached data if still valid
        if (
            self._cache_data is not None
            and now - self._cache_timestamp < self._cache_ttl
        ):
            return self._cache_data

        params = {
            "apiKey": settings.ODDS_API_KEY,
            "regions": "us",
            "markets": "h2h",
            "oddsFormat": "decimal",
        }

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(self.BASE_URL, params=params)
            response.raise_for_status()
            data = response.json()

        # 2️⃣ Save to cache
        self._cache_data = data
        self._cache_timestamp = now

        return data

    def match_event(self, events: list, home_team: str, away_team: str):
        """
        Match Kalshi teams to sportsbook event.
        """

        home_team = home_team.lower()
        away_team = away_team.lower()

        for event in events:
            event_home = event.get("home_team", "").lower()
            event_away = event.get("away_team", "").lower()

            # Exact match
            if home_team == event_home and away_team == event_away:
                return event

            # Partial match fallback
            if home_team in event_home and away_team in event_away:
                return event

        return None