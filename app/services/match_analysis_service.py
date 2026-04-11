from app.services.kalshi_client import KalshiClient
from app.services.odds_client import OddsClient
from app.services.sportsbook_fair_model import SportsbookConsensusModel
from app.services.mispricing import MispricingEngine

print("LOADING MATCH ANALYSIS...")

TEAM_NAME_MAP = {
    "IR Iran": "Iran",
    "Korea Republic": "South Korea",
    "DR Congo": "Congo",
    "Curaçao": "Curacao",
    "Côte d'Ivoire": "Ivory Coast",
    "Bosnia & Herzegovina": "Bosnia",
    "Tie": "Draw",
    "Draw": "Draw",
}

def normalize_team_name(name: str) -> str:
    if not name:
        return name
    return TEAM_NAME_MAP.get(name, name)

def clamp_score(value: float | int | None) -> float:
    if value is None:
        return 0.0
    return round(max(0.0, min(10.0, float(value))), 2)


def confidence_label(score: float) -> str:
    if score < 2.5:
        return "Very Low"
    if score < 4.5:
        return "Low"
    if score < 6.5:
        return "Moderate"
    if score < 8.5:
        return "High"
    return "Very High"


def liquidity_label(score: float) -> str:
    if score < 2.5:
        return "Very Thin"
    if score < 4.5:
        return "Thin"
    if score < 6.5:
        return "Tradable"
    if score < 8.5:
        return "Liquid"
    return "Deep"


async def build_match_analysis(
    match_id: str,
    markets: list,
    sportsbook_events: list,
    series_ticker: str = "KXWCGAME",
    status: str = "open",
    client: KalshiClient | None = None,       # ✅ accept shared client
    odds_client: OddsClient | None = None,    # ✅ accept shared client
):
    print(f"Processing match: {match_id}")

    # ✅ Only create clients if not passed in (keeps backward compat)
    _own_client = client is None
    _own_odds = odds_client is None
    if _own_client:
        from app.core.dependencies import get_kalshi_client
        client = get_kalshi_client()
    if _own_odds:
        odds_client = OddsClient()

    fair_model = SportsbookConsensusModel()
    engine = MispricingEngine()

    try:
        event_markets = [m for m in markets if m["event_ticker"] == match_id]

        if not event_markets:
            return {"detail": "Match not found"}

        grouped_outcomes = []

        for market in event_markets:
            bid = market.get("yes_bid_dollars")
            ask = market.get("yes_ask_dollars")

            bid_prob = float(bid) if bid is not None else None
            ask_prob = float(ask) if ask is not None else None

            mid_price = (
                (bid_prob + ask_prob) / 2
                if bid_prob is not None and ask_prob is not None
                else None
            )

            spread_pct = (
                (ask_prob - bid_prob) / ((ask_prob + bid_prob) / 2)
                if bid_prob is not None and ask_prob is not None
                else 0
            )

            volume = float(market.get("volume_fp") or 0)
            open_interest = float(market.get("open_interest_fp") or 0)

            volume_score = min(volume / 200, 1)
            open_interest_score = min(open_interest / 200, 1)
            spread_score = 1 - min(spread_pct / 1, 1)
            liquidity_score = round(
                (volume_score * 0.4 + open_interest_score * 0.4 + spread_score * 0.2) * 10, 2
            )
            raw_team = market.get("yes_sub_title")
            normalized_team = normalize_team_name(raw_team)

            if normalized_team == "Tie":
                normalized_team = "Draw"

            liquidity_score = clamp_score(
                (volume_score * 0.4 + open_interest_score * 0.4 + spread_score * 0.2) * 10
            )

            raw_team = market.get("yes_sub_title")
            normalized_team = normalize_team_name(raw_team)

            if normalized_team == "Tie":
                normalized_team = "Draw"

            grouped_outcomes.append({
                "team": normalized_team,
                "yes_bid": bid_prob,
                "yes_ask": ask_prob,
                "mid_price": round(mid_price, 4) if mid_price is not None else None,
                "spread_pct": round(spread_pct, 4),
                "volume": volume,
                "open_interest": open_interest,
                "liquidity_score": liquidity_score,
                "liquidity_label": liquidity_label(liquidity_score),
                "implied_bid_prob": bid_prob,
                "implied_ask_prob": ask_prob,
            })

        title = event_markets[0].get("title")
        if not title or " vs " not in title:
            return {"detail": "Invalid match title format"}

        clean_title = title.replace(" Winner?", "").strip()
        home_team, away_team = clean_title.split(" vs ")

        home_team = normalize_team_name(home_team)
        away_team = normalize_team_name(away_team)
        clean_title = f"{home_team} vs {away_team}"

        sportsbook_event = odds_client.match_event(
            sportsbook_events, home_team, away_team
        )
        print("🔍 TRY MATCH:", home_team, "vs", away_team)
        if not sportsbook_event:
            print("❌ FAILED MATCH:", home_team, "vs", away_team)
            return {"detail": "No matching sportsbook event found"}

        sportsbook_fair = fair_model.compute_fair_probabilities(sportsbook_event)
        if not sportsbook_fair:
            print(f"⚠️ No fair data for {home_team} vs {away_team}")

            return {
                "match_id": match_id,
                "match_title": clean_title,
                "kalshi": {"outcomes": grouped_outcomes},
                "sportsbook": {"fair_probabilities": None},
                "analysis": {"outcomes": []},
            }

        match_obj = {"match": title, "outcomes": grouped_outcomes}
        analysis = engine.analyze_match(match_obj, sportsbook_fair)

        book_count = len(sportsbook_event.get("bookmakers", []))

        for outcome in analysis:
            kalshi_outcome = next(
                (o for o in grouped_outcomes if o["team"] == outcome["team"]), None
            )
            if not kalshi_outcome:
                continue

            spread_pct = kalshi_outcome["spread_pct"]
            liq_score = clamp_score(kalshi_outcome["liquidity_score"])

            book_score = min(book_count / 10, 1)
            spread_penalty = min(spread_pct / 1, 1)

            conf_score = clamp_score(
                (book_score * 0.7 + (1 - spread_penalty) * 0.3) * 10
            )

            outcome["liquidity_score"] = liq_score
            outcome["liquidity_label"] = liquidity_label(liq_score)
            outcome["confidence_score"] = conf_score
            outcome["confidence_label"] = confidence_label(conf_score)

        analysis.sort(
            key=lambda x: (
                abs(x["expected_value"]) * 0.6 +
                x["confidence_score"] * 0.3 +
                x["liquidity_score"] * 0.1
            ),
            reverse=True,
        )

        return {
            "match_id": match_id,
            "match_title": clean_title,
            "home_team": home_team,
            "away_team": away_team,
            "kalshi": {"outcomes": grouped_outcomes},
            "sportsbook": {"fair_probabilities": sportsbook_fair},
            "analysis": {"outcomes": analysis},
        }

    finally:
        # ✅ Only close clients we created ourselves
        if _own_client:
            await client.close()
        # OddsClient likely has no async close, skip it