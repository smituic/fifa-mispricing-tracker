from fastapi import APIRouter, Depends
from app.core.config import settings
from typing import List, Dict, Any
from app.services.snapshot_service import get_match_history_for_all
from app.core.dependencies import get_kalshi_client
from app.services.odds_client import OddsClient
from app.services.kalshi_client import KalshiClient
from app.services.snapshot_service import get_match_history_for_all
from app.services.snapshot_service import get_match_history
from app.services.match_analysis_service import build_match_analysis

router = APIRouter()


odds_client = OddsClient()


@router.get("/fifa/markets")
async def fifa_markets(
    series_ticker: str = "KXWCGAME",
    status: str = "open",
    client: KalshiClient = Depends(get_kalshi_client),
):
    try:
        data = await client.get_markets(
            series_ticker=series_ticker,
            status=status,
            limit=200,
        )

        markets = data.get("markets", [])
        grouped = defaultdict(list)

        for market in markets:
            
            event_ticker = market["event_ticker"]

            bid = market.get("yes_bid_dollars")
            ask = market.get("yes_ask_dollars")

            bid = float(bid) if bid is not None else None
            ask = float(ask) if ask is not None else None

            grouped[event_ticker].append({
                "team": market.get("yes_sub_title"),
                "yes_bid": bid,
                "yes_ask": ask,
                "implied_bid_prob": bid,
                "implied_ask_prob": ask,
            })

        response = []

        for event_ticker, outcomes in grouped.items():

            total_bid = sum(o["implied_bid_prob"] for o in outcomes)
            total_ask = sum(o["implied_ask_prob"] for o in outcomes)

            title = next(
                (m["title"] for m in markets if m["event_ticker"] == event_ticker),
                None
            )

            response.append({
                "event_ticker": event_ticker,
                "match": title,
                "total_bid_prob": round(total_bid, 4),
                "total_ask_prob": round(total_ask, 4),
                "overround_bid": round(total_bid - 1, 4),
                "overround_ask": round(total_ask - 1, 4),
                "outcomes": outcomes
            })

        return {
            "series_ticker": series_ticker,
            "match_count": len(response),
            "matches": response
        }

    finally:
        await client.close()


@router.get("/fifa/analysis")
async def fifa_analysis(
    series_ticker: str = "KXWCGAME",
    status: str = "open",
    client: KalshiClient = Depends(get_kalshi_client),
):
    try:
        data = await client.get_markets(
            series_ticker=series_ticker,
            status=status,
            limit=200,
        )

        markets = data.get("markets", [])
        sportsbook_events = await odds_client.fetch_events()

        event_tickers = sorted(list(set(m["event_ticker"] for m in markets)))
        response = []

        for event_ticker in event_tickers:
            full_analysis = await build_match_analysis(
                match_id=event_ticker,
                markets=markets,
                sportsbook_events=sportsbook_events,
                client=client,
                odds_client=odds_client,
            )

            if (
                not full_analysis
                or "analysis" not in full_analysis
                or "kalshi" not in full_analysis
            ):
                continue

            response.append({
                "event_ticker": event_ticker,
                "match": full_analysis["match_title"],
                "analysis": full_analysis["analysis"]["outcomes"],
            })

        return {
            "match_count": len(response),
            "matches": response
        }

    finally:
        await client.close()

