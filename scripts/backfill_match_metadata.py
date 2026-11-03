"""
Backfill match_metadata from Kalshi settled/closed markets.

The snapshots table stores only match_id + team, so match titles are lost
once markets close. This recovers them from Kalshi's settled markets.

Only backfills match_ids that already exist in snapshots — we don't want
metadata for matches we never captured prices for.

Usage:  PYTHONPATH=. python3 scripts/backfill_match_metadata.py fifa
"""
import asyncio
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from app.core.config import settings, SPORTS_CONFIG
from app.services.snapshot_service import DB_PATH
from app.services.match_analysis_service import normalize_team_name


async def fetch_all_markets(series_ticker: str) -> list[dict]:
    """Page through every settled and closed market for a series.

    Kalshi rate-limits aggressively on large series (MLB has thousands of
    settled markets across a season). Throttle between pages and back off
    on 429.
    """
    out: list[dict] = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        for status in ("settled", "closed"):
            cursor = None
            pages = 0
            while True:
                params = {
                    "series_ticker": series_ticker,
                    "status": status,
                    "limit": 200,
                }
                if cursor:
                    params["cursor"] = cursor

                # Retry with exponential backoff on rate limit
                for attempt in range(6):
                    r = await client.get(
                        f"{settings.KALSHI_BASE_URL}/markets", params=params
                    )
                    if r.status_code != 429:
                        break
                    wait = 2 ** attempt
                    print(f"    429 — backing off {wait}s")
                    await asyncio.sleep(wait)
                else:
                    print(f"    giving up on {status} after repeated 429s")
                    break

                r.raise_for_status()
                data = r.json()

                markets = data.get("markets", [])
                out.extend(markets)
                pages += 1

                cursor = data.get("cursor")
                if not cursor or not markets:
                    break

                await asyncio.sleep(0.5)   # be polite between pages

            print(f"  {status}: {pages} page(s)")
    return out


# Kickoff time (commence_time) is derived two ways:
#   - Ticker has an HHMM segment (MLB, e.g. KXMLBGAME-26JUN261907TEXTOR):
#     that's the scheduled first pitch in US Eastern. Parse it as
#     America/New_York and convert to UTC.
#   - Otherwise (NFL, NBA, FIFA): use Kalshi's occurrence_datetime minus 3h.
#     For scheduled markets occurrence_datetime is a fixed kickoff + 3h
#     (verified against Odds API commence_time). For settled markets Kalshi
#     replaces it with the actual result time (~kickoff + 2h), so minus 3h
#     lands before kickoff. That's deliberately conservative: the archive
#     uses the last snapshot before commence_time, so erring early can only
#     exclude pre-game snapshots, never include in-game ones.
KALSHI_OCCURRENCE_OFFSET = timedelta(hours=3)


