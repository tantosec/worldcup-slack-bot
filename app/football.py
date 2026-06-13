import os
import logging
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

BASE_URL = "https://api.football-data.org/v4"
COMPETITION = "WC"  # FIFA World Cup code on football-data.org


def _headers() -> dict:
    return {"X-Auth-Token": os.environ["FOOTBALL_DATA_API_KEY"]}


def _get(path: str, params: dict | None = None) -> dict:
    url = f"{BASE_URL}{path}"
    resp = requests.get(url, headers=_headers(), params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


STAGE_LABELS: dict[str, str] = {
    "GROUP_STAGE":   "Group Stage",
    "LAST_32":       "Round of 32",
    "LAST_16":       "Round of 16",
    "QUARTER_FINALS": "Quarter-finals",
    "SEMI_FINALS":   "Semi-finals",
    "THIRD_PLACE":   "3rd Place",
    "FINAL":         ":trophy: Final",
}


def stage_label(stage: str) -> str:
    return STAGE_LABELS.get(stage, stage)


def _parse_match(m: dict) -> dict:
    score = m.get("score", {})
    # regularTime = 90-min score; fallback to fullTime for REGULAR-duration matches
    # where the API omits regularTime. Never use fullTime for ET/pens — it's
    # cumulative (and for pens seems to include penalty kicks, making it unusable).
    regular = score.get("regularTime") or score.get("fullTime") or {}
    et      = score.get("extraTime") or {}
    pens    = score.get("penalties") or {}

    return {
        "external_id":    m["id"],
        "home_team":      m["homeTeam"]["name"],
        "away_team":      m["awayTeam"]["name"],
        "kickoff_utc":    m["utcDate"],
        "stage":          m.get("stage", "GROUP_STAGE"),
        "matchday":       m.get("matchday"),
        "status":         m["status"],
        "home_score":     regular.get("home"),      # 90-min score — used for prediction scoring
        "away_score":     regular.get("away"),
        "et_home":        et.get("home"),            # goals in ET period only
        "et_away":        et.get("away"),
        "penalties_home": pens.get("home"),          # penalty kicks count
        "penalties_away": pens.get("away"),
        "winner":         score.get("winner"),       # HOME_TEAM / AWAY_TEAM / DRAW
        "duration":       score.get("duration", "REGULAR"),
    }


def format_score(match) -> str:
    """Return the bare score string: '2 - 1' or '1 (4) - (3) 1' for penalties."""
    h = match["home_score"]
    a = match["away_score"]
    dur = match["duration"] if hasattr(match, "keys") else match.get("duration", "REGULAR")

    if h is None or a is None:
        return "vs"

    if dur == "PENALTY_SHOOTOUT":
        ph = match["penalties_home"]
        pa = match["penalties_away"]
        if ph is not None and pa is not None:
            return f"{h} ({ph}) - ({pa}) {a}"
        return f"{h} - {a}"

    if dur == "EXTRA_TIME":
        eth = match["et_home"]
        eta = match["et_away"]
        if eth is not None and eta is not None:
            return f"{h + eth} - {a + eta}"
        return f"{h} - {a}"

    return f"{h} - {a}"


def format_score_note(match) -> str:
    """Return a suffix note like ' _(AET)_' or ' _(pens)_', or empty string."""
    dur = match["duration"] if hasattr(match, "keys") else match.get("duration", "REGULAR")
    if dur == "PENALTY_SHOOTOUT":
        return " _(pens)_"
    if dur == "EXTRA_TIME":
        return " _(AET)_"
    return ""


def fetch_top_scorer() -> str | None:
    """
    Return the golden boot winner's name using the official tiebreakers:
    1. Most goals  2. Fewest penalties  3. Most assists
    Returns None if no scorers data yet.
    """
    try:
        data = _get(f"/competitions/{COMPETITION}/scorers", params={"limit": 10})
        scorers = data.get("scorers", [])
        if not scorers:
            return None
        # Sort by: goals desc, penalties asc, assists desc
        ranked = sorted(
            scorers,
            key=lambda s: (-s.get("goals", 0), s.get("penalties", 0), -s.get("assists", 0)),
        )
        return ranked[0]["player"]["name"]
    except Exception as exc:
        logger.error("Failed to fetch scorers: %s", exc)
        return None


def fetch_all_matches() -> list[dict]:
    """Return all WC 2026 matches from football-data.org."""
    try:
        data = _get(f"/competitions/{COMPETITION}/matches")
        return [_parse_match(m) for m in data.get("matches", [])]
    except Exception as exc:
        logger.error("Failed to fetch matches: %s", exc)
        return []


def fetch_match(external_id: int) -> dict | None:
    """Return a single match by its football-data.org ID."""
    try:
        data = _get(f"/matches/{external_id}")
        return _parse_match(data)
    except Exception as exc:
        logger.error("Failed to fetch match %s: %s", external_id, exc)
        return None


def fetch_match_goals(external_id: int) -> list[dict]:
    """Return goal events for a match: [{minute, scorer_name, team_name}, ...]"""
    try:
        data = _get(f"/matches/{external_id}")
        goals = []
        for g in data.get("goals", []):
            scorer = g.get("scorer") or {}
            team = g.get("team") or {}
            goals.append({
                "minute":      g.get("minute"),
                "scorer_name": scorer.get("name") or "Unknown",
                "team_name":   team.get("name") or "Unknown",
            })
        return goals
    except Exception as exc:
        logger.error("Failed to fetch goals for match %s: %s", external_id, exc)
        return []


def is_kickoff_passed(kickoff_utc: str) -> bool:
    kickoff = datetime.fromisoformat(kickoff_utc.replace("Z", "+00:00"))
    return datetime.now(timezone.utc) >= kickoff


def estimate_match_time(kickoff_utc: str, status: str) -> str:
    """Return a human-readable match time label based on status and elapsed time."""
    if status == "HALFTIME":
        return "Half Time"
    if status == "PAUSED":
        return "Paused"
    if status != "IN_PLAY":
        return status.replace("_", " ").title()
    kickoff = datetime.fromisoformat(kickoff_utc.replace("Z", "+00:00"))
    elapsed = int((datetime.now(timezone.utc) - kickoff).total_seconds() / 60)
    elapsed = min(elapsed, 90)
    return f"~{elapsed}'"


def format_kickoff(kickoff_utc: str) -> str:
    """Return a human-readable kickoff string in the configured display timezone."""
    tz_name = os.getenv("DISPLAY_TIMEZONE", "Australia/Sydney")
    tz = ZoneInfo(tz_name)
    dt = datetime.fromisoformat(kickoff_utc.replace("Z", "+00:00")).astimezone(tz)
    tz_label = dt.strftime("%Z")
    return dt.strftime(f"%d %b %H:%M {tz_label}")
