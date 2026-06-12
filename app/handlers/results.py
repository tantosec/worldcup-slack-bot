from app import db
from app.flags import home, away
from app.football import format_kickoff, format_score, format_score_note
from app.scoring import points_label


def handle_results(respond, body):
    slack_user_id = body["user_id"]

    with db.db() as conn:
        matches = db.get_recent_matches(conn, limit=5)

        lines = [":checkered_flag: *FIFA World Cup 2026 — Recent Results*\n"]
        for m in matches:
            score_line = (
                f"*{home(m['home_team'])} {format_score(m)} {away(m['away_team'])}{format_score_note(m)}*"
                f"  ·  _{format_kickoff(m['kickoff_utc'])}_"
            )

            pred = db.get_user_prediction(conn, slack_user_id, m["id"])
            if pred:
                your_pts = points_label(pred["points"])
                pred_line = f"  :pencil: Your pick: {pred['home_score']} - {pred['away_score']}  →  *{your_pts}*"
            else:
                pred_line = "  :zipper_mouth_face: _(no prediction)_"

            lines.append(f"{score_line}\n{pred_line}")

    if len(lines) == 1:
        respond(response_type="ephemeral", text="No results yet — check back after the first match!")
        return

    respond(response_type="ephemeral", text="\n\n".join(lines))