def _format_utc(dt: datetime) -> str:
    """Match the Odds API commence_time format: 2026-06-26T23:07:00Z."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def derive_commence_time(market: dict) -> str | None:
    """Best-effort kickoff time (UTC ISO string) for a Kalshi market."""
    from app.services.team_name_maps import _TICKER_DATE_RE

    event_ticker = market.get("event_ticker") or ""
    if "-" in event_ticker:
        m = _TICKER_DATE_RE.match(event_ticker.split("-", 1)[1])
        if m:
            yy, mmm, dd, hhmm, _codes = m.groups()
            if hhmm:
                local = datetime.strptime(
                    f"20{yy}-{mmm}-{dd} {hhmm}", "%Y-%b-%d %H%M"
                ).replace(tzinfo=ZoneInfo("America/New_York"))
                return _format_utc(local)

    occ = market.get("occurrence_datetime")
    if not occ:
        return None
    try:
        occ_dt = datetime.fromisoformat(occ.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _format_utc(occ_dt - KALSHI_OCCURRENCE_OFFSET)


def build_metadata(markets: list[dict], sport: str) -> dict[str, dict]:
    """Map event_ticker -> {title, home_team, away_team, commence_time}."""
    meta: dict[str, dict] = {}

    for m in markets:
        event_ticker = m.get("event_ticker")
        if not event_ticker or event_ticker in meta:
            continue

        if sport in ("mlb", "nfl", "nba"):
            # Code-based sports resolve teams from ticker codes (their titles,
            # e.g. "Baltimore wins", can't be parsed). Mirrors
            # build_match_analysis.
            from app.services.team_name_maps import (
                parse_event_ticker, codes_to_names, SPORT_CODE_SETS,
            )
            parsed = parse_event_ticker(event_ticker, SPORT_CODE_SETS[sport])
            if not parsed:
                continue
            away_code, home_code = parsed
            away, home = codes_to_names(away_code, home_code, sport=sport)
            if not (away and home):
                continue
            meta[event_ticker] = {
                "title": f"{away} vs {home}",   # Kalshi tickers are away-first
                "home_team": home,
                "away_team": away,
                "commence_time": derive_commence_time(m),
            }
        else:
            # FIFA: parse from the market title, same as the live path.
            title = m.get("title")
            if not title or " vs " not in title:
                continue
            clean = title.replace(" Winner?", "").strip()
            parts = clean.split(" vs ")
            if len(parts) != 2:
                continue
            home = normalize_team_name(parts[0])
            away = normalize_team_name(parts[1])
            meta[event_ticker] = {
                "title": f"{home} vs {away}",
                "home_team": home,
                "away_team": away,
                "commence_time": derive_commence_time(m),
            }

    return meta


def known_match_ids(sport: str) -> set[str]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT DISTINCT match_id FROM snapshots WHERE sport = ?", (sport,))
    ids = {r[0] for r in c.fetchall()}
    conn.close()
    return ids


def upsert(rows: list[dict]) -> None:
    if not rows:
        return
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    # commence_time is never overwritten: a value captured live from the
    # Odds API is exact, while the backfill's is derived. Only fill gaps.
    conn.cursor().executemany("""
        INSERT INTO match_metadata (
            sport, match_id, title, home_team, away_team, commence_time, last_seen
        ) VALUES (
            :sport, :match_id, :title, :home_team, :away_team, :commence_time, :last_seen
        )
        ON CONFLICT(sport, match_id) DO UPDATE SET
            title         = COALESCE(excluded.title, match_metadata.title),
            home_team     = COALESCE(excluded.home_team, match_metadata.home_team),
            away_team     = COALESCE(excluded.away_team, match_metadata.away_team),
            commence_time = COALESCE(match_metadata.commence_time, excluded.commence_time)
    """, rows)
    conn.commit()
    conn.close()


async def main(sport: str) -> None:
    cfg = SPORTS_CONFIG.get(sport)
    if not cfg:
        print(f"Unknown sport '{sport}'. Known: {list(SPORTS_CONFIG)}")
        sys.exit(1)

    series = cfg["kalshi_series_ticker"]
    print(f"Fetching settled/closed markets for {sport} (series={series})...")
    markets = await fetch_all_markets(series)
    print(f"Total markets fetched: {len(markets)}")

    meta = build_metadata(markets, sport)
    print(f"Distinct matches with recoverable titles: {len(meta)}")

    have = known_match_ids(sport)
    print(f"Matches in snapshots: {len(have)}")

    now = datetime.now(timezone.utc).isoformat()
    rows = [
        {
            "sport": sport,
            "match_id": mid,
            "title": m["title"],
            "home_team": m["home_team"],
            "away_team": m["away_team"],
            "commence_time": m.get("commence_time"),
            "last_seen": now,
        }
        for mid, m in meta.items()
        if mid in have          # only matches we actually captured prices for
    ]

    upsert(rows)
    print(f"Backfilled: {len(rows)}")

    missing = have - set(meta)
    if missing:
        print(f"\nNo title recoverable for {len(missing)} match(es):")
        for mid in sorted(missing)[:10]:
            print("  ", mid)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "fifa"))
