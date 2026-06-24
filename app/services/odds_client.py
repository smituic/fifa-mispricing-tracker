import httpx
import time
from difflib import SequenceMatcher
from app.core.config import settings, SPORTS_CONFIG, DEFAULT_SPORT


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
    # ✅ Added: DR Congo variants
    "dr congo": ["dr congo", "congo dr", "democratic republic of congo", "congo", "drc"],
    "congo": ["dr congo", "congo dr", "democratic republic of congo", "congo", "drc"],
    "portugal dr congo": ["portugal vs dr congo"],  # edge case guard
    # ✅ Added: Bosnia variants
    "bosnia": ["bosnia & herzegovina", "bosnia and herzegovina", "bosnia", "bih"],
    "bosnia & herzegovina": ["bosnia & herzegovina", "bosnia and herzegovina", "bosnia", "bih"],
    # ✅ Added: Czech Republic variants
    "czech republic": ["czech republic", "czechia", "czech"],
    "czechia": ["czech republic", "czechia", "czech"],
    # ✅ Added: Turkey/Turkiye
    "turkey": ["turkey", "tur", "türkiye", "turkiye"],
    "türkiye": ["turkey", "tur", "türkiye", "turkiye"],
    "france": ["france", "fra"],
    "norway": ["norway", "nor"],
    "iraq": ["iraq", "irq"],
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
    "sweden": ["sweden", "swe"],
    "japan": ["japan", "jpn"],
    "egypt": ["egypt", "egy"],
    "mexico": ["mexico", "mex"],
    "germany": ["germany", "ger"],
    "netherlands": ["netherlands", "ned", "holland"],
    "brazil": ["brazil", "bra"],
    "spain": ["spain", "esp"],
    "argentina": ["argentina", "arg"],
    "belgium": ["belgium", "bel"],
    "england": ["england", "eng"],
    "australia": ["australia", "aus"],
    "uruguay": ["uruguay", "uru"],
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


TEAM_NAME_MAP = {
    "IR Iran": "Iran",
    "Korea Republic": "South Korea",
    "DR Congo": "Congo",
    "Curaçao": "Curacao",
    "Côte d'Ivoire": "Ivory Coast",
    "Bosnia & Herzegovina": "Bosnia",
}

def normalize_team_name(name: str) -> str:
    if not name:
        return name
    return TEAM_NAME_MAP.get(name, name)


class OddsClient:
    """
    Wrapper around The Odds API h2h odds for a single sport.

    Instantiated once per sport per snapshot cycle. Each instance owns its
    own 15-minute cache (was previously class-level, which meant different
    sports could clobber each other's caches once MLB came online).
    """
    _BASE_URL_FMT = "https://api.the-odds-api.com/v4/sports/{sport_key}/odds"

    def __init__(self, sport: str = DEFAULT_SPORT, sport_key: str | None = None):
        # sport_key wins if explicit; otherwise derive from sport via SPORTS_CONFIG
        if sport_key is None:
            sport_key = SPORTS_CONFIG.get(sport, {}).get("odds_api_sport_key")
            if not sport_key:
                raise ValueError(f"No odds_api_sport_key configured for sport='{sport}'")

        self.sport = sport
        self.sport_key = sport_key
        self.base_url = self._BASE_URL_FMT.format(sport_key=sport_key)

        # Per-instance cache (was class-level)
        self._cache_data = None
        self._cache_timestamp = 0
        self._cache_ttl = 900

    async def fetch_events(self):
        print(f"➡️  Calling Odds API ({self.sport_key})...")

        now = time.time()
        if self._cache_data is not None and now - self._cache_timestamp < self._cache_ttl:
            print(f"⚡ Using cached sportsbook data for {self.sport_key}")
            return self._cache_data

        params = {
            "apiKey": settings.ODDS_API_KEY,
            "regions": "us",
            "markets": "h2h",
            "oddsFormat": "decimal",
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
                response = await client.get(self.base_url, params=params)

            print(f"✅ Odds API responded ({self.sport_key})")
            response.raise_for_status()
            data = response.json()

            print(f"📋 Sportsbook events available ({self.sport_key}):")
            for e in data:
                print(f"   {e.get('home_team')} vs {e.get('away_team')}")

            self._cache_data = data
            self._cache_timestamp = now
            return data

        except Exception as e:
            print(f"❌ Odds API FAILED ({self.sport_key}):", e)
            return []

    def match_event(self, events: list, home_team: str, away_team: str):
        """Fuzzy name-based matching. FIFA path only — MLB uses code-based
        resolution in match_analysis_service.py and bypasses this."""
        best_match = None
        best_score = 0.0

        for event in events:
            event_home = event.get("home_team", "")
            event_away = event.get("away_team", "")

            home_norm = _normalize(normalize_team_name(home_team))
            away_norm = _normalize(normalize_team_name(away_team))

            event_home_norm = _normalize(normalize_team_name(event_home))
            event_away_norm = _normalize(normalize_team_name(event_away))

            home_match = _is_match(home_norm, event_home_norm)
            away_match = _is_match(away_norm, event_away_norm)

            home_match_flipped = _is_match(home_norm, event_away_norm)
            away_match_flipped = _is_match(away_norm, event_home_norm)

            if home_match and away_match:
                score = _similarity(home_norm, event_home_norm) + \
                        _similarity(away_norm, event_away_norm)
                if score > best_score:
                    best_score = score
                    best_match = event

            elif home_match_flipped and away_match_flipped:
                score = _similarity(home_norm, event_away_norm) + \
                        _similarity(away_norm, event_home_norm)
                if score > best_score:
                    best_score = score
                    best_match = event

        if not best_match and best_score > 1.5:
            print(f"⚡ Using fuzzy fallback for: {home_team} vs {away_team}")
            return best_match

        return best_match