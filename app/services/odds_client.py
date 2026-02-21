import httpx
from app.core.config import settings


class OddsClient:
    """
    Client for The Odds API - FIFA World Cup 3-way markets.
    """

    BASE_URL = "https://api.the-odds-api.com/v4/sports/soccer_fifa_world_cup/odds"

    async def fetch_events(self):
        """
        Fetch all current FIFA World Cup events with 3-way moneyline (h2h).
        """
        params = {
            "apiKey": settings.ODDS_API_KEY,
            "regions": "us",
            "markets": "h2h",
            "oddsFormat": "decimal",
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(self.BASE_URL, params=params)
            response.raise_for_status()
            return response.json()

    def match_event(self, events: list, home_team: str, away_team: str):
        """
        Match Kalshi teams to sportsbook event.
        """
        home_team = home_team.lower()
        away_team = away_team.lower()

        for event in events:
            event_home = event.get("home_team", "").lower()
            event_away = event.get("away_team", "").lower()

            if home_team in event_home and away_team in event_away:
                return event

        return None