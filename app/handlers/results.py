import json

from app import db
from app.config import COMPETITION_NAME
from app.espn import fetch_match_summary, get_goal_scorers, get_match_stats
from app.flags import home, away, flag
from app.football import format_kickoff, format_score, format_score_note, stage_label
from app.odds import format_prob_line, format_underdog_line
from app.scoring import points_label

OPEN_RESULTS_MODAL_ACTION = "open_results_modal"
RESULTS_MODAL_PREV_ACTION = "results_modal_prev"
RESULTS_MODAL_NEXT_ACTION = "results_modal_next"
_EPHEMERAL_PREVIEW = 3
_MODAL_PAGE_SIZE = 4


def _match_blocks(m, pred) -> list:
    blocks = [{"type": "divider"}]
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                f"*{home(m['home_team'])} {format_score(m)} {away(m['away_team'])}{format_score_note(m)}*\n"
                f"{format_kickoff(m['kickoff_utc'])}  ·  {stage_label(m['stage'])}"
            ),
        },
    })

    prob_line = format_prob_line(m)
    ud_line = format_underdog_line(m)
    if prob_line:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": prob_line}]})
    if ud_line:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": ud_line}]})

    try:
        summary = fetch_match_summary(m["external_id"])
        goals = get_goal_scorers(summary)
        stats = get_match_stats(summary)

        if goals:
            home_goals = [g for g in goals if g["team_name"] == m["home_team"]]
            away_goals = [g for g in goals if g["team_name"] == m["away_team"]]
            goal_lines = []
            if home_goals:
                goal_lines.append(flag(m["home_team"]) + "  " + "  ·  ".join(
                    f":soccer: *{g['scorer_name']}* {g['minute']}'{g['suffix']}" for g in home_goals
                ))
            if away_goals:
                goal_lines.append(flag(m["away_team"]) + "  " + "  ·  ".join(
                    f":soccer: *{g['scorer_name']}* {g['minute']}'{g['suffix']}" for g in away_goals
                ))
            if goal_lines:
                blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "\n".join(goal_lines)}]})

        if stats and stats.get("home_possession"):
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": (
                f":bar_chart:  {m['home_team']} {stats['home_possession']}% poss"
                f"  ·  {stats['home_shots_on_target']} shots on target"
                f"  ·  {m['away_team']} {stats['away_possession']}% poss"
                f"  ·  {stats['away_shots_on_target']} shots on target"
            )}]})
    except Exception:
        pass

    if pred:
        pts = points_label(pred["points"])
        pick_icon = ":robot_face:" if pred["is_auto"] else ":pencil:"
        pick_text = f"{pick_icon} Your pick: *{pred['home_score']} - {pred['away_score']}*  →  *{pts}*"
    else:
        pick_text = ":zipper_mouth_face: _(no prediction)_"
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": pick_text}]})

    return blocks


def _build_results_blocks(slack_user_id: str) -> list | None:
    with db.db() as conn:
        matches = db.get_all_finished_matches(conn)
        if not matches:
            return None
        preds = {
            p["match_id"]: p
            for p in db.get_user_predictions_with_matches(conn, slack_user_id)
        }

    blocks = [{"type": "header", "text": {"type": "plain_text", "text": f"🏁 {COMPETITION_NAME} — Recent Results", "emoji": True}}]

    for m in matches[:_EPHEMERAL_PREVIEW]:
        blocks.extend(_match_blocks(m, preds.get(m["id"])))

    if len(matches) > _EPHEMERAL_PREVIEW:
        blocks.append({"type": "divider"})
        blocks.append({"type": "actions", "elements": [{
            "type": "button",
            "text": {"type": "plain_text", "text": f"See all {len(matches)} results →", "emoji": True},
            "action_id": OPEN_RESULTS_MODAL_ACTION,
            "value": "open",
        }]})

    return blocks


def _build_results_modal_view(slack_user_id: str, page: int = 0) -> dict:
    with db.db() as conn:
        matches = db.get_all_finished_matches(conn)
        preds = {
            p["match_id"]: p
            for p in db.get_user_predictions_with_matches(conn, slack_user_id)
        }

    total = len(matches)
    total_pages = max(1, -(-total // _MODAL_PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    start = page * _MODAL_PAGE_SIZE

    blocks = []
    for m in matches[start:start + _MODAL_PAGE_SIZE]:
        blocks.extend(_match_blocks(m, preds.get(m["id"])))

    nav_elements = []
    if page > 0:
        nav_elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "← Newer", "emoji": True},
            "action_id": RESULTS_MODAL_PREV_ACTION,
            "value": str(page - 1),
        })
    if page < total_pages - 1:
        nav_elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "Older →", "emoji": True},
            "action_id": RESULTS_MODAL_NEXT_ACTION,
            "value": str(page + 1),
        })
    if nav_elements:
        blocks.append({"type": "divider"})
        blocks.append({"type": "actions", "elements": nav_elements})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"_Page {page + 1} of {total_pages}  ·  {total} results total_"}]})

    return {
        "type": "modal",
        "title": {"type": "plain_text", "text": "Match Results", "emoji": True},
        "close": {"type": "plain_text", "text": "Close"},
        "private_metadata": json.dumps({"user_id": slack_user_id}),
        "blocks": blocks,
    }


def handle_results(respond, body):
    slack_user_id = body["user_id"]
    blocks = _build_results_blocks(slack_user_id)
    if blocks is None:
        respond(response_type="ephemeral", text="No results yet — check back after the first match!")
        return
    respond(response_type="ephemeral", blocks=blocks, text=f"{COMPETITION_NAME} — Recent Results")


def handle_open_results_modal(ack, body, client):
    ack()
    view = _build_results_modal_view(body["user"]["id"], page=0)
    client.views_open(trigger_id=body["trigger_id"], view=view)


def handle_results_modal_nav(ack, body, client):
    ack()
    page = int(body["actions"][0]["value"])
    view = _build_results_modal_view(body["user"]["id"], page=page)
    client.views_update(view_id=body["view"]["id"], view=view)
