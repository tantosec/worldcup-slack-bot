import json
import re
import logging
from datetime import datetime

from app import db
from app.flags import flag, home, away, vs
from app.football import format_score, format_score_note, format_kickoff, stage_label
from app.odds import format_prob_line, format_underdog_line, get_underdog
from app.scoring import TOURNAMENT_PICK_POINTS, SEMI_PICK_POINTS
import os

logger = logging.getLogger(__name__)

# ── Upcoming predictions modal ────────────────────────────────────────────────
OPEN_MYSTATS_MODAL_ACTION = "open_mystats_modal"
MYSTATS_MODAL_PREV_ACTION = "mystats_modal_prev"
MYSTATS_MODAL_NEXT_ACTION = "mystats_modal_next"
_EPHEMERAL_PREVIEW = 3
_MODAL_PAGE_SIZE = 5

# ── Phase pagination ──────────────────────────────────────────────────────────
OPEN_PHASE_MODAL_ACTION = "open_phase_modal"
PHASE_MODAL_PREV_ACTION = "phase_modal_prev"
PHASE_MODAL_NEXT_ACTION = "phase_modal_next"
_INLINE_MAX_MATCHES = 10
_PHASE_MODAL_PAGE_SIZE = 16

_PHASE_STAGES = [
    ("GROUP_STAGE",    ["GROUP_STAGE"]),
    ("LAST_32",        ["LAST_32"]),
    ("LAST_16",        ["LAST_16"]),
    ("QUARTER_FINALS", ["QUARTER_FINALS"]),
    ("SEMI_FINALS",    ["SEMI_FINALS"]),
    ("FINALS",         ["THIRD_PLACE", "FINAL"]),
]
_PHASE_ORDER = [pk for pk, _ in _PHASE_STAGES]
_PHASE_STAGES_MAP = {pk: stages for pk, stages in _PHASE_STAGES}
_STAGE_TO_PHASE = {s: pk for pk, stages in _PHASE_STAGES for s in stages}

_PHASE_LABELS = {
    "GROUP_STAGE":    "Group Stage",
    "LAST_32":        "Round of 32",
    "LAST_16":        "Round of 16",
    "QUARTER_FINALS": "Quarter-finals",
    "SEMI_FINALS":    "Semi-finals",
    "FINALS":         "Finals",
}

_PHASE_BUTTON_TEXT = {
    "GROUP_STAGE":    "📅 Group Stage",
    "LAST_32":        "🔵 Round of 32",
    "LAST_16":        "🔵 Round of 16",
    "QUARTER_FINALS": "🔥 Quarter-finals",
    "SEMI_FINALS":    "⭐ Semi-finals",
    "FINALS":         "🏆 Finals",
}

