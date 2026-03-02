from fastapi import APIRouter, Depends
from collections import defaultdict
from app.core.config import settings
from typing import List, Dict, Any
# from app.services.fair_model import FairProbabilityModel
from app.services.mispricing import MispricingEngine
from app.services.sportsbook_fair_model import SportsbookConsensusModel
from app.core.dependencies import get_kalshi_client
from app.services.odds_client import OddsClient
from app.services.kalshi_client import KalshiClient
from app.services.snapshot_service import get_match_history
from app.services.match_analysis_service import build_match_analysis

router = APIRouter()





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

            grouped[event_ticker].append({
                "team": market.get("yes_sub_title"),
                "yes_bid": market.get("yes_bid"),
                "yes_ask": market.get("yes_ask"),
                "implied_bid_prob": (
                    market.get("yes_bid") / 100 if market.get("yes_bid") else 0
                ),
                "implied_ask_prob": (
                    market.get("yes_ask") / 100 if market.get("yes_ask") else 0
                ),
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

# @router.get("/fifa/analysis")
# async def fifa_analysis(
#     odds_client = OddsClient()
#     events = await odds_client.fetch_events()

#     print("ODDS API RESPONSE SAMPLE:")
#     print(events[:1])

#     return {"status": "tested"}
#         series_ticker: str = "KXWCGAME",
#     status: str = "open",
#     client: KalshiClient = Depends(get_kalshi_client),
# ):
#     try:
#         data = await client.get_markets(
#             series_ticker=series_ticker,
#             status=status,
#             limit=200,
#         )

#         markets = data.get("markets", [])
#         grouped = defaultdict(list)

#         for market in markets:
#             event_ticker = market["event_ticker"]

#             grouped[event_ticker].append({
#                 "team": market.get("yes_sub_title"),
#                 "yes_bid": market.get("yes_bid"),
#                 "yes_ask": market.get("yes_ask"),
#                 "implied_bid_prob": (
#                     market.get("yes_bid") / 100 if market.get("yes_bid") else 0
#                 ),
#                 "implied_ask_prob": (
#                     market.get("yes_ask") / 100 if market.get("yes_ask") else 0
#                 ),
#             })

#         fair_model = FairProbabilityModel()
#         engine = MispricingEngine()

#         response = []

#         for event_ticker, outcomes in grouped.items():

#             title = next(
#                 (m["title"] for m in markets if m["event_ticker"] == event_ticker),
#                 None
#             )

#             match_obj = {
#                 "match": title,
#                 "outcomes": outcomes
#             }

#             fair_probs = fair_model.get_fair_probabilities(match_obj)
#             analysis = engine.analyze_match(match_obj, fair_probs)

#             response.append({
#                 "event_ticker": event_ticker,
#                 "match": title,
#                 "analysis": analysis
#             })

#         return {
#             "series_ticker": series_ticker,
#             "match_count": len(response),
#             "matches": response
#         }

#     finally:
#         await client.close()

@router.get("/fifa/analysis")
async def fifa_analysis(
    series_ticker: str = "KXWCGAME",
    status: str = "open",
    client: KalshiClient = Depends(get_kalshi_client),
):
    try:
        odds_client = OddsClient()
        fair_model = SportsbookConsensusModel()
        engine = MispricingEngine()

        # 1️⃣ Get Kalshi markets
        data = await client.get_markets(
            series_ticker=series_ticker,
            status=status,
            limit=200,
        )

        markets = data.get("markets", [])
        grouped = defaultdict(list)

        for market in markets:
            event_ticker = market["event_ticker"]

            grouped[event_ticker].append({
                "team": market.get("yes_sub_title"),
                "yes_bid": market.get("yes_bid"),
                "yes_ask": market.get("yes_ask"),
                "implied_bid_prob": (
                    market.get("yes_bid") / 100 if market.get("yes_bid") else 0
                ),
                "implied_ask_prob": (
                    market.get("yes_ask") / 100 if market.get("yes_ask") else 0
                ),
            })

        # 2️⃣ Get sportsbook events once
        sportsbook_events = await odds_client.fetch_events()

        response = []

        for event_ticker, outcomes in grouped.items():

            title = next(
                (m["title"] for m in markets if m["event_ticker"] == event_ticker),
                None
            )

            if not title or " vs " not in title:
                continue
            
            # Remove trailing " Winner?"
            clean_title = title.replace(" Winner?", "").strip()
            home_team, away_team = clean_title.split(" vs ")

            # 3️⃣ Match sportsbook event
            sportsbook_event = odds_client.match_event(
                sportsbook_events,
                home_team,
                away_team
            )

            if not sportsbook_event:
                continue

            # 4️⃣ Compute sportsbook fair
            sportsbook_fair = fair_model.compute_fair_probabilities(
                sportsbook_event
            )

            if not sportsbook_fair:
                continue

            match_obj = {
                "match": title,
                "outcomes": outcomes
            }

            # 5️⃣ Run mispricing engine
            analysis = engine.analyze_match(match_obj, sportsbook_fair)

            response.append({
                "event_ticker": event_ticker,
                "match": title,
                "analysis": analysis
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
        odds_client = OddsClient()
        fair_model = SportsbookConsensusModel()
        engine = MispricingEngine()

        # 1️⃣ Get Kalshi markets
        data = await client.get_markets(
            series_ticker=series_ticker,
            status=status,
            limit=200,
        )

        markets = data.get("markets", [])
        grouped = defaultdict(list)

        for market in markets:
            event_ticker = market["event_ticker"]

            grouped[event_ticker].append({
                "team": market.get("yes_sub_title"),
                "yes_bid": market.get("yes_bid"),
                "yes_ask": market.get("yes_ask"),
                "implied_bid_prob": (
                    market.get("yes_bid") / 100 if market.get("yes_bid") else 0
                ),
                "implied_ask_prob": (
                    market.get("yes_ask") / 100 if market.get("yes_ask") else 0
                ),
            })

        sportsbook_events = await odds_client.fetch_events()

        matches = []

        for event_ticker, outcomes in grouped.items():

            title = next(
                (m["title"] for m in markets if m["event_ticker"] == event_ticker),
                None
            )

            if not title or " vs " not in title:
                continue

            clean_title = title.replace(" Winner?", "").strip()
            home_team, away_team = clean_title.split(" vs ")

            sportsbook_event = odds_client.match_event(
                sportsbook_events,
                home_team,
                away_team
            )

            if not sportsbook_event:
                continue

            sportsbook_fair = fair_model.compute_fair_probabilities(
                sportsbook_event
            )

            if not sportsbook_fair:
                continue

            match_obj = {
                "match": title,
                "outcomes": outcomes
            }

            analysis = engine.analyze_match(match_obj, sportsbook_fair)

            # find strongest EV for preview list
            best_outcome = max(
                analysis,
                key=lambda x: abs(x["expected_value"])
            )

            matches.append({
                "match_id": event_ticker,
                "home_team": home_team,
                "away_team": away_team,
                "match_title": clean_title,
                "top_ev": best_outcome["expected_value"],
                "best_signal": best_outcome["signal"]
            })

        return {
            "match_count": len(matches),
            "matches": matches
        }

    finally:
        await client.close()

@router.get("/fifa/match/{match_id}")
async def fifa_match_detail(
    match_id: str,
    series_ticker: str = "KXWCGAME",
    status: str = "open",
):
    return await build_match_analysis(
        match_id=match_id,
        series_ticker=series_ticker,
        status=status,
    )
@router.get("/fifa/top-signals")
async def fifa_top_signals(
    series_ticker: str = "KXWCGAME",
    status: str = "open",
    min_ev: float = 0.01,
    limit: int = 10,
    client: KalshiClient = Depends(get_kalshi_client),
):
    try:
        odds_client = OddsClient()
        fair_model = SportsbookConsensusModel()
        engine = MispricingEngine()

        data = await client.get_markets(
            series_ticker=series_ticker,
            status=status,
            limit=200,
        )

        markets = data.get("markets", [])
        grouped = defaultdict(list)

        for market in markets:
            event_ticker = market["event_ticker"]

            grouped[event_ticker].append({
                "team": market.get("yes_sub_title"),
                "yes_bid": market.get("yes_bid"),
                "yes_ask": market.get("yes_ask"),
                "implied_bid_prob": (
                    market.get("yes_bid") / 100 if market.get("yes_bid") else 0
                ),
                "implied_ask_prob": (
                    market.get("yes_ask") / 100 if market.get("yes_ask") else 0
                ),
            })

        sportsbook_events = await odds_client.fetch_events()

        signals = []

        for event_ticker, outcomes in grouped.items():

            title = next(
                (m["title"] for m in markets if m["event_ticker"] == event_ticker),
                None
            )

            if not title or " vs " not in title:
                continue

            clean_title = title.replace(" Winner?", "").strip()
            home_team, away_team = clean_title.split(" vs ")

            sportsbook_event = odds_client.match_event(
                sportsbook_events,
                home_team,
                away_team
            )

            if not sportsbook_event:
                continue

            sportsbook_fair = fair_model.compute_fair_probabilities(
                sportsbook_event
            )

            if not sportsbook_fair:
                continue

            match_obj = {
                "match": title,
                "outcomes": outcomes
            }

            analysis = engine.analyze_match(match_obj, sportsbook_fair)

            book_count = len(sportsbook_event.get("bookmakers", []))

            for outcome in analysis:

                if outcome["expected_value"] <= min_ev:
                    continue

                # Basic composite score
                composite_score = (
                    abs(outcome["expected_value"]) * 0.6 +
                    outcome.get("confidence_score", 0) * 0.3 +
                    outcome.get("liquidity_score", 0) * 0.1
                )

                signals.append({
                    "match_id": event_ticker,
                    "match": clean_title,
                    "team": outcome["team"],
                    "expected_value": outcome["expected_value"],
                    "confidence_score": outcome.get("confidence_score", 0),
                    "liquidity_score": outcome.get("liquidity_score", 0),
                    "composite_score": round(composite_score, 4)
                })

        # Sort globally
        signals.sort(
            key=lambda x: x["composite_score"],
            reverse=True
        )

        return {
            "signal_count": len(signals),
            "top_signals": signals[:limit]
        }

    finally:
        await client.close()

@router.get("/fifa/match/{match_id}/history")
async def fifa_match_history(match_id: str, hours: int = 6):
    data = get_match_history(match_id, hours)

    return {
        "match_id": match_id,
        "window_hours": hours,
        "teams": data
    }