@router.get("/fifa/matches")
async def fifa_matches(
    series_ticker: str = "KXWCGAME",
    status: str = "open",
    client: KalshiClient = Depends(get_kalshi_client),
):
    try:
        data = await client.get_markets(
            series_ticker=series_ticker,
            status=status,
            limit=200,
        )

        markets = data.get("markets", [])
        sportsbook_events = await odds_client.fetch_events()

        event_tickers = sorted(list(set(m["event_ticker"] for m in markets)))
        matches = []

        for event_ticker in event_tickers:
            full_analysis = await build_match_analysis(
                match_id=event_ticker,
                markets=markets,
                sportsbook_events=sportsbook_events,
                client=client,
                odds_client=odds_client,
            )

            if (
                not full_analysis
                or "analysis" not in full_analysis
                or "kalshi" not in full_analysis
            ):
                continue

            analysis_outcomes = full_analysis["analysis"]["outcomes"]
            match_title = full_analysis["match_title"]
            home_team, away_team = match_title.split(" vs ")

            positive_outcomes = [
                o for o in analysis_outcomes if o["expected_value"] > 0
            ]

            if positive_outcomes:
                best_outcome = max(
                    positive_outcomes,
                    key=lambda x: x["expected_value"]
                )
                top_ev = best_outcome["expected_value"]
                best_signal = best_outcome["signal"]
            else:
                top_ev = None
                best_signal = "No edge"

            matches.append({
                "match_id": event_ticker,
                "home_team": home_team,
                "away_team": away_team,
                "match_title": match_title,
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
            "match_count": len(matches),
            "matches": matches
        }

    finally:
        await client.close()
# AFTER (fixed):
@router.get("/fifa/match/{match_id}")
async def fifa_match_detail(
    match_id: str,
    series_ticker: str = "KXWCGAME",
    status: str = "open",
    client: KalshiClient = Depends(get_kalshi_client),
):
    try:
        data = await client.get_markets(
            series_ticker=series_ticker,
            status=status,
            limit=200,
        )
        markets = data.get("markets", [])
        sportsbook_events = await odds_client.fetch_events()

        return await build_match_analysis(
            match_id=match_id,
            markets=markets,
            sportsbook_events=sportsbook_events,
            client=client,
            odds_client=odds_client,
        )
    finally:
        await client.close()
        
@router.get("/fifa/top-signals")
async def fifa_top_signals(
    min_ev: float = 0.0,
    limit: int = 10,
    hours: int = 6,
):
    data = get_match_history_for_all(hours)

    signals = []

    for team_data in data:
        ev_series = team_data.get("ev_series", [])

        if not ev_series:
            continue

        latest_ev = ev_series[-1]

        if abs(latest_ev) <= min_ev:
            continue

        composite_score = (
            abs(latest_ev) * 0.6 +
            team_data.get("confidence_score", 0) * 0.3 +
            team_data.get("liquidity_score", 0) * 0.1
        )

        signals.append({
            "match_id": team_data["match_id"],
            "match": team_data["match"],
            "team": team_data["team"],
            "expected_value": latest_ev,
            "confidence_score": team_data.get("confidence_score", 0),
            "liquidity_score": team_data.get("liquidity_score", 0),
            "composite_score": round(composite_score, 4),
        })

    signals.sort(key=lambda x: x["composite_score"], reverse=True)

    return {
        "signal_count": len(signals),
        "top_signals": signals[:limit]
    }

@router.get("/fifa/match/{match_id}/history")
async def fifa_match_history(match_id: str, hours: int = 6):
    data = get_match_history(match_id, hours)

    return {
        "match_id": match_id,
        "window_hours": hours,
        "teams": data
    }

@router.get("/fifa/ev-movers")
async def fifa_ev_movers(hours: int = 6, limit: int = 10):

    data = get_match_history_for_all(hours)

    movers = []

    for team_data in data:

        ev_series = team_data["ev_series"]

        if len(ev_series) < 2:
            continue

        ev_change = ev_series[-1] - ev_series[0]

        movers.append({
            "match_id": team_data["match_id"],
            "match": team_data["match"],
            "team": team_data["team"],
            "ev_change": ev_change
        })

        movers = sorted(
            movers,
            key=lambda x: abs(x["ev_change"]),
            reverse=True
        )

    movers.sort(
        key=lambda x: abs(x["ev_change"]),
        reverse=True
    )

    return {
        "count": len(movers),
        "movers": movers[:limit]
    }

@router.get("/fifa/opportunities")
async def fifa_opportunities(
    series_ticker: str = "KXWCGAME",
    status: str = "open",
    client: KalshiClient = Depends(get_kalshi_client),
):
    try:
        data = await client.get_markets(
            series_ticker=series_ticker,
            status=status,
            limit=200,
        )

        markets = data.get("markets", [])
        sportsbook_events = await odds_client.fetch_events()

        event_tickers = sorted(list(set(m["event_ticker"] for m in markets)))

        positive_opportunities = []
        negative_opportunities = []

        for event_ticker in event_tickers:
            full_analysis = await build_match_analysis(
                match_id=event_ticker,
                markets=markets,
                sportsbook_events=sportsbook_events,
                client=client,
                odds_client=odds_client,
            )

            if (
                not full_analysis
                or "analysis" not in full_analysis
                or "kalshi" not in full_analysis
            ):
                continue

            match_title = full_analysis["match_title"]
            analysis_outcomes = full_analysis["analysis"]["outcomes"]

            if not analysis_outcomes:
                continue

            positive_outcomes = [
                o for o in analysis_outcomes if o["expected_value"] > 0
            ]

            if positive_outcomes:
                best_outcome = max(
                    positive_outcomes,
                    key=lambda x: x["expected_value"]
                )

                positive_opportunities.append({
                    "match_id": event_ticker,
                    "match_title": match_title,
                    "outcome_team": best_outcome["team"],
                    "expected_value": best_outcome["expected_value"],
                    "signal": best_outcome["signal"],
                    "confidence_score": best_outcome.get("confidence_score", 0),
                    "liquidity_score": best_outcome.get("liquidity_score", 0),
                })
            else:
                worst_outcome = min(
                    analysis_outcomes,
                    key=lambda x: x["expected_value"]
                )

                negative_opportunities.append({
                    "match_id": event_ticker,
                    "match_title": match_title,
                    "outcome_team": worst_outcome["team"],
                    "expected_value": worst_outcome["expected_value"],
                    "signal": worst_outcome["signal"],
                    "confidence_score": worst_outcome.get("confidence_score", 0),
                    "liquidity_score": worst_outcome.get("liquidity_score", 0),
                })

        positive_opportunities.sort(
            key=lambda x: x["expected_value"],
            reverse=True
        )
        negative_opportunities.sort(
            key=lambda x: x["expected_value"]
        )

        best_positive = positive_opportunities[0] if positive_opportunities else None

        return {
            "best_positive": best_positive,
            "positive_count": len(positive_opportunities),
            "negative_count": len(negative_opportunities),
            "positive_opportunities": positive_opportunities,
            "negative_opportunities": negative_opportunities,
        }

    finally:
        await client.close()