_PHASE_MODAL_TITLES = {
    "GROUP_STAGE":    "Group Stage",
    "LAST_32":        "Round of 32",
    "LAST_16":        "Round of 16",
    "QUARTER_FINALS": "Quarter-finals",
    "SEMI_FINALS":    "Semi-finals",
    "FINALS":         "Finals",
}


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _context(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


def _divider() -> dict:
    return {"type": "divider"}


def _date_group_key(kickoff_utc: str, phase_key: str, stage: str) -> str:
    dt = datetime.fromisoformat(kickoff_utc.replace("Z", "+00:00"))
    label = f"{dt.day} {dt.strftime('%b %Y')}"
    if phase_key == "FINALS":
        label += " · Final" if stage == "FINAL" else " · 3rd Place"
    return label


def _pred_icon(p) -> str:
    pts = p["points"] or 0
    if p["is_auto"]:
        return ":robot_face:"
    if p["pred_home"] == p["act_home"] and p["pred_away"] == p["act_away"]:
        return ":dart:"
    if pts > 0:
        return ":white_check_mark:"
    return ":x:"


def _upset_flag(p) -> str:
    pts = p["points"] or 0
    if pts <= 0:
        return ""
    underdog = get_underdog(p)
    if not underdog:
        return ""
    underdog_won = (
        (underdog == p["home_team"] and p["home_score"] > p["away_score"]) or
        (underdog == p["away_team"] and p["away_score"] > p["home_score"])
    )
    pred_underdog_wins = (
        (underdog == p["home_team"] and p["pred_home"] > p["pred_away"]) or
        (underdog == p["away_team"] and p["pred_away"] > p["pred_home"])
    )
    return "  :zap:" if (underdog_won and pred_underdog_wins) else ""


def _phase_preds_blocks(preds, phase_key: str) -> list[dict]:
    """Build blocks for finished predictions (sorted newest-first), grouped by date.
    Each date group: context (date sep) + per match: section (result) + context (prediction)."""
    by_date = {}
    date_order = []
    for p in preds:
        key = _date_group_key(p["kickoff_utc"], phase_key, p["stage"])
        if key not in by_date:
            by_date[key] = []
            date_order.append(key)
        by_date[key].append(p)

    blocks = []
    for date_key in date_order:
        blocks.append(_context(f"─────────────────  {date_key}  ─────────────────"))
        for p in by_date[date_key]:
            pts = p["points"] or 0
            pred_flags = f"{flag(p['home_team'])} {p['pred_home']} - {p['pred_away']} {flag(p['away_team'])}"
            pred_icon = ":robot_face:" if p["is_auto"] else ":pencil:"
            auto_note = ""
            if p["is_auto"] and pts > 0:
                multiplier = float(os.getenv("AUTO_PICK_POINTS_MULTIPLIER", "0.75"))
                penalty_pct = round((1 - multiplier) * 100)
                auto_note = f" _(-{penalty_pct}%)_"
            blocks.append(_section(
                f"{_pred_icon(p)}  {home(p['home_team'])} {format_score(p)} "
                f"{away(p['away_team'])}{format_score_note(p)}"
            ))
            blocks.append(_context(f"*{pred_icon} Predicted:  {pred_flags}   →   +{pts} pts{_upset_flag(p)}*{auto_note}"))

    return blocks


def _current_phase_key(finished_preds, upcoming_preds=None) -> str:
    """Return the key of the most advanced phase with finished predictions.
    Upcoming predictions are ignored — they can be in future phases and would
    skip over phases that have results but haven't fully completed yet."""
    stage_set = {p["stage"] for p in finished_preds}
    current = _PHASE_ORDER[0]
    for pk in _PHASE_ORDER:
        if any(s in stage_set for s in _PHASE_STAGES_MAP[pk]):
            current = pk
    return current


def _build_me_blocks(target_id: str, caller_id: str, client) -> tuple[list, str]:
    """Build blocks for /mystats. Returns (blocks, title)."""
    viewing_other = target_id != caller_id

    with db.db() as conn:
        stats = db.get_user_match_stats(conn, target_id)
        picks = db.get_tournament_pick(conn, target_id)
        rank, total_points = db.get_user_rank_and_total(conn, target_id)
        total_players = len(db.get_enrolled_users(conn))
        picks_locked = _picks_locked(conn)
        picks_revealed = db.picks_reveal_already_sent(conn)
        finished_preds = db.get_user_finished_predictions(conn, target_id)
        upcoming_preds = [] if viewing_other else db.get_user_upcoming_predictions(conn, target_id)
        missed, still_to_predict = (0, 0) if viewing_other else db.get_user_prediction_gaps(conn, target_id)
        zebra_knocked_out = db.team_knocked_out(conn, picks["zebra"]) if picks and picks["zebra"] else None

    if viewing_other:
        try:
            user_info = client.users_info(user=target_id)
            display_name = (
                user_info["user"]["profile"].get("display_name")
                or user_info["user"]["profile"].get("real_name")
                or "Unknown"
            )
            header_name = f"{display_name}'s"
        except Exception:
            header_name = "Their"
    else:
        header_name = "Your"

    match_pts = stats["match_points"] or 0
    tournament_pts = (total_points or 0) - match_pts

    bonus_fields = []
    if picks:
        if picks["winner_points"] is not None:
            bonus_fields.append({"type": "mrkdwn", "text": f":first_place_medal: *Winner*\n{picks['winner_points']} pts"})
        if picks["scorer_points"] is not None:
            bonus_fields.append({"type": "mrkdwn", "text": f":athletic_shoe: *Golden Boot*\n{picks['scorer_points']} pts"})
        if picks["zebra_points"] is not None:
            z_pts_val = picks["zebra_points"]
            if z_pts_val == 0 or zebra_knocked_out is True:
                z_status = "  :skull:"
            elif z_pts_val > 0 and zebra_knocked_out is False:
                z_status = "  :fire:"
            else:
                z_status = ""
            bonus_fields.append({"type": "mrkdwn", "text": f":zebra_face: *Zebra*\n{z_pts_val} pts{z_status}"})
        if picks["semi_points"] is not None:
            bonus_fields.append({"type": "mrkdwn", "text": f":four: *Semis*\n{picks['semi_points']} pts"})
        if picks["group_goals_points"] is not None:
            bonus_fields.append({"type": "mrkdwn", "text": f":goal_net: *Group goals*\n{picks['group_goals_points']} pts"})

    title = f"📊 {header_name} World Cup 2026 Stats"
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": title, "emoji": True}},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f":trophy: *Rank*\n#{rank} of {total_players}"},
                {"type": "mrkdwn", "text": f"*Total*\n{total_points} pts"},
                {"type": "mrkdwn", "text": f":soccer: *Match pts*\n{match_pts}"},
                {"type": "mrkdwn", "text": f":crystal_ball: *Tournament pts*\n{tournament_pts}"},
            ],
        },
    ]
    if bonus_fields:
        blocks.append({"type": "section", "fields": bonus_fields})

    # ── Tournament Picks ──────────────────────────────────────────────────────
    blocks += [_divider(), _section("🔮  *Tournament Picks*")]

    if picks and (not viewing_other or picks_revealed):
        blocks.append(_section(_picks_text(picks, picks_locked, zebra_knocked_out)))
        if picks["is_auto"] if "is_auto" in picks.keys() else False:
            blocks.append(_context(":robot_face: _These picks were auto-generated — you missed the deadline._"))
    elif viewing_other and not picks_revealed:
        blocks.append(_context("_Picks are revealed after picks lock._"))
    else:
        blocks.append(_context("_(not submitted yet)_"))

    # ── Match Predictions ─────────────────────────────────────────────────────
    if finished_preds:
        current_pk = _current_phase_key(finished_preds, upcoming_preds or [])
        current_stages = set(_PHASE_STAGES_MAP[current_pk])
        current_preds = [p for p in finished_preds if p["stage"] in current_stages]
        phase_label = _PHASE_LABELS[current_pk]

        # count_str is phase-specific
        phase_upcoming = [p for p in (upcoming_preds or []) if p["stage"] in current_stages]
        count_str = f"{len(current_preds)} played"
        if phase_upcoming:
            count_str += f"  ·  {len(phase_upcoming)} upcoming"
        if not viewing_other:
            if still_to_predict:
                count_str += f"  ·  :pencil: {still_to_predict} to predict"
            if missed:
                count_str += f"  ·  :x: {missed} missed"

        # Merged header + phase label (cut 1: saves 1 block vs separate section + context)
        blocks += [_divider(), _section(f"⚽  *{phase_label} Predictions* ({count_str})")]

        if current_preds:
            sorted_current = sorted(current_preds, key=lambda p: p["kickoff_utc"], reverse=True)
            blocks.extend(_phase_preds_blocks(sorted_current[:_INLINE_MAX_MATCHES], current_pk))
            if len(current_preds) > _INLINE_MAX_MATCHES:
                blocks.append({"type": "actions", "elements": [{
                    "type": "button",
                    "text": {"type": "plain_text", "text": f"📋 See all {len(current_preds)} {phase_label} predictions →", "emoji": True},
                    "action_id": OPEN_PHASE_MODAL_ACTION,
                    "value": json.dumps({"target_id": target_id, "phase_key": current_pk, "page": 0}),
                }]})
        else:
            blocks.append(_context(f"_No {phase_label} results yet._"))

        # Past phase buttons — no divider, no label (cuts 2 & 3: saves 2 blocks)
        finished_phase_keys = {_STAGE_TO_PHASE[p["stage"]] for p in finished_preds if p["stage"] in _STAGE_TO_PHASE}
        past_buttons = []
        for pk in _PHASE_ORDER:
            if pk == current_pk:
                break
            if pk in finished_phase_keys:
                past_buttons.append({
                    "type": "button",
                    "text": {"type": "plain_text", "text": _PHASE_BUTTON_TEXT[pk], "emoji": True},
                    "action_id": OPEN_PHASE_MODAL_ACTION,
                    "value": json.dumps({"target_id": target_id, "phase_key": pk, "page": 0}),
                })
        if past_buttons:
            blocks.append({"type": "actions", "elements": past_buttons})
    else:
        no_preds_count = f"{len(upcoming_preds)} upcoming" if upcoming_preds else "0 played"
        blocks += [_divider(), _section(f"⚽  *Match Predictions* ({no_preds_count})")]
        blocks.append(_context("_No finished matches predicted yet._"))

    # ── Upcoming ──────────────────────────────────────────────────────────────
    if upcoming_preds:
        blocks += [_divider(), _section("⏰  *Upcoming*")]
        for p in upcoming_preds[:_EPHEMERAL_PREVIEW]:
            venue_parts = [x for x in [p["venue_name"], p["venue_city"]] if x]
            venue_str = ("  ·  " + ", ".join(venue_parts)) if venue_parts else ""
            pick_icon = ":robot_face:" if p["is_auto"] else ":pencil:"
            blocks.append(_section(
                f"*{vs(p['home_team'], p['away_team'])}*  ·  {format_kickoff(p['kickoff_utc'])}{venue_str}\n"
                f"{pick_icon} Your pick: *{p['pred_home']} - {p['pred_away']}*"
            ))
            context_parts = [x for x in [format_prob_line(p), format_underdog_line(p, action=True)] if x]
            if context_parts:
                blocks.append(_context("  ·  ".join(context_parts)))
        if len(upcoming_preds) > _EPHEMERAL_PREVIEW:
            blocks.append({"type": "actions", "elements": [{
                "type": "button",
                "text": {"type": "plain_text", "text": f"See all {len(upcoming_preds)} upcoming →", "emoji": True},
                "action_id": OPEN_MYSTATS_MODAL_ACTION,
                "value": target_id,
            }]})

    return blocks, title


