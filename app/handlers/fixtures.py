from app import db
from app.flags import home, away, vs
from app.football import format_kickoff, format_score, is_kickoff_passed, stage_label, estimate_match_time
from app.odds import format_prob_line, format_underdog_line


def handle_fixtures(respond, body):
    slack_user_id = body["user_id"]

    with db.db() as conn:
        live_matches = db.get_live_matches(conn)
        upcoming = db.get_upcoming_matches(conn, limit=8)
        user_preds = {
            p["match_id"]: p
            for p in db.get_user_predictions_with_matches(conn, slack_user_id)
        }
        live_preds = {
            m["id"]: db.get_match_predictions_all_users(conn, m["id"])
            for m in live_matches
        }

    lines = [":soccer: *FIFA World Cup 2026 — Fixtures*"]

    if live_matches:
        lines.append("\n*━━━━━━━━━━━━━━━━━━━━*")
        lines.append(":red_circle: *LIVE*")
        lines.append("*━━━━━━━━━━━━━━━━━━━━*\n")

        for m in live_matches:
            match_time = estimate_match_time(m["kickoff_utc"], m["status"])
            lines.append(
                f"*{home(m['home_team'])} {format_score(m)} {away(m['away_team'])}*"
                f"  ·  {match_time}  ·  {stage_label(m['stage'])}"
            )
            prob_line = format_prob_line(m)
            if prob_line:
                lines.append(prob_line)
            ud_line = format_underdog_line(m)
            if ud_line:
                lines.append(ud_line)

            preds = live_preds.get(m["id"], [])
            predicted = [(r["slack_user_id"], r["home_score"], r["away_score"]) for r in preds if r["home_score"] is not None]
            no_pick = [r["slack_user_id"] for r in preds if r["home_score"] is None]

            if predicted:
                pred_str = "  ·  ".join(f"<@{uid}>: {h} - {a}" for uid, h, a in predicted)
                lines.append(f"  :dart:  {pred_str}")
            if no_pick:
                no_pick_str = "  ·  ".join(f"<@{uid}>" for uid in no_pick)
                lines.append(f"  :ghost:  No pick: {no_pick_str}")

            lines.append("")

    if upcoming:
        if live_matches:
            lines.append("*━━━━━━━━━━━━━━━━━━━━*")
        lines.append(":calendar: *Upcoming*")
        if live_matches:
            lines.append("*━━━━━━━━━━━━━━━━━━━━*\n")

        for m in upcoming:
            line = (
                f"*{vs(m['home_team'], m['away_team'])}*\n"
                f"  :calendar: {format_kickoff(m['kickoff_utc'])}  ·  {stage_label(m['stage'])}"
            )
            prob_line = format_prob_line(m)
            if prob_line:
                line += f"\n  {prob_line}"
            ud_line = format_underdog_line(m, action=True)
            if ud_line:
                line += f"\n  {ud_line}"
            pred = user_preds.get(m["id"])
            if pred:
                line += f"\n  :pencil:  Your pick: *{pred['home_score']} - {pred['away_score']}*"
            else:
                line += "\n  :crystal_ball:  No prediction yet — use `/predict`"
            lines.append(line)

    if not live_matches and not upcoming:
        respond(response_type="ephemeral", text="No fixtures found. The fixture list may not be loaded yet.")
        return

    respond(response_type="ephemeral", text="\n\n".join(lines))
