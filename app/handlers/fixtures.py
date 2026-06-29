import json

from app import db
from app.espn import fetch_match_summary, get_goal_scorers, get_match_stats, get_display_clock
from app.flags import home, away, vs
from app.football import format_kickoff, format_score, stage_label, estimate_match_time
from app.odds import format_prob_line, format_underdog_line

OPEN_FIXTURES_MODAL_ACTION = "open_fixtures_modal"
FIXTURES_MODAL_PREV_ACTION = "fixtures_modal_prev"
FIXTURES_MODAL_NEXT_ACTION = "fixtures_modal_next"
_EPHEMERAL_PREVIEW = 3
_MODAL_PAGE_SIZE = 5


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _context(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


def _divider() -> dict:
    return {"type": "divider"}


def _venue_line(m) -> str | None:
    venue_name = m["venue_name"]
    if not venue_name:
        return None
    parts = [venue_name]
    city = m["venue_city"]
    if city:
        parts.append(city)
    return ":round_pushpin: " + ", ".join(parts)


def _enrich_live(match: dict) -> tuple:
    try:
        summary = fetch_match_summary(match["external_id"])
        scorers = get_goal_scorers(summary)
        stats = get_match_stats(summary)
        display_clock = get_display_clock(summary)
        return scorers, stats, display_clock
    except Exception:
        return [], None, None


def _upcoming_blocks(matches: list, user_preds: dict) -> list:
    blocks = []
    for m in matches:
        pred = user_preds.get(m["id"])
        if pred:
            pick_icon = ":robot_face:" if pred["is_auto"] else ":pencil:"
            pick_line = f"{pick_icon} Your pick: *{pred['home_score']} - {pred['away_score']}*"
        else:
            pick_line = ":crystal_ball: _No prediction yet — use `/predict`_"
        venue = _venue_line(m)
        venue_suffix = f"  ·  {venue}" if venue else ""
        blocks.append(_section(
            f"*{vs(m['home_team'], m['away_team'])}*\n"
            f"{format_kickoff(m['kickoff_utc'])}  ·  {stage_label(m['stage'])}{venue_suffix}\n"
            f"{pick_line}"
        ))
        context_parts = [x for x in [format_prob_line(m), format_underdog_line(m, action=True)] if x]
        for part in context_parts:
            blocks.append(_context(part))
    return blocks


def _build_fixtures_blocks(slack_user_id: str) -> list | None:
    with db.db() as conn:
        live_matches = db.get_live_matches(conn)
        upcoming = db.get_all_upcoming_matches(conn)
        user_preds = {
            p["match_id"]: p
            for p in db.get_user_predictions_with_matches(conn, slack_user_id)
        }
        live_preds = {
            m["id"]: db.get_match_predictions_all_users(conn, m["id"])
            for m in live_matches
        }

    if not live_matches and not upcoming:
        return None

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "FIFA World Cup 2026 — Fixtures", "emoji": True}},
    ]

    if live_matches:
        blocks += [_divider(), _section(":red_circle:  *LIVE NOW*"), _divider()]

        for m in live_matches:
            scorers, stats, display_clock = _enrich_live(m)
            match_time = estimate_match_time(m["kickoff_utc"], m["status"], display_clock=display_clock)

            blocks.append(_section(
                f"*{home(m['home_team'])} {format_score(m)} {away(m['away_team'])}*"
                f"  ·  {match_time}  ·  {stage_label(m['stage'])}"
            ))

            venue = _venue_line(m)
            if venue:
                blocks.append(_context(venue))

            if scorers:
                scorer_parts = [
                    f":soccer: {s['scorer_name']} {s['minute']}'{s['suffix']} _({s['team_name']})_"
                    for s in scorers
                ]
                blocks.append(_context("  ·  ".join(scorer_parts)))

            if stats:
                stat_parts = []
                if stats.get("home_possession") and stats.get("away_possession"):
                    stat_parts.append(f"Poss {stats['home_possession']} / {stats['away_possession']}")
                if stats.get("home_total_shots") and stats.get("away_total_shots"):
                    shot_str = f"Shots {stats['home_total_shots']} - {stats['away_total_shots']}"
                    if stats.get("home_shots_on_target") and stats.get("away_shots_on_target"):
                        shot_str += f" ({stats['home_shots_on_target']} - {stats['away_shots_on_target']} on target)"
                    stat_parts.append(shot_str)
                if stat_parts:
                    blocks.append(_context("  ·  ".join(stat_parts)))

            context_parts = [x for x in [format_prob_line(m), format_underdog_line(m)] if x]
            for part in context_parts:
                blocks.append(_context(part))

            preds = live_preds.get(m["id"], [])
            predicted = [
                (r["slack_user_id"], r["home_score"], r["away_score"], r["is_auto"])
                for r in preds if r["home_score"] is not None
            ]
            no_pick = [r["slack_user_id"] for r in preds if r["home_score"] is None]

            if predicted:
                fields = []
                for uid, h, a, is_auto in predicted:
                    fields.append({"type": "mrkdwn", "text": f"<@{uid}>"})
                    score_label = f"`{h} - {a}` :robot_face:" if is_auto else f"`{h} - {a}`"
                    fields.append({"type": "mrkdwn", "text": score_label})
                blocks.append({"type": "section", "fields": fields[:10]})
            if no_pick:
                blocks.append(_context(":ghost:  No pick: " + "  ".join(f"<@{uid}>" for uid in no_pick)))

    if upcoming:
        blocks += [_divider(), _section(":calendar:  *Upcoming*"), _divider()]
        blocks += _upcoming_blocks(upcoming[:_EPHEMERAL_PREVIEW], user_preds)
        if len(upcoming) > _EPHEMERAL_PREVIEW:
            blocks.append({"type": "actions", "elements": [{
                "type": "button",
                "text": {"type": "plain_text", "text": f"See all {len(upcoming)} upcoming →", "emoji": True},
                "action_id": OPEN_FIXTURES_MODAL_ACTION,
                "value": "open",
            }]})

    return blocks