def handle_me(respond, body, client):
    caller_id = body["user_id"]
    text = (body.get("text") or "").strip()
    logger.info("/mystats called by %s with text: %r", caller_id, text)

    target_id = caller_id

    mention = re.search(r"<@([A-Z0-9]+)(?:\|[^>]+)?>", text)
    if mention:
        target_id = mention.group(1)
    elif text.startswith("@"):
        username = text[1:].lower()
        target_id = _lookup_user_by_name(client, username) or caller_id

    with db.db() as conn:
        if not db.is_enrolled(conn, target_id):
            if target_id != caller_id:
                respond(response_type="ephemeral", text=f":shrug: <@{target_id}> hasn't joined the league yet.")
            else:
                respond(response_type="ephemeral", text=":wave: You're not enrolled yet — use `/register` to join!")
            return

    blocks, title = _build_me_blocks(target_id, caller_id, client)
    respond(response_type="ephemeral", blocks=blocks, text=title)


# ── Upcoming predictions modal ─────────────────────────────────────────────────

def _build_upcoming_modal_view(target_id: str, page: int = 0) -> dict:
    with db.db() as conn:
        upcoming_preds = db.get_user_upcoming_predictions(conn, target_id)

    total = len(upcoming_preds)
    total_pages = max(1, -(-total // _MODAL_PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    start = page * _MODAL_PAGE_SIZE

    blocks = []
    for p in upcoming_preds[start:start + _MODAL_PAGE_SIZE]:
        venue_parts = [x for x in [p["venue_name"], p["venue_city"]] if x]
        venue_str = ("  ·  " + ", ".join(venue_parts)) if venue_parts else ""
        pick_icon = ":robot_face:" if p["is_auto"] else ":pencil:"
        blocks.append(_section(
            f"*{vs(p['home_team'], p['away_team'])}*  ·  {format_kickoff(p['kickoff_utc'])}{venue_str}\n"
            f"{pick_icon} Your pick: *{p['pred_home']} - {p['pred_away']}*"
        ))
        context_parts = [x for x in [format_prob_line(p), format_underdog_line(p, action=True)] if x]
        if context_parts:
            blocks.append(_context("  ·  ".join(context_parts)))

    nav_elements = []
    if page > 0:
        nav_elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "← Previous", "emoji": True},
            "action_id": MYSTATS_MODAL_PREV_ACTION,
            "value": str(page - 1),
        })
    if page < total_pages - 1:
        nav_elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "Next →", "emoji": True},
            "action_id": MYSTATS_MODAL_NEXT_ACTION,
            "value": str(page + 1),
        })
    if nav_elements:
        blocks.append(_divider())
        blocks.append({"type": "actions", "elements": nav_elements})
    blocks.append(_context(f"_Page {page + 1} of {total_pages}  ·  {total} upcoming predictions_"))

    return {
        "type": "modal",
        "title": {"type": "plain_text", "text": "Upcoming Predictions", "emoji": True},
        "close": {"type": "plain_text", "text": "Close"},
        "private_metadata": json.dumps({"target_id": target_id}),
        "blocks": blocks,
    }


