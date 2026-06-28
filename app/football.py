import os
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

STAGE_LABELS: dict[str, str] = {
    "GROUP_STAGE":    "Group Stage",
    "LAST_32":        "Round of 32",
    "LAST_16":        "Round of 16",
    "QUARTER_FINALS": "Quarter-finals",
    "SEMI_FINALS":    "Semi-finals",
    "THIRD_PLACE":    "3rd Place",
    "FINAL":          ":trophy: Final",
}


def stage_label(stage: str) -> str:
    return STAGE_LABELS.get(stage, stage)


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
            return f"({ph}) {h} - {a} ({pa})"
        return f"{h} - {a}"

    if dur == "EXTRA_TIME":
        return f"{h} - {a}"

    return f"{h} - {a}"


def format_score_note(match) -> str:
    """Return a suffix note like ' _(AET)_', or empty string.

    For PENALTY_SHOOTOUT: returns empty when pen scores are shown inline
    in format_score (i.e. penalties_home is set), otherwise ' _(pens)_'.
    """
    dur = match["duration"] if hasattr(match, "keys") else match.get("duration", "REGULAR")
    if dur == "PENALTY_SHOOTOUT":
        try:
            if match["penalties_home"] is not None:
                return ""
        except (KeyError, IndexError):
            pass
        return " _(pens)_"
    if dur == "EXTRA_TIME":
        return " _(AET)_"
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
