from app import db
from app.espn import fetch_match_summary, get_goal_scorers, get_match_stats
from app.flags import home, away, flag
from app.football import format_kickoff, format_score, format_score_note, stage_label
from app.odds import format_prob_line, format_underdog_line
from app.scoring import points_label

RESULTS_PAGE_ACTION = "results_page"
_PAGE_SIZE = 4


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
        pick_text = f":pencil: Your pick: *{pred['home_score']} - {pred['away_score']}*  →  *{pts}*"
    else:
        pick_text = ":zipper_mouth_face: _(no prediction)_"
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": pick_text}]})

    return blocks


def _build_results_blocks(slack_user_id: str, page: int = 0, response_url: str = "") -> list | None:
    with db.db() as conn:
        matches = db.get_all_finished_matches(conn)
        if not matches:
            return None
        preds = {
            p["match_id"]: p
            for p in db.get_user_predictions_with_matches(conn, slack_user_id)
        }

    total = len(matches)
    total_pages = max(1, -(-total // _PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    start = page * _PAGE_SIZE
    page_matches = matches[start:start + _PAGE_SIZE]

    blocks = [{"type": "header", "text": {"type": "plain_text", "text": "🏁 FIFA World Cup 2026 — Recent Results", "emoji": True}}]

    for m in page_matches:
        pred = preds.get(m["id"])
        blocks.extend(_match_blocks(m, pred))

    nav_elements = []
    if page > 0:
        nav_elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "← Newer", "emoji": True},
            "action_id": RESULTS_PAGE_ACTION,
            "value": f"{page - 1}|{response_url}",
        })
    if page < total_pages - 1:
        nav_elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "Older →", "emoji": True},
            "action_id": RESULTS_PAGE_ACTION,
            "value": f"{page + 1}|{response_url}",
        })
    if nav_elements:
        blocks.append({"type": "divider"})
        blocks.append({"type": "actions", "elements": nav_elements})
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"_Page {page + 1} of {total_pages}  ·  {total} results total_"}]})

    return blocks


def handle_results(respond, body):
    slack_user_id = body["user_id"]
    response_url = body.get("response_url", "")
    blocks = _build_results_blocks(slack_user_id, response_url=response_url)
    if blocks is None:
        respond(response_type="ephemeral", text="No results yet — check back after the first match!")
        return
    respond(response_type="ephemeral", blocks=blocks, text="FIFA World Cup 2026 — Recent Results")


def handle_results_page(ack, body):
    ack()
    from slack_sdk.webhook import WebhookClient
    slack_user_id = body["user"]["id"]
    raw_value = body["actions"][0]["value"]
    page_str, _, response_url = raw_value.partition("|")
    page = int(page_str)
    blocks = _build_results_blocks(slack_user_id, page=page, response_url=response_url)
    if blocks is None:
        if response_url:
            WebhookClient(response_url).send(replace_original=True, text="No results found.")
        return
    if response_url:
        WebhookClient(response_url).send(replace_original=True, blocks=blocks, text="FIFA World Cup 2026 — Recent Results")
