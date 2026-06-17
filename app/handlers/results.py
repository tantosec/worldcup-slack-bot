from app import db
from app.espn import fetch_match_summary, get_goal_scorers, get_match_stats
from app.flags import home, away, flag
from app.football import format_kickoff, format_score, format_score_note, stage_label
from app.odds import format_prob_line, format_underdog_line
from app.scoring import points_label

SHOW_MORE_RESULTS_ACTION = "results_show_more"
_DEFAULT_SHOWN = 4
_MAX_BLOCKS = 47


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
                    f":soccer: *{g['scorer_name']}* {g['minute']}'" for g in home_goals
                ))
            if away_goals:
                goal_lines.append(flag(m["away_team"]) + "  " + "  ·  ".join(
                    f":soccer: *{g['scorer_name']}* {g['minute']}'" for g in away_goals
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


def _build_results_blocks(slack_user_id: str, show_all: bool = False) -> list | None:
    with db.db() as conn:
        matches = db.get_recent_matches(conn, limit=20)
        if not matches:
            return None
        preds = {
            p["match_id"]: p
            for p in db.get_user_predictions_with_matches(conn, slack_user_id)
        }

    header = [{"type": "header", "text": {"type": "plain_text", "text": "🏁 FIFA World Cup 2026 — Recent Results", "emoji": True}}]
    blocks = list(header)
    spare = _MAX_BLOCKS - len(blocks)

    candidates = matches if show_all else matches[:_DEFAULT_SHOWN]
    remaining_count = max(0, len(matches) - _DEFAULT_SHOWN) if not show_all else 0
    overflow_count = 0

    for m in candidates:
        pred = preds.get(m["id"])
        mb = _match_blocks(m, pred)
        if spare - len(mb) < 1:
            overflow_count = candidates[candidates.index(m):]
            overflow_count = len(overflow_count)
            break
        blocks.extend(mb)
        spare -= len(mb)

    if not show_all and remaining_count > 0:
        blocks.append({
            "type": "actions",
            "elements": [{
                "type": "button",
                "text": {"type": "plain_text", "text": f"Show {remaining_count} more results", "emoji": True},
                "action_id": SHOW_MORE_RESULTS_ACTION,
            }],
        })
    elif overflow_count:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"_…and {overflow_count} more results not shown_"}]})

    return blocks


def handle_results(respond, body):
    slack_user_id = body["user_id"]
    blocks = _build_results_blocks(slack_user_id)
    if blocks is None:
        respond(response_type="ephemeral", text="No results yet — check back after the first match!")
        return
    respond(response_type="ephemeral", blocks=blocks, text="FIFA World Cup 2026 — Recent Results")


def handle_results_show_more(ack, respond, body):
    ack()
    slack_user_id = body["user"]["id"]
    blocks = _build_results_blocks(slack_user_id, show_all=True)
    if blocks is None:
        respond(response_type="ephemeral", text="No results found.")
        return
    respond(replace_original=True, blocks=blocks, text="FIFA World Cup 2026 — Recent Results")
