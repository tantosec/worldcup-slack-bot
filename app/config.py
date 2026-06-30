"""
Central competition configuration — loaded once from config.json at startup.
All competition-specific constants flow through here so swapping tournaments
only requires editing config.json (and rebuilding app/data/).
"""
import json
import os
from datetime import date

_PATH = os.path.join(os.path.dirname(__file__), "data", "config.json")

with open(_PATH, encoding="utf-8") as _f:
    _C = json.load(_f)

_comp    = _C["competition"]
_phases  = _C["phases"]
_scoring = _C["scoring"]

# ── Competition identity ──────────────────────────────────────────────────────
COMPETITION_NAME        = _comp["name"]
COMPETITION_SHORT_NAME  = _comp["short_name"]
ESPN_SLUG               = _comp["espn_slug"]
GROUP_STAGE_MATCH_COUNT = _comp["group_stage_match_count"]
TOURNAMENT_START        = date.fromisoformat(_comp["tournament_start"])
TOURNAMENT_END          = date.fromisoformat(_comp["tournament_end"])
PICKS_LOCK_TIME: str | None = _comp.get("picks_lock_time")

# ── Phase structure ───────────────────────────────────────────────────────────
PHASE_STAGES       = [(_p["key"], _p["stages"]) for _p in _phases]
PHASE_LABELS       = {_p["key"]: _p["label"]       for _p in _phases}
PHASE_BUTTON_TEXT  = {_p["key"]: _p["button_text"] for _p in _phases}
PHASE_MODAL_TITLES = {_p["key"]: _p["modal_title"] for _p in _phases}

# ── Stage labels (football.py stage_label()) ─────────────────────────────────
# Each stage inherits its phase label unless the phase defines per-stage overrides.
STAGE_LABELS: dict[str, str] = {}
for _p in _phases:
    _overrides = _p.get("stage_labels", {})
    for _s in _p["stages"]:
        STAGE_LABELS[_s] = _overrides.get(_s, _p["label"])

# ── Stage multipliers (scoring.py) ───────────────────────────────────────────
STAGE_MULTIPLIERS: dict[str, float] = {}
for _p in _phases:
    for _s in _p["stages"]:
        STAGE_MULTIPLIERS[_s] = _p["multiplier"]

# ── Stage display names for notifications (scheduler.py) ─────────────────────
STAGE_DISPLAY: dict[str, str] = _C.get("stage_display", {})

# ── Next-phase labels for knockout notifications (scheduler.py) ──────────────
NEXT_PHASE_LABEL: dict[str, str] = {
    _p["stages"][0]: _p["next_label"]
    for _p in _phases
    if "next_label" in _p
}

# ── Scoring constants (scoring.py / odds.py) ─────────────────────────────────
TOURNAMENT_PICK_POINTS    = _scoring["tournament_pick_points"]
SEMI_PICK_POINTS          = _scoring["semi_pick_points"]
GROUP_GOALS_WIN_POINTS    = _scoring["group_goals_win_points"]
GROUP_GOALS_NEAR_POINTS   = _scoring["group_goals_near_points"]
ZEBRA_POINTS: dict[str, int] = _scoring["zebra_points"]
ZEBRA_WILDCARD_MULTIPLIER = _scoring["zebra_wildcard_multiplier"]
UNDERDOG_RATIO            = _scoring["underdog_ratio"]
ZEBRA_BOLD: list[str]     = _scoring["zebra_bold"]
ZEBRA_WILDCARD: list[str] = _scoring["zebra_wildcard"]
