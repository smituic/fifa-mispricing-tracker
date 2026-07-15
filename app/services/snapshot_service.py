import sqlite3
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict
from app.core.config import settings, SPORTS_CONFIG, DEFAULT_SPORT
from app.services.match_analysis_service import build_match_analysis
from app.services.kalshi_client import KalshiClient
from app.services.odds_client import OddsClient

DB_PATH = "fifa_tracker.db"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    # WAL mode: allows reads while writing, much safer for concurrent access
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    cursor = conn.cursor()

    # Create table with sport column included for fresh databases.
    # Existing databases will be migrated below.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sport TEXT NOT NULL DEFAULT 'fifa',
            timestamp TEXT,
            match_id TEXT,
            team TEXT,
            ask_probability REAL,
            bid_probability REAL,
            mid_price REAL,
            fair_probability REAL,
            expected_value REAL,
            liquidity_score REAL,
            confidence_score REAL,
            volume INTEGER,
            open_interest INTEGER
        )
    """)

    # Migration: add `sport` column to pre-existing tables that lack it.
    # This is idempotent — safe to run on every startup.
    cursor.execute("PRAGMA table_info(snapshots)")
    existing_columns = {row[1] for row in cursor.fetchall()}
    if "sport" not in existing_columns:
        print("Migrating snapshots table: adding 'sport' column (default 'fifa')")
        cursor.execute(
            "ALTER TABLE snapshots ADD COLUMN sport TEXT NOT NULL DEFAULT 'fifa'"
        )

    # Indexes — note the new (sport, match_id, timestamp) index for fast
    # per-sport history queries.
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_snapshots_match_time
        ON snapshots(match_id, timestamp)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_snapshots_sport_match_time
        ON snapshots(sport, match_id, timestamp)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_snapshots_team
        ON snapshots(team)
    """)

    # Match metadata: title and home/away per match.
    #
    # The snapshots table only stores match_id + team, so once a sport's
    # markets close on Kalshi we lose the ability to reconstruct match
    # titles (they only exist in the live Kalshi response). Archive mode
    # reads this table to render historical matches properly.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS match_metadata (
            sport TEXT NOT NULL,
            match_id TEXT NOT NULL,
            title TEXT,
            home_team TEXT,
            away_team TEXT,
            last_seen TEXT,
            PRIMARY KEY (sport, match_id)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_match_metadata_sport
        ON match_metadata(sport)
    """)

    conn.commit()
    conn.close()


def _save_snapshot_rows_bulk(rows: list[dict]):
    """
    Single connection, single transaction for ALL rows in a cycle.
    Called via run_in_executor so it never blocks the event loop.

    Each row dict must include a 'sport' key.
    """
    if not rows:
        return

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    cursor = conn.cursor()

    cursor.executemany("""
        INSERT INTO snapshots (
            sport, timestamp, match_id, team,
            ask_probability, bid_probability, mid_price,
            fair_probability, expected_value,
            liquidity_score, confidence_score,
            volume, open_interest
        ) VALUES (
            :sport, :timestamp, :match_id, :team,
            :ask_probability, :bid_probability, :mid_price,
            :fair_probability, :expected_value,
            :liquidity_score, :confidence_score,
            :volume, :open_interest
        )
    """, rows)

    conn.commit()
    conn.close()


async def save_snapshot_rows_async(rows: list[dict]):
    """
    ✅ Offloads the blocking SQLite write to a thread
    so the event loop stays free.
    """
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _save_snapshot_rows_bulk, rows)


def _save_match_metadata_bulk(rows: list[dict]):
    """
    Upsert match metadata for a snapshot cycle.

    COALESCE on update so a cycle that lacks home/away (e.g. a match with no
    sportsbook data that round) never overwrites values captured earlier.
    """
    if not rows:
        return

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    cursor = conn.cursor()

    cursor.executemany("""
        INSERT INTO match_metadata (
            sport, match_id, title, home_team, away_team, last_seen
        ) VALUES (
            :sport, :match_id, :title, :home_team, :away_team, :last_seen
        )
        ON CONFLICT(sport, match_id) DO UPDATE SET
            title     = COALESCE(excluded.title, match_metadata.title),
            home_team = COALESCE(excluded.home_team, match_metadata.home_team),
            away_team = COALESCE(excluded.away_team, match_metadata.away_team),
            last_seen = excluded.last_seen
    """, rows)

    conn.commit()
    conn.close()


async def save_match_metadata_async(rows: list[dict]):
    """Offload the blocking metadata upsert to a thread."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _save_match_metadata_bulk, rows)


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


