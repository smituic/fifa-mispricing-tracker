from fastapi import APIRouter, Depends
from collections import defaultdict
from app.core.config import settings
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