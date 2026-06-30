import logging
import requests
from datetime import date, timedelta, datetime, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world"

# WC 2026 group stage matchday date ranges
_MATCHDAY_RANGES = [
    (1, date(2026, 6, 12), date(2026, 6, 17)),
    (2, date(2026, 6, 18), date(2026, 6, 23)),
    (3, date(2026, 6, 24), date(2026, 6, 27)),
]

_STATUS_MAP = {
    "STATUS_SCHEDULED":   "TIMED",
    "STATUS_FIRST_HALF":  "IN_PLAY",
    "STATUS_SECOND_HALF": "IN_PLAY",
    "STATUS_HALFTIME":    "HALFTIME",
    "STATUS_FULL_TIME":   "FINISHED",
    "STATUS_EXTRA_TIME":  "IN_PLAY",
    "STATUS_PENALTIES":   "IN_PLAY",
    "STATUS_FINAL_AET":   "FINISHED",
    "STATUS_FINAL_PEN":   "FINISHED",
    "STATUS_POSTPONED":   "POSTPONED",
    "STATUS_CANCELLED":   "CANCELLED",
}

_STAGE_MAP = {
    "group-stage":   "GROUP_STAGE",
    "round-of-32":   "LAST_32",
    "round-of-16":   "LAST_16",
    "quarterfinals": "QUARTER_FINALS",
    "semifinals":    "SEMI_FINALS",
    "3rd-place":     "THIRD_PLACE",
    "final":         "FINAL",
}

# Detect ET/pens from status name (live and finished variants)
_ET_STATUSES = {"STATUS_EXTRA_TIME", "STATUS_FINAL_AET"}
_PEN_STATUSES = {"STATUS_PENALTIES", "STATUS_FINAL_PEN"}


