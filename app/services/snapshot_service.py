import sqlite3
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict
from app.services.match_analysis_service import build_match_analysis
from app.core.dependencies import get_kalshi_client

DB_PATH = "fifa_tracker.db"


def init_db():
    conn = sqlite3.connect(DB_PATH)
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


def save_snapshot_row(
    match_id,
    team,
    ask_probability,
    bid_probability,
    mid_price,
    fair_probability,
    expected_value,
    liquidity_score,
    confidence_score,
    volume,
    open_interest,
):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO snapshots (
            timestamp,
            match_id,
            team,
            ask_probability,
            bid_probability,
            mid_price,
            fair_probability,
            expected_value,
            liquidity_score,
            confidence_score,
            volume,
            open_interest
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.utcnow().isoformat(),
        match_id,
        team,
        ask_probability,
        bid_probability,
        mid_price,
        fair_probability,
        expected_value,
        liquidity_score,
        confidence_score,
        volume,
        open_interest,
    ))

    conn.commit()
    conn.close()


async def snapshot_all_matches():
    client = get_kalshi_client()

    try:
        data = await client.get_markets(
            series_ticker="KXWCGAME",
            status="open",
            limit=200,
        )

        markets = data.get("markets", [])
        match_ids = list(set(m["event_ticker"] for m in markets))

        for match_id in match_ids:
            try:
                analysis = await build_match_analysis(match_id)

                if not analysis or "kalshi" not in analysis:
                    continue

                kalshi_outcomes = analysis["kalshi"]["outcomes"]
                analysis_outcomes = analysis["analysis"]["outcomes"]

                for outcome in analysis_outcomes:
                    team = outcome["team"]

                    kalshi_data = next(
                        (o for o in kalshi_outcomes if o["team"] == team),
                        None
                    )

                    if not kalshi_data:
                        continue

                    save_snapshot_row(
                        match_id=match_id,
                        team=team,
                        ask_probability=kalshi_data["implied_ask_prob"],
                        bid_probability=kalshi_data["implied_bid_prob"],
                        mid_price=kalshi_data["mid_price"],
                        fair_probability=outcome["sportsbook_fair_probability"],
                        expected_value=outcome["expected_value"],
                        liquidity_score=outcome["liquidity_score"],
                        confidence_score=outcome["confidence_score"],
                        volume=kalshi_data.get("volume"),
                        open_interest=kalshi_data.get("open_interest"),
                    )

            except Exception as e:
                print(f"Snapshot error for {match_id}: {e}")

    finally:
        await client.close()


async def start_snapshot_loop():
    while True:
        print("Running snapshot cycle...")
        try:
            await snapshot_all_matches()
        except Exception as e:
            print("Snapshot cycle failed:", e)

        await asyncio.sleep(300)


def get_match_history(match_id: str, hours: int = 6):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cutoff_time = datetime.utcnow() - timedelta(hours=hours)
    cutoff_iso = cutoff_time.isoformat()

    cursor.execute("""
        SELECT
            timestamp,
            team,
            ask_probability,
            bid_probability,
            mid_price,
            fair_probability,
            expected_value,
            liquidity_score,
            confidence_score,
            volume,
            open_interest
        FROM snapshots
        WHERE match_id = ?
        AND timestamp >= ?
        ORDER BY timestamp ASC
    """, (match_id, cutoff_iso))

    rows = cursor.fetchall()
    conn.close()

    grouped = {}

    for row in rows:
        (
            timestamp,
            team,
            ask_prob,
            bid_prob,
            mid_price,
            fair_prob,
            ev,
            liquidity_score,
            confidence_score,
            volume,
            open_interest
        ) = row

        signal = "Undervalued" if ev > 0 else "Overvalued"

        if team not in grouped:
            grouped[team] = []

        grouped[team].append({
            "timestamp": timestamp,
            "ask_probability": ask_prob,
            "bid_probability": bid_prob,
            "mid_price": mid_price,
            "fair_probability": fair_prob,
            "expected_value": ev,
            "signal": signal,
            "liquidity_score": liquidity_score,
            "confidence_score": confidence_score,
            "volume": volume,
            "open_interest": open_interest,
        })

    return grouped

def get_match_history_for_all(hours: int):
    conn = sqlite3.connect("fifa_tracker.db")
    cursor = conn.cursor()

    cutoff = datetime.utcnow() - timedelta(hours=hours)

    cursor.execute("""
        SELECT match_id, team, timestamp, expected_value, confidence_score, liquidity_score
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
        teams = sorted(list(match_teams[match_id]))
        match_title = " vs ".join(teams)

        latest = history[-1]

        results.append({
            "match_id": match_id,
            "team": team,
            "match": match_title,
            "ev_series": [row["expected_value"] for row in history],
            "confidence_score": latest["confidence_score"] or 0,
            "liquidity_score": latest["liquidity_score"] or 0,
        })

    return results