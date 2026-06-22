from statistics import mean


class SportsbookConsensusModel:
    """
    Computes fair probabilities from sportsbook odds.

    Supports both 3-way markets (e.g., soccer: home / draw / away) and
    2-way markets (e.g., MLB, NFL, NBA, NHL: home / away).

    Removes vig per book (normalize implied probs to sum to 1), then
    averages across books to dampen single-book noise.
    """

    # Expected outcome counts per market type
    EXPECTED_OUTCOMES = {
        "3way": 3,
        "2way": 2,
    }

    def compute_fair_probabilities(self, event: dict, market_type: str = "3way"):
        """
        Args:
            event: an Odds API event dict (has 'bookmakers' key)
            market_type: '3way' for soccer-style, '2way' for moneyline sports.
                         Defaults to '3way' for backward compatibility with
                         the existing FIFA pipeline.

        Returns:
            dict mapping outcome name -> consensus fair probability, or None
            if no usable book data was found.
        """
        if not event:
            return None

        expected_count = self.EXPECTED_OUTCOMES.get(market_type)
        if expected_count is None:
            # Unknown market_type — fail safe
            return None

        books = event.get("bookmakers", [])
        outcome_probs = {}

        for book in books:
            markets = book.get("markets", [])
            if not markets:
                continue

            market = markets[0]

            # h2h ("head-to-head" / moneyline) is what we want for both
            # 2-way and 3-way; the Odds API uses the same key for both.
            if market.get("key") != "h2h":
                continue

            outcomes = market.get("outcomes", [])

            if len(outcomes) != expected_count:
                continue

            implied_probs = {}
            total_implied = 0

            # Convert decimal odds to implied probability
            for o in outcomes:
                try:
                    decimal_odds = float(o["price"])
                except (TypeError, ValueError):
                    continue

                if decimal_odds <= 0:
                    continue

                implied = 1 / decimal_odds
                implied_probs[o["name"]] = implied
                total_implied += implied

            if total_implied == 0:
                continue

            # Remove vig (normalize so implied probabilities sum to 1)
            fair_probs = {
                name: implied / total_implied
                for name, implied in implied_probs.items()
            }

            # Accumulate per outcome across books
            for name, prob in fair_probs.items():
                outcome_probs.setdefault(name, []).append(prob)

        if not outcome_probs:
            return None

        # Consensus = arithmetic mean across all books that quoted
        consensus = {
            name: round(mean(probs), 4)
            for name, probs in outcome_probs.items()
        }

        return consensus