def _get(path: str, params: dict | None = None) -> dict:
    resp = requests.get(f"{BASE_URL}{path}", params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_90min_scores(summary: dict) -> tuple:
    """Return (home_90, away_90) goals scored in regular 90 minutes from linescores.

    Linescores[0] = 1st half, [1] = 2nd half (stop at -1 separator or after 2 periods).
    Returns (None, None) if linescores are unavailable.
    """
    header = summary.get("header", {})
    comp = (header.get("competitions") or [{}])[0]
    home_comp = next((c for c in comp.get("competitors", []) if c.get("homeAway") == "home"), None)
    away_comp = next((c for c in comp.get("competitors", []) if c.get("homeAway") == "away"), None)
    if not home_comp or not away_comp:
        return None, None

    def _sum_first_two(linescores):
        if not linescores or len(linescores) < 2:
            return None
        total, count = 0, 0
        for ls in linescores:
            val = int(ls.get("displayValue", -1))
            if val < 0:
                break
            total += val
            count += 1
            if count == 2:
                break
        return total if count == 2 else None

    h90 = _sum_first_two(home_comp.get("linescores"))
    a90 = _sum_first_two(away_comp.get("linescores"))
    return h90, a90


def _matchday(kickoff_utc: str) -> int | None:
    try:
        d = datetime.fromisoformat(kickoff_utc.replace("Z", "+00:00")).date()
        for num, start, end in _MATCHDAY_RANGES:
            if start <= d <= end:
                return num
    except Exception:
        pass
    return None


def _parse_event(event: dict) -> dict | None:
    comps = event.get("competitions", [])
    if not comps:
        return None
    comp = comps[0]

    competitors = comp.get("competitors", [])
    home_comp = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away_comp = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if not home_comp or not away_comp:
        return None

    home_name = home_comp["team"]["displayName"]
    away_name = away_comp["team"]["displayName"]

    # Skip TBD/placeholder knockout teams
    for name in (home_name, away_name):
        if any(word in name for word in ("Winner", "Loser", "TBD", "Runner")):
            return None

    status_obj = event.get("status", {})
    status_name = status_obj.get("type", {}).get("name", "STATUS_SCHEDULED")
    internal_status = _STATUS_MAP.get(status_name, "TIMED")
    period = status_obj.get("period") or 0

    # Duration — detect ET/pens from status name first, then fall back to period number.
    # Period 3/4 = extra time halves, period 5 = penalty shootout.
    # The period fallback handles unknown status names ESPN may use during live ET/pens.
    duration = "REGULAR"
    if status_name in _PEN_STATUSES:
        duration = "PENALTY_SHOOTOUT"
    elif status_name in _ET_STATUSES:
        duration = "EXTRA_TIME"
    elif period >= 5:
        duration = "PENALTY_SHOOTOUT"
    elif period >= 3:
        duration = "EXTRA_TIME"

    # If status name is unknown but period indicates live ET/pens, treat as IN_PLAY
    if internal_status == "TIMED" and period >= 3:
        internal_status = "IN_PLAY"
        logger.warning("Unknown ESPN status %r at period %s — treating as IN_PLAY", status_name, period)

    season = event.get("season", {})
    stage = _STAGE_MAP.get(season.get("slug", "group-stage"), "GROUP_STAGE")

    kickoff_utc = comp.get("startDate") or event.get("date", "")

    home_score = None
    away_score = None
    if internal_status in ("IN_PLAY", "HALFTIME", "FINISHED"):
        try:
            home_score = int(home_comp.get("score", 0))
            away_score = int(away_comp.get("score", 0))
        except (ValueError, TypeError):
            pass

    display_clock = status_obj.get("displayClock") if internal_status == "IN_PLAY" else None

    winner = None
    if internal_status == "FINISHED":
        if home_comp.get("winner"):
            winner = "HOME_TEAM"
        elif away_comp.get("winner"):
            winner = "AWAY_TEAM"
        else:
            winner = "DRAW"

    venue = comp.get("venue", {})
    venue_name = venue.get("fullName")
    venue_city = venue.get("address", {}).get("city")

    return {
        "external_id":     int(event["id"]),
        "home_team":       home_name,
        "away_team":       away_name,
        "kickoff_utc":     kickoff_utc,
        "stage":           stage,
        "matchday":        _matchday(kickoff_utc),
        "status":          internal_status,
        "home_score":      home_score,
        "away_score":      away_score,
        "et_home":         None,
        "et_away":         None,
        "penalties_home":  None,
        "penalties_away":  None,
        "winner":          winner,
        "duration":        duration,
        "display_clock":   display_clock,
        "venue_name":      venue_name,
        "venue_city":      venue_city,
    }


def _fetch_date(d: date) -> list[dict]:
    date_str = d.strftime("%Y%m%d")
    try:
        data = _get("/scoreboard", params={"dates": date_str})
        results = []
        for event in data.get("events", []):
            parsed = _parse_event(event)
            if parsed:
                results.append(parsed)
        return results
    except Exception as exc:
        logger.error("ESPN: failed to fetch %s: %s", date_str, exc)
        return []


def fetch_matches_for_dates(dates: list[date]) -> list[dict]:
    seen = set()
    matches = []
    for d in dates:
        for m in _fetch_date(d):
            key = m["external_id"]
            if key not in seen:
                seen.add(key)
                matches.append(m)
    return matches


def fetch_all_matches(from_date: date | None = None) -> list[dict]:
    """Fetch WC 2026 matches from from_date (default: today) through July 19.

    Skipping past dates avoids redundant API calls — finished matches are already
    scored in the DB. Pass from_date=date(2026, 6, 12) for a full historical import.
    """
    start = max(from_date or datetime.now(tz=_ESPN_TZ).date(), date(2026, 6, 12))
    end = date(2026, 7, 19)
    if start > end:
        return []
    dates = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    return fetch_matches_for_dates(dates)


_ESPN_TZ = ZoneInfo("America/New_York")


def fetch_live_matches() -> list[dict]:
    """Fetch yesterday through next 7 days using ESPN's own timezone (America/New_York).

    ESPN keys scoreboard dates by Eastern time regardless of match location,
    so we must use that timezone to build the correct date strings.
    Yesterday is included so any match missed during a DNS outage or date
    rollover is corrected on the next poll after connectivity recovers.
    The 7-day lookahead ensures newly-confirmed knockout fixtures (once ESPN
    replaces TBD placeholders with real team names) are picked up within
    seconds rather than waiting for the hourly full sync.
    """
    today_espn = datetime.now(tz=_ESPN_TZ).date()
    return fetch_matches_for_dates([today_espn + timedelta(days=i) for i in range(-1, 8)])


def fetch_match_summary(event_id: int) -> dict:
    try:
        return _get("/summary", params={"event": event_id})
    except Exception as exc:
        logger.error("ESPN: failed to fetch summary for event %s: %s", event_id, exc)
        return {}


def get_display_clock(summary: dict) -> str | None:
    """Extract displayClock from a match summary — header can be a dict or a list."""
    header = summary.get("header") or {}
    if isinstance(header, list):
        header = header[0] if header else {}
    try:
        return header.get("competitions", [{}])[0].get("status", {}).get("displayClock")
    except Exception:
        return None


_GOAL_TYPE_SUFFIX = {
    "penalty---scored": " _(pen)_",
    "own-goal":         " _(OG)_",
}


def get_goal_scorers(summary: dict) -> list[dict]:
    """Extract all goals from keyEvents: [{team_name, scorer_name, minute, suffix}]."""
    goals = []
    for event in summary.get("keyEvents", []):
        if not event.get("scoringPlay"):
            continue
        team_name = event.get("team", {}).get("displayName", "")
        participants = event.get("participants", [])
        if not participants:
            scorer_name = "Unknown"
        elif isinstance(participants[0], str):
            scorer_name = participants[0]
        else:
            scorer_name = participants[0].get("athlete", {}).get("displayName", "Unknown")
        minute = event.get("clock", {}).get("displayValue", "?")
        event_type = event.get("type", {}).get("type", "")
        suffix = _GOAL_TYPE_SUFFIX.get(event_type, "")
        goals.append({"team_name": team_name, "scorer_name": scorer_name, "minute": minute, "suffix": suffix})
    return goals


def get_second_half_kickoff(summary: dict) -> datetime | None:
    """Return estimated second half kickoff (halftime wallclock + 15 min), or None."""
    for event in summary.get("keyEvents", []):
        if event.get("type", {}).get("type") == "halftime":
            wc = event.get("wallclock")
            if wc:
                ht = datetime.fromisoformat(wc.replace("Z", "+00:00"))
                return ht + timedelta(minutes=15)
    return None


def get_match_stats(summary: dict) -> dict | None:
    """Extract key stats from a match summary."""
    teams = summary.get("boxscore", {}).get("teams", [])
    if len(teams) < 2:
        return None

    def _stat(team_data, name):
        for s in team_data.get("statistics", []):
            if s.get("name") == name:
                return s.get("displayValue", "")
        return ""

    home, away = teams[0], teams[1]
    return {
        "home_possession":      _stat(home, "possessionPct"),
        "away_possession":      _stat(away, "possessionPct"),
        "home_shots_on_target": _stat(home, "shotsOnTarget"),
        "away_shots_on_target": _stat(away, "shotsOnTarget"),
        "home_total_shots":     _stat(home, "totalShots"),
        "away_total_shots":     _stat(away, "totalShots"),
        "home_yellow_cards":    _stat(home, "yellowCards"),
        "away_yellow_cards":    _stat(away, "yellowCards"),
    }


def fetch_top_scorer() -> str | None:
    """Return the current golden boot leader's name from ESPN leaders endpoint."""
    try:
        data = _get("/leaders")
        for cat in data.get("categories", []):
            if "goal" in cat.get("name", "").lower():
                leaders = cat.get("leaders", [])
                if leaders:
                    return leaders[0].get("athlete", {}).get("displayName")
    except Exception as exc:
        logger.error("ESPN: failed to fetch top scorer: %s", exc)
    return None


def get_penalty_scores(summary: dict) -> tuple[int | None, int | None]:
    """Extract penalty shootout scores from match summary header linescores.

    ESPN stores period-by-period scores in competitor.linescores as
    [{'displayValue': N}, ...].  The penalty period is always the last entry
    (index 4 for AET→pens, index 2 for direct pens).  Confirmed via
    Copa America 2024 Argentina vs Ecuador (4-2 on pens).
    Returns (home_pen_score, away_pen_score) or (None, None).
    """
    header = summary.get("header") or {}
    if isinstance(header, list):
        header = header[0] if header else {}
    comps = header.get("competitions", [])
    if not comps:
        return None, None
    comp = comps[0]
    competitors = comp.get("competitors", [])
    home_comp = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away_comp = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if not home_comp or not away_comp:
        return None, None
    home_ls = home_comp.get("linescores", [])
    away_ls = away_comp.get("linescores", [])
    if len(home_ls) >= 3 and len(away_ls) >= 3:
        try:
            return int(home_ls[-1]["displayValue"]), int(away_ls[-1]["displayValue"])
        except (KeyError, ValueError, TypeError):
            pass
    return None, None
