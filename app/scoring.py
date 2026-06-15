from app.fifa_rankings import get_rank
from app.odds import get_underdog

STAGE_MULTIPLIERS: dict[str, float] = {
    "GROUP_STAGE":    1.0,
    "LAST_32":        1.5,
    "LAST_16":        1.5,
    "QUARTER_FINALS": 2.0,
    "SEMI_FINALS":    2.5,
    "THIRD_PLACE":    2.5,
    "FINAL":          3.0,
}

TOURNAMENT_PICK_POINTS = 30

SEMI_PICK_POINTS = 15          # per correct semi-finalist (4 picks × 15 = 60 max)

GROUP_GOALS_WIN_POINTS = 25    # closest guess
GROUP_GOALS_NEAR_POINTS = 10   # within ±5 of actual
GROUP_GOALS_NEAR_RANGE = 5

# Points for zebra pick based on how far they go
ZEBRA_POINTS: dict[str, int] = {
    "LAST_32":        10,
    "LAST_16":        20,
    "QUARTER_FINALS": 35,
    "SEMI_FINALS":    50,
    "FINAL":          65,
    "WINNER":         80,
}
ZEBRA_WILDCARD_MULTIPLIER = 2

ZEBRA_BOLD = [
    "Australia", "South Korea", "Canada", "Ecuador", "Austria",
    "Norway", "Sweden", "Turkey", "Iran", "Ghana", "Ivory Coast",
    "Algeria", "Tunisia", "Egypt", "Scotland", "Czechia", "Saudi Arabia",
]

ZEBRA_WILDCARD = [
    "Paraguay", "South Africa", "Bosnia-Herzegovina", "Uzbekistan",
    "Jordan", "Iraq", "Qatar", "Congo DR", "Panama",
    "New Zealand", "Cape Verde Islands", "Haiti", "Curaçao",
]


def score_semi_picks(user_picks: set[str], actual_semis: set[str]) -> int:
    return sum(SEMI_PICK_POINTS for t in user_picks if t in actual_semis)


def score_group_goals(guess: int, actual: int, all_guesses: list[int]) -> int:
    """Return points for one guess. all_guesses used to determine closest."""
    diff = abs(guess - actual)
    min_diff = min(abs(g - actual) for g in all_guesses)
    if diff == min_diff:
        return GROUP_GOALS_WIN_POINTS
    if diff <= GROUP_GOALS_NEAR_RANGE:
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
