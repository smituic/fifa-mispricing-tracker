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
        KALSHI_NAME_MAP = {
            "Tie": "Draw",
            "IR Iran": "Iran",
            "Curacao": "Curaçao",  # sportsbook uses the accented version
        }

        # AFTER:
        for outcome in match["outcomes"]:
            team = outcome["team"]
            team = KALSHI_NAME_MAP.get(team, team)

            kalshi_ask_prob = outcome["implied_ask_prob"]
            kalshi_bid_prob = outcome["implied_bid_prob"]
            kalshi_mid = outcome.get("mid_price") or kalshi_ask_prob  # ← use mid

            fair_prob = sportsbook_fair.get(team)

            if fair_prob is None or kalshi_ask_prob is None:
                continue

            spread = round(fair_prob - kalshi_mid, 4)  # ← compare vs mid
            ev = spread

            
            


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