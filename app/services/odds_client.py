import httpx
import time
from difflib import SequenceMatcher
from app.core.config import settings


# ✅ Team name aliases — Kalshi uses short names, sportsbook uses full names
TEAM_ALIASES = {
    "usa": ["united states", "usa", "us"],
    "south korea": ["korea republic", "south korea", "korea"],
    "korea republic": ["korea republic", "south korea", "korea"],
    "ivory coast": ["côte d'ivoire", "ivory coast", "cote d'ivoire"],
    "iran": ["iran", "ir iran"],
    "ir iran": ["iran", "ir iran"],
    "south africa": ["south africa", "rsa"],
    "new zealand": ["new zealand", "nzl"],
    "saudi arabia": ["saudi arabia", "ksa"],
    "cape verde": ["cape verde", "cpv"],
    "curacao": ["curaçao", "curacao"],
    "curaçao": ["curaçao", "curacao"],
    "dr congo": ["dr congo", "congo dr", "democratic republic of congo"],
    "bosnia": ["bosnia & herzegovina", "bosnia and herzegovina", "bosnia"],
    "france": ["france", "fra"],
    "norway": ["norway", "nor"],
    "iraq": ["iraq", "irq"],
    "czech republic": ["czech republic", "czechia"],
    "austria": ["austria", "aut"],
    "croatia": ["croatia", "cro"],
    "senegal": ["senegal", "sen"],
    "colombia": ["colombia", "col"],
    "portugal": ["portugal", "por"],
    "uzbekistan": ["uzbekistan", "uzb"],
    "algeria": ["algeria", "dza"],
    "jordan": ["jordan", "jor"],
    "ecuador": ["ecuador", "ecu"],
    "panama": ["panama", "pan"],
    "paraguay": ["paraguay", "par"],
    "ghana": ["ghana", "gha"],
    "haiti": ["haiti", "hti"],
    "scotland": ["scotland", "sco"],
    "morocco": ["morocco", "mar"],
    "tunisia": ["tunisia", "tun"],
    "switzerland": ["switzerland", "sui"],
    "qatar": ["qatar", "qat"],
    "canada": ["canada", "can"],
    "turkey": ["turkey", "tur", "türkiye"],
}


def _normalize(name: str) -> str:
    return name.lower().strip()


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _is_match(kalshi_team: str, sportsbook_team: str) -> bool:
    k = _normalize(kalshi_team)
    s = _normalize(sportsbook_team)

    # 1. Exact
    if k == s:
        return True

    # 2. One contains the other
    if k in s or s in k:
        return True

    # 3. Alias lookup
    for canonical, aliases in TEAM_ALIASES.items():
        if k in aliases or k == canonical:
            if s in aliases or s == canonical:
                return True

    # 4. Fuzzy similarity > 80%
    if _similarity(k, s) > 0.80:
        return True

    return False


class OddsClient:
    BASE_URL = "https://api.the-odds-api.com/v4/sports/soccer_fifa_world_cup/odds"

    _cache_data = None
    _cache_timestamp = 0
    _cache_ttl = 900

    async def fetch_events(self):
        print("➡️ Calling Odds API...")

        now = time.time()
        if self._cache_data is not None and now - self._cache_timestamp < self._cache_ttl:
            print("⚡ Using cached sportsbook data")
            return self._cache_data

        params = {
            "apiKey": settings.ODDS_API_KEY,
            "regions": "us",
            "markets": "h2h",
            "oddsFormat": "decimal",
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
                response = await client.get(self.BASE_URL, params=params)

            print("✅ Odds API responded")
            response.raise_for_status()
            data = response.json()

            # ✅ Log all available sportsbook teams so you can add aliases if needed
            print("📋 Sportsbook events available:")
            for e in data:
                print(f"   {e.get('home_team')} vs {e.get('away_team')}")

            self._cache_data = data
            self._cache_timestamp = now
            return data

        except Exception as e:
            print("❌ Odds API FAILED:", e)
            return []

    def match_event(self, events: list, home_team: str, away_team: str):
        best_match = None
        best_score = 0.0

        for event in events:
            event_home = event.get("home_team", "")
            event_away = event.get("away_team", "")

            home_match = _is_match(home_team, event_home)
            away_match = _is_match(away_team, event_away)

            # ✅ Also try flipped (some APIs swap home/away)
            home_match_flipped = _is_match(home_team, event_away)
            away_match_flipped = _is_match(away_team, event_home)

            if home_match and away_match:
                score = _similarity(_normalize(home_team), _normalize(event_home)) + \
                        _similarity(_normalize(away_team), _normalize(event_away))
                if score > best_score:
                    best_score = score
                    best_match = event

            elif home_match_flipped and away_match_flipped:
                score = _similarity(_normalize(home_team), _normalize(event_away)) + \
                        _similarity(_normalize(away_team), _normalize(event_home))
                if score > best_score:
                    best_score = score
                    best_match = event

        if not best_match:
            print(f"⚠️ No sportsbook match found for: {home_team} vs {away_team}")

        return best_match