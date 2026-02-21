class MispricingEngine:
    """
    Compares Kalshi implied probabilities vs sportsbook consensus fair probabilities.
    Returns spread, EV, and classification.
    """

    THRESHOLD = 0.02  # 2% mispricing threshold

    def analyze_match(self, match: dict, sportsbook_fair: dict):
        results = []

        for outcome in match["outcomes"]:
            team = outcome["team"]

            kalshi_ask_prob = outcome["implied_ask_prob"]
            kalshi_bid_prob = outcome["implied_bid_prob"]

            kalshi_ask_price = (
                outcome["yes_ask"] / 100 if outcome["yes_ask"] else 0
            )
            kalshi_bid_price = (
                outcome["yes_bid"] / 100 if outcome["yes_bid"] else 0
            )

            fair_prob = sportsbook_fair.get(team)

            if fair_prob is None:
                continue

            # Spread vs ask
            spread = round(fair_prob - kalshi_ask_prob, 4)

            # EV at ask price
            ev = round(fair_prob - kalshi_ask_price, 4)

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
                "signal": signal
            })

        return results