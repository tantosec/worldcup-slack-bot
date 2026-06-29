from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    def predict_match(
        self,
        home_team: str,
        away_team: str,
        stage: str,
        home_prob: float,
        draw_prob: float,
        away_prob: float,
    ) -> dict:
        """Return {"home": int, "away": int, "reasoning": str} or raise."""
        ...

    def predict_tournament_picks(
        self,
        zebra_bold: list[str],
        zebra_wildcard: list[str],
        players: list[str],
    ) -> dict:
        """Return tournament picks dict or raise."""
        ...
