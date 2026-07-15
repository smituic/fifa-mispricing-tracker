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
                # Kalshi prefixes knockout-match subtitles with "Reg Time: "
                # (e.g. "Reg Time: Spain") because knockouts continue into
                # extra time and penalties. The tradeable market is still the
                # 90-minute result, which is exactly what the Odds API's 3-way
                # h2h prices — so strip the prefix and compare directly.
                if raw_team and raw_team.startswith("Reg Time: "):
                    raw_team = raw_team[len("Reg Time: "):]
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
            # Match on team pair AND game date. Multiple events with the same
            # team pair can appear in the Odds API response when consecutive-day
            # series games are both listed (and the in-progress game stays in
            # the feed with live odds until it ends). Without date filtering
            # we'd grab today's live-odds event for tomorrow's Kalshi market.
            from datetime import datetime, timedelta

            target_pair = {home_team, away_team}

            # Pull date from event_ticker: KXMLBGAME-26JUN251210KCTB -> 2026-06-25
            # Pull full game datetime from event_ticker.
            # KXMLBGAME-26JUN241910CHCNYM -> 2026-06-24 19:10 US/Eastern.
            # Kalshi MLB tickers consistently use Eastern; we convert to UTC
            # for direct comparison with Odds API commence_time.
            kalshi_dt_utc = None
            try:
                from app.services.team_name_maps import _TICKER_DATE_RE
                from zoneinfo import ZoneInfo

                body = match_id.split("-", 1)[1]
                m = _TICKER_DATE_RE.match(body)
                if m:
                    yy, mmm, dd, hhmm, _codes = m.groups()
                    if hhmm:
                        kalshi_dt_local = datetime.strptime(
                            f"20{yy}-{mmm}-{dd} {hhmm}",
                            "%Y-%b-%d %H%M",
                        ).replace(tzinfo=ZoneInfo("America/New_York"))
                        kalshi_dt_utc = kalshi_dt_local.astimezone(ZoneInfo("UTC"))
                    else:
                        # No time in ticker — fall back to noon ET that day
                        kalshi_dt_local = datetime.strptime(
                            f"20{yy}-{mmm}-{dd} 1200",
                            "%Y-%b-%d %H%M",
                        ).replace(tzinfo=ZoneInfo("America/New_York"))
                        kalshi_dt_utc = kalshi_dt_local.astimezone(ZoneInfo("UTC"))
            except Exception as e:
                print(f"⚠️  Could not parse datetime from {match_id}: {e}")

            candidates = [
                e for e in sportsbook_events
                if {e.get("home_team"), e.get("away_team")} == target_pair
            ]

            if kalshi_dt_utc and candidates:
                # Match within ±6 hours of game time. Tight enough to rule out
                # consecutive-day series games (and live in-game odds from
                # earlier games), loose enough to handle minor schedule shifts.
                MATCH_WINDOW = timedelta(hours=6)

                def _time_distance(ev):
                    ct = ev.get("commence_time")
                    if not ct:
                        return timedelta(days=999)
                    try:
                        ev_dt = datetime.fromisoformat(ct.replace("Z", "+00:00"))
                        return abs(ev_dt - kalshi_dt_utc)
                    except Exception:
                        return timedelta(days=999)

                candidates.sort(key=_time_distance)
                if _time_distance(candidates[0]) <= MATCH_WINDOW:
                    sportsbook_event = candidates[0]
                else:
                    # No candidate within the window — the Odds API hasn't
                    # priced this future game yet, OR all matches are live
                    # in-game snapshots of an earlier game in the series.
                    sportsbook_event = None
            elif candidates:
                # No time parsed — fall back to first match (preserves FIFA
                # behavior; FIFA tickers don't have time segments).
                sportsbook_event = candidates[0]
            else:
                sportsbook_event = None
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

        # Loud failure: we had fair probabilities but matched no outcomes,
        # which means Kalshi outcome labels don't align with Odds API team
        # names. Silent [] here is how the "Reg Time:" prefix bug went
        # unnoticed through the entire knockout stage.
        if not analysis and grouped_outcomes:
            print(
                f"⚠️  {sport}: 0 outcomes matched for {clean_title} — "
                f"kalshi={[o['team'] for o in grouped_outcomes]} "
                f"fair={list(sportsbook_fair.keys())}"
            )

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