def handle_open_mystats_modal(ack, body, client):
    ack()
    target_id = body["actions"][0]["value"]
    view = _build_upcoming_modal_view(target_id, page=0)
    client.views_open(trigger_id=body["trigger_id"], view=view)


def handle_mystats_modal_nav(ack, body, client):
    ack()
    metadata = json.loads(body["view"]["private_metadata"])
    target_id = metadata["target_id"]
    page = int(body["actions"][0]["value"])
    view = _build_upcoming_modal_view(target_id, page=page)
    client.views_update(view_id=body["view"]["id"], view=view)


# ── Phase predictions modal ────────────────────────────────────────────────────

def _build_phase_modal_view(target_id: str, phase_key: str, page: int) -> dict:
    with db.db() as conn:
        all_preds = db.get_user_finished_predictions_by_stage(
            conn, target_id, _PHASE_STAGES_MAP[phase_key]
        )

    sorted_preds = sorted(all_preds, key=lambda p: p["kickoff_utc"], reverse=True)
    total = len(sorted_preds)
    total_pages = max(1, (total + _PHASE_MODAL_PAGE_SIZE - 1) // _PHASE_MODAL_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * _PHASE_MODAL_PAGE_SIZE
    page_preds = sorted_preds[start:start + _PHASE_MODAL_PAGE_SIZE]

    phase_label = _PHASE_LABELS[phase_key]
    page_info = f"Page {page + 1} of {total_pages}  ·  {total} predictions"
    blocks = [_context(f"*{phase_label} Predictions*  ·  _{page_info}_")]

    if page_preds:
        blocks.extend(_phase_preds_blocks(page_preds, phase_key))
    else:
        blocks.append(_context(f"_No {phase_label} predictions yet._"))

    nav_elements = []
    if page > 0:
        nav_elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "← Previous", "emoji": True},
            "action_id": PHASE_MODAL_PREV_ACTION,
            "value": str(page - 1),
        })
    if page < total_pages - 1:
        nav_elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "Next →", "emoji": True},
            "action_id": PHASE_MODAL_NEXT_ACTION,
            "value": str(page + 1),
        })
    if nav_elements:
        blocks.append(_divider())
        blocks.append({"type": "actions", "elements": nav_elements})

    return {
        "type": "modal",
        "title": {"type": "plain_text", "text": _PHASE_MODAL_TITLES[phase_key], "emoji": True},
        "close": {"type": "plain_text", "text": "Close"},
        "private_metadata": json.dumps({"target_id": target_id, "phase_key": phase_key}),
        "blocks": blocks,
    }


