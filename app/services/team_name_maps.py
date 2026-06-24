"""
Centralized, per-sport team-name handling.

Two distinct jobs live here:

1. Free-text name normalization (Kalshi subtitle -> Odds API display name).
   Used by sports where the Kalshi text and Odds API text are *almost* the
   same and just need a few aliases reconciled (e.g. FIFA country names).

2. Code-based team resolution (Kalshi 3-letter ticker code -> Odds API full
   name). Used by sports where the Kalshi subtitle is too sparse to match on
   (e.g. MLB city-only subtitles, multi-team cities), so we instead read the
   team codes embedded in the event_ticker.

This file is intentionally pure/standalone: no imports from the rest of the
app, so it can be unit-tested in isolation (see the __main__ block).
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher


# ─────────────────────────────────────────────────────────────────────────────
# 1. Free-text name maps (sport -> {kalshi_name: odds_api_name})
#    FIFA values are lifted verbatim from the previous single TEAM_NAME_MAP so
#    behavior is byte-for-byte identical.
# ─────────────────────────────────────────────────────────────────────────────

TEAM_NAME_MAPS: dict[str, dict[str, str]] = {
    "fifa": {
        "IR Iran": "Iran",
        "Korea Republic": "South Korea",
        "DR Congo": "Congo",
        "Curaçao": "Curacao",
        "Côte d'Ivoire": "Ivory Coast",
        "Bosnia & Herzegovina": "Bosnia",
        "Tie": "Draw",
        "Draw": "Draw",
    },
    # MLB resolves teams via ticker codes (below), not free text, so this map
    # stays empty. Kept for symmetry and any future stray-subtitle fixes.
    "mlb": {},
}


def normalize_team_name(name: str, sport: str = "fifa") -> str:
    """Sport-aware replacement for the old module-level normalize_team_name.

    Defaults to 'fifa' so existing call sites that don't pass a sport keep
    their current behavior.
    """
    if not name:
        return name
    return TEAM_NAME_MAPS.get(sport, {}).get(name, name)


# ─────────────────────────────────────────────────────────────────────────────
# 2. MLB code -> Odds API full name.
#
#    !!! REPLACE the codes in this dict with your verified 30-team map. !!!
#    The Odds API names (values) are stable "City Nickname" strings; the Kalshi
#    *codes* (keys) are the uncertain side. Your five 2-char codes are applied.
#
#    Flagged below are the three I'm least sure Kalshi spells the way I guessed
#    — confirm these against your map / the raw sample:
#      - White Sox: CWS  (could be CHW)
#      - Athletics: ATH  (team relocated; could be OAK/SAC, and the Odds API
#                         name may be "Oakland Athletics" or just "Athletics")
#      - Nationals: WSH  (could be WAS/WSN)
# ─────────────────────────────────────────────────────────────────────────────

MLB_CODE_TO_ODDS: dict[str, str] = {
    "AZ":  "Arizona Diamondbacks",   # 2-char (per your note)
    "ATL": "Atlanta Braves",
    "BAL": "Baltimore Orioles",
    "BOS": "Boston Red Sox",
    "CHC": "Chicago Cubs",
    "CWS": "Chicago White Sox",      # FLAG: maybe CHW
    "CIN": "Cincinnati Reds",
    "CLE": "Cleveland Guardians",
    "COL": "Colorado Rockies",
    "DET": "Detroit Tigers",
    "HOU": "Houston Astros",
    "KC":  "Kansas City Royals",     # 2-char (per your note)
    "LAA": "Los Angeles Angels",
    "LAD": "Los Angeles Dodgers",
    "MIA": "Miami Marlins",
    "MIL": "Milwaukee Brewers",
    "MIN": "Minnesota Twins",
    "NYM": "New York Mets",
    "NYY": "New York Yankees",
    "ATH": "Athletics",              # FLAG: relocation; code + Odds name uncertain
    "PHI": "Philadelphia Phillies",
    "PIT": "Pittsburgh Pirates",
    "SD":  "San Diego Padres",       # 2-char (per your note)
    "SF":  "San Francisco Giants",   # 2-char (per your note)
    "SEA": "Seattle Mariners",
    "STL": "St. Louis Cardinals",
    "TB":  "Tampa Bay Rays",         # 2-char (per your note)
    "TEX": "Texas Rangers",
    "TOR": "Toronto Blue Jays",
    "WSH": "Washington Nationals",   # FLAG: maybe WAS/WSN
}

# Per-sport set of valid codes, used by the ticker parser's split logic.
SPORT_CODE_SETS: dict[str, set[str]] = {
    "mlb": set(MLB_CODE_TO_ODDS),
}

# Per-sport code->name resolver tables.
SPORT_CODE_TO_NAME: dict[str, dict[str, str]] = {
    "mlb": MLB_CODE_TO_ODDS,
}


# ─────────────────────────────────────────────────────────────────────────────
# 3. Kalshi event_ticker parser.
#
#    MLB:  KXMLBGAME-26JUN251235SEAPIT
#                    └YY┘└MMM┘└DD┘└HHMM┘└codes┘   -> away=SEA, home=PIT
#    FIFA: KXWCGAME-26JUN21NZLEGY  (no time segment)
#
#    Team codes are variable length (2-3 chars). We strip the date(/time)
#    prefix structurally, then try each away/home split and keep the one where
#    BOTH halves are known codes. With real MLB codes the split is unique; if it
#    ever isn't (0 valid splits = unknown code, >1 = ambiguous) we return None
#    so the caller can log it loudly rather than mismatch silently.
# ─────────────────────────────────────────────────────────────────────────────

# YY (2 digits) + MMM (3 letters) + DD (2 digits) + optional HHMM (4 digits),
# then the rest (team codes).
_TICKER_DATE_RE = re.compile(r"^(\d{2})([A-Z]{3})(\d{2})(\d{4})?(.+)$")


def parse_event_ticker(
    event_ticker: str, valid_codes: set[str]
) -> tuple[str, str] | None:
    """Return (away_code, home_code) from a Kalshi game event_ticker.

    `valid_codes` is the sport's known code set (e.g. SPORT_CODE_SETS['mlb']).
    Returns None if the ticker can't be parsed into exactly one valid split.
    """
    if "-" not in event_ticker:
        return None

    body = event_ticker.split("-", 1)[1]
    m = _TICKER_DATE_RE.match(body)
    if not m:
        return None

    codes = m.group(5)  # everything after the date/time prefix
    valid_splits: list[tuple[str, str]] = []
    for away_len in (2, 3):
        away, home = codes[:away_len], codes[away_len:]
        if away in valid_codes and home in valid_codes:
            valid_splits.append((away, home))

    if len(valid_splits) == 1:
        return valid_splits[0]
    return None  # 0 = unknown code(s); >1 = ambiguous — caller should log


def codes_to_names(
    away_code: str, home_code: str, sport: str = "mlb"
) -> tuple[str | None, str | None]:
    """Map a (away_code, home_code) pair to (away_name, home_name)."""
    table = SPORT_CODE_TO_NAME.get(sport, {})
    return table.get(away_code), table.get(home_code)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Outcome -> full-name resolver.
#
#    !!! The PRIMARY path below assumes each Kalshi market's own `ticker` ends
#    in the team code (e.g. ...SEAPIT-SEA). One raw markets sample confirms
#    whether that's true. If it isn't, only the fuzzy fallback runs — still
#    correct for distinct-city games, but it can't split same-city matchups
#    (Yankees/Mets, Dodgers/Angels, Cubs/White Sox), which is exactly why the
#    code path matters. Adjust _code_from_market once we see the real shape.
# ─────────────────────────────────────────────────────────────────────────────

def _normalize(s: str) -> str:
    return (s or "").lower().strip()


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _code_from_market(market: dict, valid_codes: set[str]) -> str | None:
    """Try to read a team code off the market's own ticker (last segment)."""
    ticker = market.get("ticker") or ""
    if "-" in ticker:
        last = ticker.rsplit("-", 1)[-1]
        if last in valid_codes:
            return last
    return None


