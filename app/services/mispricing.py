from app.core.config import settings
import random

class MispricingEngine:
    """
    Compares Kalshi implied probabilities vs sportsbook consensus fair probabilities.
    Returns spread, EV, and classification.
    """

    THRESHOLD = settings.MIN_EV_SIGNAL


    def analyze_match(self, match: dict, sportsbook_fair: dict):
        results = []

        for outcome in match["outcomes"]:
            team = outcome["team"]

            # Kalshi probabilities already normalized (0–1)
            kalshi_ask_prob = outcome["implied_ask_prob"]
            kalshi_bid_prob = outcome["implied_bid_prob"]

            fair_prob = sportsbook_fair.get(team)

            if fair_prob is None or kalshi_ask_prob is None:
                continue

            # Spread vs ask
            spread = round(fair_prob - kalshi_ask_prob, 4)

            # EV (same as spread since price == probability)
            ev = spread

            # Dev testing: inject artificial mispricing
            if settings.DEV_MODE:
                ev += random.uniform(-0.03, 0.03)
                spread = ev


            # Classification
            if spread > self.THRESHOLD:
                signal = "Undervalued"
            elif spread < -self.THRESHOLD:
                signal = "Overvalued"
            else:
                signal = "Fair"

            results.append({
                "team": team,
                "kalshi_ask_probability": kalshi_ask_prob,
                "sportsbook_fair_probability": round(fair_prob, 4),
                "spread": spread,
                "expected_value": ev,
                "signal": signal,
                "confidence_score": outcome.get("confidence_score", 0),
                "liquidity_score": outcome.get("liquidity_score", 0)
            })

        return results