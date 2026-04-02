
from app.core.dependencies import get_kalshi_client
from app.services.odds_client import OddsClient
from app.services.sportsbook_fair_model import SportsbookConsensusModel
from app.services.mispricing import MispricingEngine

print("LOADING MATCH ANALYSIS...")

async def build_match_analysis(
    match_id: str,
    markets: list,
    sportsbook_events: list,
    series_ticker: str = "KXWCGAME",
    status: str = "open",
):
    print(f"Processing match: {match_id}")
    client = get_kalshi_client()
    odds_client = OddsClient()
    fair_model = SportsbookConsensusModel()
    engine = MispricingEngine()

    try:
        # 1️⃣ Get Kalshi markets
        

        

        event_markets = [
            m for m in markets if m["event_ticker"] == match_id
        ]

        if not event_markets:
            return {"detail": "Match not found"}

        grouped_outcomes = []

        for market in event_markets:

            # NEW API fields
            bid = market.get("yes_bid_dollars")
            ask = market.get("yes_ask_dollars")

            bid_prob = float(bid) if bid is not None else None
            ask_prob = float(ask) if ask is not None else None

            # Mid price
            mid_price = (
                (bid_prob + ask_prob) / 2
                if bid_prob is not None and ask_prob is not None
                else None
            )

            # Spread %
            spread_pct = (
                (ask_prob - bid_prob) / ((ask_prob + bid_prob) / 2)
                if bid_prob is not None and ask_prob is not None
                else 0
            )
            volume = float(market.get("volume_fp") or 0)
            open_interest = float(market.get("open_interest_fp") or 0)
            # Liquidity score
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
                "yes_bid": bid_prob,
                "yes_ask": ask_prob,
                "mid_price": round(mid_price, 4) if mid_price else None,
                "spread_pct": round(spread_pct, 4),
                "volume": volume,
                "open_interest": open_interest,
                "liquidity_score": liquidity_score,
                "implied_bid_prob": bid_prob,
                "implied_ask_prob": ask_prob
            })

        title = event_markets[0].get("title")

        if not title or " vs " not in title:
            return {"detail": "Invalid match title format"}

        clean_title = title.replace(" Winner?", "").strip()
        home_team, away_team = clean_title.split(" vs ")

        # 2️⃣ Fetch sportsbook events
        
        
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

        # Add liquidity + confidence to analysis
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

        # Rank opportunities
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