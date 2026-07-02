import os
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.config import STAGE_LABELS

logger = logging.getLogger(__name__)


def _safe_get(match, *keys):
    """Try multiple keys on a match dict/Row, return first non-None value or None."""
    for key in keys:
        try:
            val = match[key]
            if val is not None:
                return val
        except (KeyError, IndexError, TypeError):
            pass
    return None


def stage_label(stage: str) -> str:
    return STAGE_LABELS.get(stage, stage)


def format_score(match) -> str:
    """Return the bare score string.

    REGULAR/HALFTIME: '2 - 1'
    PENALTY_SHOOTOUT: '(3) 1 - 1 (4)'  (pen scores wrap the tied 90min/AET score)
    EXTRA_TIME: '1 - 1'  (90-minute score; AET score goes in format_score_note)
    """
    h = match["home_score"]
    a = match["away_score"]
    dur = match["duration"] if hasattr(match, "keys") else match.get("duration", "REGULAR")

    if h is None or a is None:
        return "vs"

    if dur == "PENALTY_SHOOTOUT":
        ph = match["penalties_home"]
        pa = match["penalties_away"]
        if ph is not None and pa is not None:
            return f"({ph}) {h} - {a} ({pa})"
        return f"{h} - {a}"

    if dur == "EXTRA_TIME":
        h90 = _safe_get(match, "home_score_90", "act_home")
        a90 = _safe_get(match, "away_score_90", "act_away")
        if h90 is not None:
            return f"{h90} - {a90}"
        return f"{h} - {a}"

    return f"{h} - {a}"


def format_score_note(match) -> str:
    """Return a score suffix: ' _(Penalties)_', ' _(Extra Time: 🇩🇪 2 - 1 🇵🇾)_', or ''."""
    from app.flags import flag as _flag
    dur = match["duration"] if hasattr(match, "keys") else match.get("duration", "REGULAR")
    if dur == "PENALTY_SHOOTOUT":
        return " _(Penalties)_"
    if dur == "EXTRA_TIME":
        h90 = _safe_get(match, "home_score_90", "act_home")
        h_aet = match["home_score"]
        a_aet = match["away_score"]
        home_team = _safe_get(match, "home_team")
        away_team = _safe_get(match, "away_team")
        # Only show the ET final score in the note when we also have the 90-min score to
        # show in the main display — otherwise both would show the same score redundantly.
        if h90 is not None and home_team and away_team and h_aet is not None:
            return f" _(Extra Time: {_flag(home_team)} {h_aet} - {a_aet} {_flag(away_team)})_"
        return " _(Extra Time)_"
    return ""


def is_kickoff_passed(kickoff_utc: str) -> bool:
    kickoff = datetime.fromisoformat(kickoff_utc.replace("Z", "+00:00"))
    return datetime.now(timezone.utc) >= kickoff


def estimate_match_time(kickoff_utc: str, status: str, display_clock: str | None = None) -> str:
    """Return a human-readable match time label."""
    if display_clock:
        return display_clock
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
