from app import db
from app.flags import home, away
from app.football import format_kickoff, format_score, format_score_note
from app.scoring import points_label


def handle_results(respond, body):
    slack_user_id = body["user_id"]

    with db.db() as conn:
        matches = db.get_recent_matches(conn, limit=5)

        if not matches:
            respond(response_type="ephemeral", text="No results yet — check back after the first match!")
            return

        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": "🏁 FIFA World Cup 2026 — Recent Results", "emoji": True}},
        ]

        for m in matches:
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{home(m['home_team'])} {format_score(m)} {away(m['away_team'])}{format_score_note(m)}*\n"
                        f"{format_kickoff(m['kickoff_utc'])}"
                    ),
                },
            })

            pred = db.get_user_prediction(conn, slack_user_id, m["id"])
            if pred:
                pts = points_label(pred["points"])
                pick_text = f":pencil: Your pick: *{pred['home_score']} - {pred['away_score']}*  →  *{pts}*"
            else:
                pick_text = ":zipper_mouth_face: _(no prediction)_"

            blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": pick_text}],
            })

    respond(response_type="ephemeral", blocks=blocks, text="FIFA World Cup 2026 — Recent Results")
