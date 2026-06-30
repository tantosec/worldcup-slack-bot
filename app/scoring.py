from app.fifa_rankings import get_rank
from app.odds import get_underdog
from app.config import (
    STAGE_MULTIPLIERS,
    TOURNAMENT_PICK_POINTS,
    SEMI_PICK_POINTS,
    GROUP_GOALS_WIN_POINTS,
    GROUP_GOALS_NEAR_POINTS,
    ZEBRA_POINTS,
    ZEBRA_WILDCARD_MULTIPLIER,
    ZEBRA_BOLD,
    ZEBRA_WILDCARD,
)


def score_semi_picks(user_picks: set[str], actual_semis: set[str]) -> int:
    return sum(SEMI_PICK_POINTS for t in user_picks if t in actual_semis)


def score_group_goals(guess: int, actual: int, all_guesses: list[int]) -> int:
    """Return points for one guess. 1st closest = 25pts, 2nd closest = 10pts, rest = 0."""
    diff = abs(guess - actual)
    ranked = sorted(set(abs(g - actual) for g in all_guesses))
    if diff == ranked[0]:
        return GROUP_GOALS_WIN_POINTS
    if len(ranked) > 1 and diff == ranked[1]:
        return GROUP_GOALS_NEAR_POINTS
    return 0


def zebra_points(stage_reached: str, is_wildcard: bool) -> int:
    base = ZEBRA_POINTS.get(stage_reached, 0)
    return base * ZEBRA_WILDCARD_MULTIPLIER if is_wildcard else base


def _result(home: int, away: int) -> str:
    if home > away:
        return "home"
    if away > home:
        return "away"
    return "draw"


def calculate_points(
    pred_home: int,
    pred_away: int,
    act_home: int,
    act_away: int,
    home_team: str,
    away_team: str,
    stage: str = "GROUP_STAGE",
    match: dict | None = None,
) -> int:
    base = 0

    if pred_home == act_home and pred_away == act_away:
        base = 9
    elif _result(pred_home, pred_away) == _result(act_home, act_away):
        base = 3

    if base > 0:
        base += _upset_bonus(pred_home, pred_away, act_home, act_away, match)

    multiplier = STAGE_MULTIPLIERS.get(stage, 1.0)
    return round(base * multiplier)


def _upset_bonus(
    pred_home: int,
    pred_away: int,
    act_home: int,
    act_away: int,
    match: dict | None,
) -> int:
    actual_winner = _result(act_home, act_away)
    pred_winner = _result(pred_home, pred_away)

    if actual_winner == "draw" or pred_winner == "draw":
        return 0

    underdog = get_underdog(match) if match else None
    if underdog is None:
        return 0

    underdog_side = "home" if underdog == match["home_team"] else "away"

    if actual_winner == underdog_side and pred_winner == underdog_side:
        return 2

    return 0


def points_label(points: int | None) -> str:
    if points is None:
        return "—"
    if points == 0:
        return "0 pts"
    if points >= 27:
        return f"{points} pts :fire:"
    if points >= 18:
        return f"{points} pts :dart:"
    if points >= 9:
        return f"{points} pts :white_check_mark:"
    return f"{points} pts"