def resolve_outcome_team(
    market: dict,
    away_name: str,
    home_name: str,
    sport: str = "mlb",
) -> str | None:
    """Resolve one Kalshi market to its full Odds API team name.

    Chooses between the two teams already known for this game (away_name,
    home_name), so even the fuzzy fallback only ever picks between two options.
    """
    valid_codes = SPORT_CODE_SETS.get(sport, set())
    table = SPORT_CODE_TO_NAME.get(sport, {})

    # Primary: code from the market's own ticker.
    code = _code_from_market(market, valid_codes)
    if code and code in table:
        return table[code]

    # Fallback: fuzzy city/subtitle against the two candidates.
    subtitle = market.get("yes_sub_title") or market.get("title") or ""
    if not subtitle:
        return None
    away_score = _similarity(subtitle, away_name)
    home_score = _similarity(subtitle, home_name)
    # substring containment beats raw ratio for "Seattle" in "Seattle Mariners"
    if _normalize(subtitle) in _normalize(away_name):
        away_score += 0.5
    if _normalize(subtitle) in _normalize(home_name):
        home_score += 0.5
    if max(away_score, home_score) == 0:
        return None
    return away_name if away_score >= home_score else home_name


# ─────────────────────────────────────────────────────────────────────────────
# Smoke test:  python3 team_name_maps.py
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    codes = SPORT_CODE_SETS["mlb"]
    cases = {
        "KXMLBGAME-26JUN251235SEAPIT": ("SEA", "PIT"),  # 3+3
        "KXMLBGAME-26JUN251940KCTB":   ("KC", "TB"),    # 2+2
        "KXMLBGAME-26JUN251310SDLAD":  ("SD", "LAD"),   # 2+3
        "KXMLBGAME-26JUN251705NYYBOS": ("NYY", "BOS"),  # 3+3
    }
    ok = True
    for ticker, expected in cases.items():
        got = parse_event_ticker(ticker, codes)
        status = "ok " if got == expected else "FAIL"
        if got != expected:
            ok = False
        print(f"[{status}] {ticker} -> {got} (expected {expected})")

    # FIFA-style ticker (no time) should NOT parse under MLB codes — proves the
    # parser doesn't accidentally swallow a different format as MLB.
    print("FIFA-shaped under MLB codes:",
          parse_event_ticker("KXWCGAME-26JUN21NZLEGY", codes))

    print("\nall passed" if ok else "\nSOME FAILED")
