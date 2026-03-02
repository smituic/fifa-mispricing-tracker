from app.core.dependencies import get_kalshi_client
from app.services.odds_client import OddsClient
from app.services.sportsbook_fair_model import SportsbookConsensusModel
from app.services.mispricing import MispricingEngine


async def build_match_analysis(
    match_id: str,
    series_ticker: str = "KXWCGAME",
    status: str = "open",
):
    client = get_kalshi_client()
    odds_client = OddsClient()
    fair_model = SportsbookConsensusModel()
    engine = MispricingEngine()

    try:
        # 1️⃣ Get Kalshi markets
        data = await client.get_markets(
            series_ticker=series_ticker,
            status=status,
            limit=200,
        )

        markets = data.get("markets", [])

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
            max_spread_penalty = 1

            volume_score = min(volume / max_volume, 1)
            open_interest_score = min(open_interest / max_open_interest, 1)
            spread_score = 1 - min(spread_pct / max_spread_penalty, 1)

            normalized_liquidity = (
                volume_score * 0.4 +
                open_interest_score * 0.4 +
                spread_score * 0.2
            )
            liquidity_score = round(normalized_liquidity * 10, 2)

            grouped_outcomes.append({
                "team": market.get("yes_sub_title"),
                "yes_bid": yes_bid,
                "yes_ask": yes_ask,
                "mid_price": round(mid_price, 4),
                "spread_pct": round(spread_pct, 4),
                "volume": volume,
                "open_interest": open_interest,
                "liquidity_score": liquidity_score,
                "implied_bid_prob": yes_bid / 100 if yes_bid else 0,
                "implied_ask_prob": yes_ask / 100 if yes_ask else 0
            })

        title = event_markets[0].get("title")

        if not title or " vs " not in title:
            return {"detail": "Invalid match title format"}

        clean_title = title.replace(" Winner?", "").strip()
        home_team, away_team = clean_title.split(" vs ")

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
            kalshi_outcome = next(
                (o for o in grouped_outcomes if o["team"] == outcome["team"]),
                None
            )

            if not kalshi_outcome:
                continue

            spread_pct = kalshi_outcome["spread_pct"]
            liquidity_score = kalshi_outcome["liquidity_score"]

            max_books = 10
            book_score = min(book_count / max_books, 1)
            spread_penalty = min(spread_pct / 1, 1)

            normalized_confidence = (
                book_score * 0.7 +
                (1 - spread_penalty) * 0.3
            )

            confidence_score = round(normalized_confidence * 10, 2)

            outcome["liquidity_score"] = liquidity_score
            outcome["confidence_score"] = confidence_score

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