def _build_fixtures_modal_view(slack_user_id: str, page: int = 0) -> dict:
    with db.db() as conn:
        upcoming = db.get_all_upcoming_matches(conn)
        user_preds = {
            p["match_id"]: p
            for p in db.get_user_predictions_with_matches(conn, slack_user_id)
        }

    total = len(upcoming)
    total_pages = max(1, -(-total // _MODAL_PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    start = page * _MODAL_PAGE_SIZE

    blocks = _upcoming_blocks(upcoming[start:start + _MODAL_PAGE_SIZE], user_preds)

    nav_elements = []
    if page > 0:
        nav_elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "← Previous", "emoji": True},
            "action_id": FIXTURES_MODAL_PREV_ACTION,
            "value": str(page - 1),
        })
    if page < total_pages - 1:
        nav_elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "Next →", "emoji": True},
            "action_id": FIXTURES_MODAL_NEXT_ACTION,
            "value": str(page + 1),
        })
    if nav_elements:
        blocks.append(_divider())
        blocks.append({"type": "actions", "elements": nav_elements})
    blocks.append(_context(f"_Page {page + 1} of {total_pages}  ·  {total} upcoming matches_"))

    return {
        "type": "modal",
        "title": {"type": "plain_text", "text": "Upcoming Fixtures", "emoji": True},
        "close": {"type": "plain_text", "text": "Close"},
        "private_metadata": json.dumps({"user_id": slack_user_id}),
        "blocks": blocks,
    }


def handle_fixtures(respond, body):
    slack_user_id = body["user_id"]
    blocks = _build_fixtures_blocks(slack_user_id)
    if blocks is None:
        respond(response_type="ephemeral", text="No fixtures found. The fixture list may not be loaded yet.")
        return
    respond(response_type="ephemeral", blocks=blocks, text="FIFA World Cup 2026 — Fixtures")


def handle_open_fixtures_modal(ack, body, client):
    ack()
    view = _build_fixtures_modal_view(body["user"]["id"], page=0)
    client.views_open(trigger_id=body["trigger_id"], view=view)


def handle_fixtures_modal_nav(ack, body, client):
    ack()
    page = int(body["actions"][0]["value"])
    view = _build_fixtures_modal_view(body["user"]["id"], page=page)
    client.views_update(view_id=body["view"]["id"], view=view)
