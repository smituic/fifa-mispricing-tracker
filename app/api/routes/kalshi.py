from fastapi import APIRouter, Depends
from collections import defaultdict
from app.core.config import settings
from typing import List, Dict, Any
# from app.services.fair_model import FairProbabilityModel
from app.services.mispricing import MispricingEngine
from app.services.sportsbook_fair_model import SportsbookConsensusModel
from app.services.odds_client import OddsClient
from app.services.kalshi_client import KalshiClient

router = APIRouter()


def get_kalshi_client() -> KalshiClient:
    return KalshiClient(base_url=settings.KALSHI_BASE_URL)


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

        # Filter only markets for this event_ticker
        event_markets = [
            m for m in markets if m["event_ticker"] == match_id
        ]

        if not event_markets:
            return {"detail": "Match not found"}

        grouped_outcomes = []

        for market in event_markets:
            yes_bid = market.get("yes_bid") or 0
            yes_ask = market.get("yes_ask") or 0
            volume = market.get("volume") or 0
            open_interest = market.get("open_interest") or 0

            mid_price = (yes_bid + yes_ask) / 200 if (yes_bid and yes_ask) else 0

            spread_pct = (
                (yes_ask - yes_bid) / ((yes_ask + yes_bid) / 2)
                if (yes_bid and yes_ask)
                else 0
            )

            max_volume = 200
            max_open_interest = 200
            max_spread_penalty = 1  # spread_pct usually < 1

            volume_score = min(volume / max_volume, 1)
            open_interest_score = min(open_interest / max_open_interest, 1)
            spread_score = 1 - min(spread_pct / max_spread_penalty, 1)

            normalized_liquidity = (
                volume_score * 0.4 +
                open_interest_score * 0.4 +
                spread_score * 0.2
            )
            liquidity_score = round(normalized_liquidity * 10, 2)
            if liquidity_score < 3:
                liquidity_label = "Low"
            elif liquidity_score < 7:
                liquidity_label = "Moderate"
            else:
                liquidity_label = "High"

            grouped_outcomes.append({
                "team": market.get("yes_sub_title"),
                "yes_bid": yes_bid,
                "yes_ask": yes_ask,
                "mid_price": round(mid_price, 4),
                "spread_pct": round(spread_pct, 4),
                "volume": volume,
                "open_interest": open_interest,
                "liquidity_score": round(liquidity_score, 2),
                "implied_bid_prob": yes_bid / 100 if yes_bid else 0,
                "implied_ask_prob": yes_ask / 100 if yes_ask else 0
            })

        title = event_markets[0].get("title")

        if not title or " vs " not in title:
            return {"detail": "Invalid match title format"}

        clean_title = title.replace(" Winner?", "").strip()
        home_team, away_team = clean_title.split(" vs ")

        # 2️⃣ Fetch sportsbook events
        sportsbook_events = await odds_client.fetch_events()

        sportsbook_event = odds_client.match_event(
            sportsbook_events,
            home_team,
            away_team
        )
        
        if not sportsbook_event:
            return {"detail": "No matching sportsbook event found"}

        sportsbook_fair = fair_model.compute_fair_probabilities(
            sportsbook_event
        )

        if not sportsbook_fair:
            return {"detail": "Unable to compute sportsbook fair probabilities"}

        match_obj = {
            "match": title,
            "outcomes": grouped_outcomes
        }

        analysis = engine.analyze_match(match_obj, sportsbook_fair)

        book_count = len(sportsbook_event.get("bookmakers", []))

        for outcome in analysis:

            # Find matching Kalshi outcome
            kalshi_outcome = next(
                (o for o in grouped_outcomes if o["team"] == outcome["team"]),
                None
            )

            if not kalshi_outcome:
                continue

            spread_pct = kalshi_outcome["spread_pct"]
            liquidity_score = kalshi_outcome["liquidity_score"]

            # --- Normalize Confidence (0–10) ---
            max_books = 10
            book_score = min(book_count / max_books, 1)
            spread_penalty = min(spread_pct / 1, 1)

            normalized_confidence = (
                book_score * 0.7 +
                (1 - spread_penalty) * 0.3
            )

            confidence_score = round(normalized_confidence * 10, 2)

            # --- Labels ---
            if liquidity_score < 3:
                liquidity_label = "Low"
            elif liquidity_score < 7:
                liquidity_label = "Moderate"
            else:
                liquidity_label = "High"

            if confidence_score < 3:
                confidence_label = "Weak"
            elif confidence_score < 7:
                confidence_label = "Medium"
            else:
                confidence_label = "Strong"

            # --- Attach to outcome ---
            outcome["liquidity_score"] = liquidity_score
            outcome["liquidity_label"] = liquidity_label
            outcome["confidence_score"] = confidence_score
            outcome["confidence_label"] = confidence_label
        analysis.sort(
            key=lambda x: (
                abs(x["expected_value"]) * 0.6 +
                x["confidence_score"] * 0.3 +
                x["liquidity_score"] * 0.1
            ),
            reverse=True
        )

        return {
            "match_id": match_id,
            "match_title": clean_title,
            "kalshi": {
                "outcomes": grouped_outcomes
            },
            "sportsbook": {
                "fair_probabilities": sportsbook_fair
            },
            "analysis": {
                "outcomes": analysis
            }
        }

    finally:
        await client.close()
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