async def snapshot_all_matches(sport: str = DEFAULT_SPORT):
    """
    Run one snapshot cycle for a given sport.

    Looks up the sport's Kalshi series_ticker from SPORTS_CONFIG,
    fetches markets, runs analysis, and persists snapshots tagged with sport.
    """
    sport_config = SPORTS_CONFIG.get(sport)
    if sport_config is None:
        print(f"Unknown sport '{sport}' — skipping snapshot cycle")
        return

    series_ticker = sport_config["kalshi_series_ticker"]

    # One shared client for the entire cycle, not one per match
    client = KalshiClient(base_url=settings.KALSHI_BASE_URL)
    odds_client = OddsClient(sport=sport)

    try:
        print(f"[{sport}] Fetching markets (series={series_ticker})...")
        data = await client.get_markets(
            series_ticker=series_ticker,
            status="open",
            limit=200,
        )

        markets = data.get("markets", [])
        match_ids = list(set(m["event_ticker"] for m in markets))

        print(f"[{sport}] Fetching sportsbook events...")
        sportsbook_events = await odds_client.fetch_events()

        print(f"[{sport}] Total matches: {len(match_ids)}")

        # Pass shared clients into each task — no more per-match instantiation
        tasks = [
            build_match_analysis(
                match_id,
                markets,
                sportsbook_events,
                client=client,
                odds_client=odds_client,
                sport=sport,
            )
            for match_id in match_ids
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect ALL rows first, then write in one bulk transaction
        rows_to_insert = []
        metadata_rows = []
        now = datetime.utcnow().isoformat()

        for analysis in results:
            if (
                not analysis
                or isinstance(analysis, Exception)
                or "kalshi" not in analysis
                or "analysis" not in analysis
            ):
                if isinstance(analysis, Exception):
                    print(f"[{sport}] Match analysis error: {analysis}")
                continue

            # Capture title/home/away for archive mode. Runs before the
            # outcome loop so matches with no fair data still record a title.
            if analysis.get("match_title"):
                metadata_rows.append({
                    "sport": sport,
                    "match_id": analysis["match_id"],
                    "title": analysis.get("match_title"),
                    "home_team": analysis.get("home_team"),
                    "away_team": analysis.get("away_team"),
                    "last_seen": now,
                })

            kalshi_outcomes = analysis["kalshi"]["outcomes"]
            analysis_outcomes = analysis["analysis"]["outcomes"]

            for outcome in analysis_outcomes:
                team = normalize_team_name(outcome["team"])
                kalshi_data = next(
                    (o for o in kalshi_outcomes if normalize_team_name(o["team"]) == team), None
                )
                if not kalshi_data:
                    continue

                rows_to_insert.append({
                    "sport": sport,
                    "timestamp": now,
                    "match_id": analysis["match_id"],
                    "team": team,
                    "ask_probability": kalshi_data["implied_ask_prob"],
                    "bid_probability": kalshi_data["implied_bid_prob"],
                    "mid_price": kalshi_data["mid_price"],
                    "fair_probability": outcome["sportsbook_fair_probability"],
                    "expected_value": outcome["expected_value"],
                    "liquidity_score": outcome["liquidity_score"],
                    "confidence_score": outcome["confidence_score"],
                    "volume": kalshi_data.get("volume"),
                    "open_interest": kalshi_data.get("open_interest"),
                })

       # One single async-safe bulk write for the whole cycle
        await save_snapshot_rows_async(rows_to_insert)
        await save_match_metadata_async(metadata_rows)
        print(
            f"[{sport}] Saved {len(rows_to_insert)} snapshot rows, "
            f"{len(metadata_rows)} metadata rows"
        )

    finally:
        await client.close()


async def start_snapshot_loop():
    """
    Main snapshot loop. Iterates over every sport with status='live'
    in SPORTS_CONFIG and runs a snapshot cycle for each.

    Currently only FIFA is live. Adding new live sports is a config-only
    change in app/core/config.py.
    """
    while True:
        live_sports = [
            sport for sport, cfg in SPORTS_CONFIG.items()
            if cfg.get("status") == "live"
        ]

        if not live_sports:
            print("No live sports configured — sleeping")
        else:
            print(f"Running snapshot cycle for sports: {live_sports}")
            for sport in live_sports:
                try:
                    await snapshot_all_matches(sport=sport)
                except Exception as e:
                    print(f"[{sport}] Snapshot cycle failed: {e}")

        await asyncio.sleep(7200)


# ── Read helpers (unchanged logic, same signatures) ──────────────────────────

def get_match_history(match_id: str, hours: int = 6, sport: str | None = None):
    """
    Get per-team snapshot history for one match within the time window.

    If `sport` is provided, filter to that sport. If None, returns rows
    regardless of sport (backward-compatible behavior).
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cutoff_iso = (datetime.utcnow() - timedelta(hours=hours)).isoformat()

    if sport is not None:
        cursor.execute("""
            SELECT timestamp, team, ask_probability, bid_probability, mid_price,
                   fair_probability, expected_value, liquidity_score,
                   confidence_score, volume, open_interest
            FROM snapshots
            WHERE sport = ? AND match_id = ? AND timestamp >= ?
            ORDER BY timestamp ASC
        """, (sport, match_id, cutoff_iso))
    else:
        cursor.execute("""
            SELECT timestamp, team, ask_probability, bid_probability, mid_price,
                   fair_probability, expected_value, liquidity_score,
                   confidence_score, volume, open_interest
            FROM snapshots
            WHERE match_id = ? AND timestamp >= ?
            ORDER BY timestamp ASC
        """, (match_id, cutoff_iso))

    rows = cursor.fetchall()
    conn.close()

    grouped = {}
    for row in rows:
        (timestamp, team, ask_prob, bid_prob, mid_price,
         fair_prob, ev, liquidity_score, confidence_score,
         volume, open_interest) = row

        if team not in grouped:
            grouped[team] = []

        grouped[team].append({
            "timestamp": timestamp,
            "ask_probability": ask_prob,
            "bid_probability": bid_prob,
            "mid_price": mid_price,
            "fair_probability": fair_prob,
            "expected_value": ev,
            "signal": "Undervalued" if ev > 0 else "Overvalued",
            "liquidity_score": liquidity_score,
            "confidence_score": confidence_score,
            "volume": volume,
            "open_interest": open_interest,
        })

    return grouped


def get_match_history_for_all(hours: int, sport: str | None = None):
    """
    Get latest EV per (match, team) across all matches in the time window.

    If `sport` is provided, filter to that sport. If None, returns rows
    regardless of sport (backward-compatible behavior).
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()

    if sport is not None:
        cursor.execute("""
            SELECT match_id, team, timestamp, expected_value,
                   confidence_score, liquidity_score
            FROM snapshots
            WHERE sport = ? AND timestamp >= ?
            ORDER BY timestamp ASC
        """, (sport, cutoff))
    else:
        cursor.execute("""
            SELECT match_id, team, timestamp, expected_value,
                   confidence_score, liquidity_score
            FROM snapshots
            WHERE timestamp >= ?
            ORDER BY timestamp ASC
        """, (cutoff,))

    rows = cursor.fetchall()
    conn.close()

    team_series = defaultdict(list)
    match_teams = defaultdict(set)

    for match_id, team, ts, ev, confidence_score, liquidity_score in rows:
        team_series[(match_id, team)].append({
            "timestamp": ts,
            "expected_value": ev,
            "confidence_score": confidence_score,
            "liquidity_score": liquidity_score,
        })
        match_teams[match_id].add(team)

    results = []
    for (match_id, team), history in team_series.items():
        teams = [t for t in sorted(match_teams[match_id]) if t not in ("Draw", "Tie")]
        history.sort(key=lambda r: r["timestamp"])
        latest = history[-1]
        results.append({
            "match_id": match_id,
            "team": team,
            "match": " vs ".join(teams),
            "ev_series": [r["expected_value"] for r in history],
            "confidence_score": latest["confidence_score"] or 0,
            "liquidity_score": latest["liquidity_score"] or 0,
        })

    return results