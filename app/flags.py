import json
from pathlib import Path

FLAGS: dict[str, str] = json.loads(
    (Path(__file__).parent / "data" / "flags.json").read_text(encoding="utf-8")
)


def flag(team_name: str) -> str:
    return FLAGS.get(team_name, ":white_flag:")


def team(team_name: str) -> str:
    """Return 'FLAG TeamName' for display."""
    return f"{flag(team_name)} {team_name}"


def home(team_name: str) -> str:
    """Return 'TeamName FLAG' for home team in match display."""
    return f"{team_name} {flag(team_name)}"


def away(team_name: str) -> str:
    """Return 'FLAG TeamName' for away team in match display."""
    return f"{flag(team_name)} {team_name}"


def vs(home_team: str, away_team: str) -> str:
    """Return 'HomeTeam FLAG vs FLAG AwayTeam'."""
    return f"{home(home_team)} vs {away(away_team)}"


def score(home_team: str, away_team: str, home_score, away_score) -> str:
    """Return 'HomeTeam FLAG h - a FLAG AwayTeam'."""
    return f"{home(home_team)} {home_score} - {away_score} {away(away_team)}"
