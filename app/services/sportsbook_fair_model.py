from statistics import mean


class SportsbookConsensusModel:
    """
    Computes fair 3-way probabilities from sportsbook odds.
    Removes vig per book, then averages across books.
    """

    def compute_fair_probabilities(self, event: dict):
        if not event:
            return None

        books = event.get("bookmakers", [])
        outcome_probs = {}

        for book in books:
            markets = book.get("markets", [])
            if not markets:
                continue

            market = markets[0]

            if market.get("key") != "h2h":
                continue

            outcomes = market.get("outcomes", [])

            if len(outcomes) != 3:
                continue

            implied_probs = {}
            total_implied = 0

            # Convert decimal odds to implied probability
            for o in outcomes:
                decimal_odds = o["price"]
                implied = 1 / decimal_odds
                implied_probs[o["name"]] = implied
                total_implied += implied

            if total_implied == 0:
                continue

            # Remove vig (normalize)
            fair_probs = {
                name: implied / total_implied
                for name, implied in implied_probs.items()
            }

            # Store per outcome
            for name, prob in fair_probs.items():
                outcome_probs.setdefault(name, []).append(prob)

        if not outcome_probs:
            return None

        # Average across books
        consensus = {
            name: round(mean(probs), 4)
            for name, probs in outcome_probs.items()
        }

        return consensus