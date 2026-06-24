from app.services.kalshi_client import KalshiClient
from app.services.odds_client import OddsClient
from app.services.sportsbook_fair_model import SportsbookConsensusModel
from app.services.mispricing import MispricingEngine
from app.core.config import DEFAULT_SPORT, SPORTS_CONFIG


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
    series_ticker: str = "KXWCGAME",  # legacy; unused, kept for back-compat
    status: str = "open",
    client: KalshiClient | None = None,
    odds_client: OddsClient | None = None,
    sport: str = DEFAULT_SPORT,
):
    print(f"Processing match: {match_id} (sport={sport})")

    _own_client = client is None
    _own_odds = odds_client is None
    if _own_client:
        from app.core.dependencies import get_kalshi_client
        client = get_kalshi_client()
    if _own_odds:
        odds_client = OddsClient(sport=sport)

    fair_model = SportsbookConsensusModel()
    engine = MispricingEngine()

    try:
        event_markets = [m for m in markets if m["event_ticker"] == match_id]
        if not event_markets:
            return {"detail": "Match not found"}

        # ── Sport-aware team identification ─────────────────────────────────
        away_name_resolved = None
        home_name_resolved = None

        if sport == "mlb":
            from app.services.team_name_maps import (
                parse_event_ticker, codes_to_names, SPORT_CODE_SETS,
            )

            parsed = parse_event_ticker(match_id, SPORT_CODE_SETS["mlb"])
            if not parsed:
                print(f"❌ MLB ticker parse failed: {match_id}")
                return {"detail": f"Could not parse MLB event_ticker: {match_id}"}

            away_code, home_code = parsed
            away_name_resolved, home_name_resolved = codes_to_names(
                away_code, home_code, sport="mlb"
            )
            if not (away_name_resolved and home_name_resolved):
                print(f"❌ MLB unknown code(s) in {match_id}: away={away_code} home={home_code}")
                return {
                    "detail": f"Unknown MLB team codes: {away_code}/{home_code}"
                }

            home_team = home_name_resolved
            away_team = away_name_resolved
            # Kalshi convention is away-first display
            clean_title = f"{away_team} vs {home_team}"

        else:
            # FIFA: parse from market title ("Home vs Away Winner?")
            title = event_markets[0].get("title")
            if not title or " vs " not in title:
                return {"detail": "Invalid match title format"}

            clean_title = title.replace(" Winner?", "").strip()
            home_team, away_team = clean_title.split(" vs ")
            home_team = normalize_team_name(home_team)
            away_team = normalize_team_name(away_team)
            clean_title = f"{home_team} vs {away_team}"

        # ── Build grouped outcomes from Kalshi markets ──────────────────────
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

            liquidity_score = clamp_score(
                (volume_score * 0.4 + open_interest_score * 0.4 + spread_score * 0.2) * 10
            )

            # Sport-aware outcome team resolution
            if sport == "mlb":
                from app.services.team_name_maps import resolve_outcome_team
                normalized_team = resolve_outcome_team(
                    market, away_name_resolved, home_name_resolved, sport="mlb"
                )
                if not normalized_team:
                    print(f"⚠️  Could not resolve MLB outcome for market {market.get('ticker')}")
                    continue
            else:
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

        # ── Find sportsbook event ───────────────────────────────────────────
        if sport == "mlb":
            # Exact name match — codes_to_names returned Odds-API-canonical names
            sportsbook_event = next(
                (e for e in sportsbook_events
                 if {e.get("home_team"), e.get("away_team")} == {home_team, away_team}),
                None,
            )
        else:
            sportsbook_event = odds_client.match_event(
                sportsbook_events, home_team, away_team
            )

        print(f"🔍 TRY MATCH ({sport}):", home_team, "vs", away_team)
        if not sportsbook_event:
            print(f"❌ FAILED MATCH ({sport}):", home_team, "vs", away_team)
            return {"detail": "No matching sportsbook event found"}

        # ── Fair probabilities (sport's market_type drives 2-way vs 3-way) ──
        market_type = SPORTS_CONFIG.get(sport, {}).get("market_type", "3way")
        sportsbook_fair = fair_model.compute_fair_probabilities(
            sportsbook_event,
            market_type=market_type,
        )

        if not sportsbook_fair:
            print(f"⚠️  No fair data for {home_team} vs {away_team}")
            return {
                "sport": sport,
                "match_id": match_id,
                "match_title": clean_title,
                "kalshi": {"outcomes": grouped_outcomes},
                "sportsbook": {"fair_probabilities": None},
                "analysis": {"outcomes": []},
            }

        # ── Mispricing engine + scoring ─────────────────────────────────────
        match_obj = {"match": clean_title, "outcomes": grouped_outcomes}
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
                abs(x["expected_value"]) * 0.6
                + x["confidence_score"] * 0.3
                + x["liquidity_score"] * 0.1
            ),
            reverse=True,
        )

        return {
            "sport": sport,
            "match_id": match_id,
            "match_title": clean_title,
            "home_team": home_team,
            "away_team": away_team,
            "kalshi": {"outcomes": grouped_outcomes},
            "sportsbook": {"fair_probabilities": sportsbook_fair},
            "analysis": {"outcomes": analysis},
        }

    finally:
        if _own_client:
            await client.close()