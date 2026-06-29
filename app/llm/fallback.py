import logging
from app.football import stage_label

logger = logging.getLogger(__name__)


class FallbackProvider:
    """Odds-based dumb picks — no LLM, used when all providers fail."""

    def predict_match(self, home_team, away_team, stage, home_prob, draw_prob, away_prob):
        if home_prob > away_prob and home_prob > draw_prob:
            home, away = 1, 0
        elif away_prob > home_prob and away_prob > draw_prob:
            home, away = 0, 1
        else:
            home, away = 0, 0
        return {
            "home": home,
            "away": away,
            "reasoning": None,
        }

    def predict_tournament_picks(self, zebra_bold, zebra_wildcard, players):
        return {
            "winner": "Brazil",
            "golden_boot": players[0] if players else "Unknown",
            "semi1": "Brazil", "semi2": "France", "semi3": "Argentina", "semi4": "Spain",
            "zebra": zebra_bold[0] if zebra_bold else (zebra_wildcard[0] if zebra_wildcard else None),
            "zebra_tier": "BOLD" if zebra_bold else "WILDCARD",
            "group_goals_guess": 180,
            "reasoning": None,
        }
