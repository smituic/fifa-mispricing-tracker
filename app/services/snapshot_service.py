import sqlite3
import asyncio
from datetime import datetime, timedelta
from app.services.match_analysis_service import build_match_analysis
from app.core.dependencies import get_kalshi_client
from datetime import datetime

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
            confidence_score REAL
        )
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
            confidence_score
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

                # Skip if match failed
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

        await asyncio.sleep(60)




def get_match_history(match_id: str, hours: int = 6):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cutoff_time = datetime.utcnow() - timedelta(hours=hours)
    cutoff_iso = cutoff_time.isoformat()

    cursor.execute("""
        SELECT timestamp,
               team,
               ask_probability,
               fair_probability,
               expected_value
        FROM snapshots
        WHERE match_id = ?
        AND timestamp >= ?
        ORDER BY timestamp ASC
    """, (match_id, cutoff_iso))

    rows = cursor.fetchall()
    conn.close()

    grouped = {}

    for row in rows:
        timestamp, team, ask_prob, fair_prob, ev = row

        if team not in grouped:
            grouped[team] = []

        grouped[team].append({
            "timestamp": timestamp,
            "ask_probability": ask_prob,
            "fair_probability": fair_prob,
            "expected_value": ev,
        })

    return grouped