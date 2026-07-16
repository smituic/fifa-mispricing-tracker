"""
Archive mode: reconstruct route responses from stored snapshots.

When a sport's status is 'archive', its Kalshi markets are closed and the
live path returns nothing. These functions rebuild the same response shapes
from the latest snapshot per (match_id, team), joined to match_metadata for
titles the snapshots table doesn't store.

Response shapes intentionally mirror the live routes exactly, so the frontend
needs no data-layer changes.
"""
import sqlite3

from app.core.config import settings
from app.services.match_analysis_service import confidence_label, liquidity_label
from app.services.snapshot_service import DB_PATH


def _signal_from_ev(ev: float | None) -> str:
    """Mirror MispricingEngine's classification thresholds."""
    if ev is None:
        return "Fair"
    if ev > settings.MIN_EV_SIGNAL:
        return "Undervalued"
    if ev < -settings.MIN_EV_SIGNAL:
        return "Overvalued"
    return "Fair"


def _spread_pct(bid: float | None, ask: float | None) -> float:
    """Recompute spread — snapshots store bid/ask but not spread_pct."""
    if bid is None or ask is None:
        return 0.0
    mid = (bid + ask) / 2
    if mid == 0:
        return 0.0
    return round((ask - bid) / mid, 4)


def _latest_snapshots(sport: str, match_id: str | None = None) -> dict[str, list[dict]]:
    """Latest snapshot row per (match_id, team), grouped by match_id."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    sql = """
        SELECT match_id, team, ask_probability, bid_probability, mid_price,
               fair_probability, expected_value, liquidity_score,
               confidence_score, volume, open_interest, timestamp
        FROM (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY match_id, team ORDER BY timestamp DESC
                   ) AS rn
            FROM snapshots
            WHERE sport = ?
    """
    params: list = [sport]
    if match_id:
        sql += " AND match_id = ?"
        params.append(match_id)
    sql += ") WHERE rn = 1"

    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()

    grouped: dict[str, list[dict]] = {}
    for r in rows:
        (mid, team, ask, bid, mid_price, fair, ev, liq, conf,
         volume, oi, ts) = r
        grouped.setdefault(mid, []).append({
            "team": team,
            "ask_probability": ask,
            "bid_probability": bid,
            "mid_price": mid_price,
            "fair_probability": fair,
            "expected_value": ev,
            "liquidity_score": liq or 0,
            "confidence_score": conf or 0,
            "volume": volume,
            "open_interest": oi,
            "timestamp": ts,
        })
    return grouped


def _metadata(sport: str) -> dict[str, dict]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT match_id, title, home_team, away_team FROM match_metadata WHERE sport = ?",
        (sport,),
    )
    rows = cursor.fetchall()
    conn.close()
    return {
        mid: {"title": title, "home_team": home, "away_team": away}
        for mid, title, home, away in rows
    }


def get_archive_matches(sport: str) -> dict:
    """Archive equivalent of /kalshi/{sport}/matches."""
    snaps = _latest_snapshots(sport)
    meta = _metadata(sport)

    matches = []
    for match_id, outcomes in snaps.items():
        m = meta.get(match_id)
        if not m or not m.get("title"):
            continue  # no title — can't render it meaningfully

        positive = [o for o in outcomes if (o["expected_value"] or 0) > 0]
        if positive:
            best = max(positive, key=lambda x: x["expected_value"])
            top_ev = best["expected_value"]
            best_signal = _signal_from_ev(top_ev)
        else:
            top_ev = None
            best_signal = "No edge"

        matches.append({
            "match_id": match_id,
            "sport": sport,
            "home_team": m.get("home_team") or "",
            "away_team": m.get("away_team") or "",
            "match_title": m["title"],
            "top_ev": top_ev,
            "best_signal": best_signal,
        })

    matches.sort(
        key=lambda x: (
            -(x["top_ev"] if x["top_ev"] is not None else -999),
            x["match_title"],
        )
    )

    return {
        "sport": sport,
        "archive": True,
        "match_count": len(matches),
        "matches": matches,
    }


def get_archive_opportunities(sport: str) -> dict:
    """Archive equivalent of /kalshi/{sport}/opportunities."""
    snaps = _latest_snapshots(sport)
    meta = _metadata(sport)

    positive_opportunities = []
    negative_opportunities = []

    for match_id, outcomes in snaps.items():
        m = meta.get(match_id)
        if not m or not m.get("title"):
            continue
        if not outcomes:
            continue

        def shape(o: dict) -> dict:
            liq = o["liquidity_score"]
            conf = o["confidence_score"]
            return {
                "match_id": match_id,
                "sport": sport,
                "match_title": m["title"],
                "outcome_team": o["team"],
                "expected_value": o["expected_value"],
                "signal": _signal_from_ev(o["expected_value"]),
                "confidence_score": conf,
                "confidence_label": confidence_label(conf),
                "liquidity_score": liq,
                "liquidity_label": liquidity_label(liq),
            }

        positive = [o for o in outcomes if (o["expected_value"] or 0) > 0]
        if positive:
            positive_opportunities.append(
                shape(max(positive, key=lambda x: x["expected_value"]))
            )
        else:
            negative_opportunities.append(
                shape(min(outcomes, key=lambda x: x["expected_value"] or 0))
            )

    positive_opportunities.sort(key=lambda x: x["expected_value"], reverse=True)
    negative_opportunities.sort(key=lambda x: x["expected_value"])

    return {
        "sport": sport,
        "archive": True,
        "best_positive": positive_opportunities[0] if positive_opportunities else None,
        "positive_count": len(positive_opportunities),
        "negative_count": len(negative_opportunities),
        "positive_opportunities": positive_opportunities,
        "negative_opportunities": negative_opportunities,
    }


def get_archive_match_detail(sport: str, match_id: str) -> dict:
    """Archive equivalent of /kalshi/{sport}/match/{match_id}."""
    snaps = _latest_snapshots(sport, match_id)
    outcomes = snaps.get(match_id)
    if not outcomes:
        return {"detail": "Match not found"}

    m = _metadata(sport).get(match_id, {})

    kalshi_outcomes = []
    fair_probabilities = {}
    analysis_outcomes = []

    for o in outcomes:
        bid = o["bid_probability"]
        ask = o["ask_probability"]
        liq = o["liquidity_score"]
        conf = o["confidence_score"]

        kalshi_outcomes.append({
            "team": o["team"],
            "yes_bid": bid,
            "yes_ask": ask,
            "mid_price": o["mid_price"],
            "spread_pct": _spread_pct(bid, ask),
            "volume": o["volume"],
            "open_interest": o["open_interest"],
            "liquidity_score": liq,
            "liquidity_label": liquidity_label(liq),
            "implied_bid_prob": bid,
            "implied_ask_prob": ask,
        })

        if o["fair_probability"] is not None:
            fair_probabilities[o["team"]] = o["fair_probability"]

        ev = o["expected_value"]
        analysis_outcomes.append({
            "team": o["team"],
            "kalshi_ask_probability": ask,
            "sportsbook_fair_probability": o["fair_probability"],
            "spread": ev,
            "expected_value": ev,
            "signal": _signal_from_ev(ev),
            "confidence_score": conf,
            "confidence_label": confidence_label(conf),
            "liquidity_score": liq,
            "liquidity_label": liquidity_label(liq),
        })

    analysis_outcomes.sort(
        key=lambda x: (
            abs(x["expected_value"] or 0) * 0.6
            + (x["confidence_score"] or 0) * 0.3
            + (x["liquidity_score"] or 0) * 0.1
        ),
        reverse=True,
    )

    return {
        "sport": sport,
        "archive": True,
        "match_id": match_id,
        "match_title": m.get("title") or match_id,
        "home_team": m.get("home_team") or "",
        "away_team": m.get("away_team") or "",
        "kalshi": {"outcomes": kalshi_outcomes},
        "sportsbook": {"fair_probabilities": fair_probabilities or None},
        "analysis": {"outcomes": analysis_outcomes},
    }