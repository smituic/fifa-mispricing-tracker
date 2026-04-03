import sqlite3
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict
from app.core.config import settings
from app.services.match_analysis_service import build_match_analysis
from app.services.kalshi_client import KalshiClient
from app.services.odds_client import OddsClient

DB_PATH = "fifa_tracker.db"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    # ✅ WAL mode: allows reads while writing, much safer for concurrent access
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_snapshots_match_time
        ON snapshots(match_id, timestamp)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_snapshots_team
        ON snapshots(team)
    """)
    conn.commit()
    conn.close()


def _save_snapshot_rows_bulk(rows: list[dict]):
    """
    ✅ Single connection, single transaction for ALL rows in a cycle.
    Called via run_in_executor so it never blocks the event loop.
    """
    if not rows:
        return

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    cursor = conn.cursor()

    cursor.executemany("""
        INSERT INTO snapshots (
            timestamp, match_id, team,
            ask_probability, bid_probability, mid_price,
            fair_probability, expected_value,
            liquidity_score, confidence_score,
            volume, open_interest
        ) VALUES (
            :timestamp, :match_id, :team,
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


async def snapshot_all_matches():
    # ✅ One shared client for the entire cycle, not one per match
    client = KalshiClient(base_url=settings.KALSHI_BASE_URL)
    odds_client = OddsClient()

    try:
        print("Fetching markets...")
        data = await client.get_markets(
            series_ticker="KXWCGAME",
            status="open",
            limit=200,
        )

        markets = data.get("markets", [])
        match_ids = list(set(m["event_ticker"] for m in markets))

        print("Fetching sportsbook events...")
        sportsbook_events = await odds_client.fetch_events()

        print(f"Total matches: {len(match_ids)}")

        # ✅ Pass shared clients into each task — no more per-match instantiation
        tasks = [
            build_match_analysis(
                match_id,
                markets,
                sportsbook_events,
                client=client,          # shared
                odds_client=odds_client # shared
            )
            for match_id in match_ids
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # ✅ Collect ALL rows first, then write in one bulk transaction
        rows_to_insert = []
        now = datetime.utcnow().isoformat()

        for analysis in results:
            if (
                not analysis
                or isinstance(analysis, Exception)
                or "kalshi" not in analysis
                or "analysis" not in analysis
            ):
                if isinstance(analysis, Exception):
                    print(f"Match analysis error: {analysis}")
                continue

            kalshi_outcomes = analysis["kalshi"]["outcomes"]
            analysis_outcomes = analysis["analysis"]["outcomes"]

            for outcome in analysis_outcomes:
                team = outcome["team"]
                kalshi_data = next(
                    (o for o in kalshi_outcomes if o["team"] == team), None
                )
                if not kalshi_data:
                    continue

                rows_to_insert.append({
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
        print(f"✅ Saved {len(rows_to_insert)} snapshot rows")

    finally:
        await client.close()


async def start_snapshot_loop():
    while True:
        print("Running snapshot cycle...")
        try:
            await snapshot_all_matches()
        except Exception as e:
            print(f"Snapshot cycle failed: {e}")
        await asyncio.sleep(180)


# ── Read helpers (unchanged logic, same signatures) ──────────────────────────

def get_match_history(match_id: str, hours: int = 6):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cutoff_iso = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
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


def get_match_history_for_all(hours: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
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
    # AFTER:
    for (match_id, team), history in team_series.items():
        teams = sorted(list(match_teams[match_id]))
        history.sort(key=lambda r: r["timestamp"])  # ← ADD THIS
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