def handle_open_phase_modal(ack, body, client):
    ack()
    value = json.loads(body["actions"][0]["value"])
    target_id = value["target_id"]
    phase_key = value["phase_key"]
    page = value.get("page", 0)
    view = _build_phase_modal_view(target_id, phase_key, page)
    client.views_open(trigger_id=body["trigger_id"], view=view)


def handle_phase_modal_nav(ack, body, client):
    ack()
    metadata = json.loads(body["view"]["private_metadata"])
    target_id = metadata["target_id"]
    phase_key = metadata["phase_key"]
    page = int(body["actions"][0]["value"])
    view = _build_phase_modal_view(target_id, phase_key, page)
    client.views_update(view_id=body["view"]["id"], view=view)


# ── Picks helpers ─────────────────────────────────────────────────────────────

def _picks_text(picks, locked: bool, zebra_knocked_out: bool | None = None) -> str:
    lines = []

    w = picks["winner"]
    w_pts = picks["winner_points"]
    w_line = f":first_place_medal: Winner: *{flag(w)} {w}*"
    if w_pts is not None:
        w_line += f"  → *{w_pts} pts*" if w_pts > 0 else "  → :x: 0 pts"
    else:
        w_line += f"  _(+{TOURNAMENT_PICK_POINTS} if correct)_"
    lines.append(w_line)

    gs = picks["top_scorer"]
    s_pts = picks["scorer_points"]
    s_line = f":athletic_shoe: Golden Boot: *{gs}*"
    if s_pts is not None:
        s_line += f"  → *{s_pts} pts*" if s_pts > 0 else "  → :x: 0 pts"
    else:
        s_line += f"  _(+{TOURNAMENT_PICK_POINTS} if correct)_"
    lines.append(s_line)

    semis = [picks[f"semi{i}"] for i in range(1, 5) if picks[f"semi{i}"]]
    if semis:
        semi_pts = picks["semi_points"]
        semi_str = "  ·  ".join(f"{flag(t)} {t}" for t in semis)
        semi_line = f":four: Semis: {semi_str}"
        if semi_pts is not None:
            semi_line += f"  → *{semi_pts} pts*"
        else:
            semi_line += f"  _(+{SEMI_PICK_POINTS} pts each if correct)_"
        lines.append(semi_line)
    else:
        lines.append(":four: Semis: _(not picked)_")

    guess = picks["group_goals_guess"]
    if guess is not None:
        gg_pts = picks["group_goals_points"]
        gg_line = f":goal_net: Group goals guess: *{guess}*"
        if gg_pts is not None:
            gg_line += f"  → *{gg_pts} pts*" if gg_pts > 0 else "  → :x: 0 pts"
        else:
            gg_line += "  _(pending — scored after group stage)_"
        lines.append(gg_line)

    zebra = picks["zebra"]
    if zebra:
        tier_label = ":black_joker: Wildcard" if picks["zebra_tier"] == "WILDCARD" else "⭐ Bold"
        z_pts = picks["zebra_points"]
        if (z_pts is not None and z_pts == 0) or zebra_knocked_out is True:
            status = "  :skull: *Eliminated!*"
        elif z_pts is not None and z_pts > 0 and zebra_knocked_out is False:
            status = "  :fire: *Still Alive!*"
        else:
            status = ""
        z_line = f":zebra_face: Zebra: *{flag(zebra)} {zebra}* ({tier_label}){status}"
        if z_pts is not None:
            z_line += f"  → *{z_pts} pts*" if z_pts > 0 else "  → :x: 0 pts"
        elif not status:
            z_line += "  _(pending)_"
        lines.append(z_line)

    return "\n".join(lines)


def _picks_locked(conn) -> bool:
    lock_time = db.get_picks_lock_time(conn)
    from app.football import is_kickoff_passed
    return lock_time is not None and is_kickoff_passed(lock_time)


def _lookup_user_by_name(client, username: str) -> str | None:
    try:
        resp = client.users_list()
        for member in resp.get("members", []):
            if member.get("deleted") or member.get("is_bot"):
                continue
            if (member.get("name", "").lower() == username or
                    member.get("profile", {}).get("display_name", "").lower() == username):
                return member["id"]
    except Exception as exc:
        logger.error("Failed to look up user %r: %s", username, exc)
    return None
