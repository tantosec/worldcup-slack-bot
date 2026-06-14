from app import db
from app.flags import home, away, vs
from app.football import format_kickoff, format_score, is_kickoff_passed, stage_label, estimate_match_time
from app.odds import format_prob_line, format_underdog_line


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _context(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


def _divider() -> dict:
    return {"type": "divider"}


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

    if not live_matches and not upcoming:
        respond(response_type="ephemeral", text="No fixtures found. The fixture list may not be loaded yet.")
        return

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "FIFA World Cup 2026 — Fixtures", "emoji": True}},
    ]

    if live_matches:
        blocks += [_divider(), _section(":red_circle:  *LIVE NOW*"), _divider()]

        for m in live_matches:
            match_time = estimate_match_time(m["kickoff_utc"], m["status"])
            blocks.append(_section(
                f"*{home(m['home_team'])} {format_score(m)} {away(m['away_team'])}*"
                f"  ·  {match_time}  ·  {stage_label(m['stage'])}"
            ))

            context_parts = [x for x in [format_prob_line(m), format_underdog_line(m)] if x]
            for part in context_parts:
                blocks.append(_context(part))

            preds = live_preds.get(m["id"], [])
            predicted = [(r["slack_user_id"], r["home_score"], r["away_score"]) for r in preds if r["home_score"] is not None]
            no_pick = [r["slack_user_id"] for r in preds if r["home_score"] is None]

            if predicted:
                fields = []
                for uid, h, a in predicted:
                    fields.append({"type": "mrkdwn", "text": f"<@{uid}>"})
                    fields.append({"type": "mrkdwn", "text": f"`{h} - {a}`"})
                blocks.append({"type": "section", "fields": fields[:10]})
            if no_pick:
                blocks.append(_context(":ghost:  No pick: " + "  ".join(f"<@{uid}>" for uid in no_pick)))

    if upcoming:
        blocks += [_divider(), _section(":calendar:  *Upcoming*"), _divider()]

        for m in upcoming:
            pred = user_preds.get(m["id"])
            pick_line = (
                f":pencil: Your pick: *{pred['home_score']} - {pred['away_score']}*"
                if pred
                else ":crystal_ball: _No prediction yet — use `/predict`_"
            )
            blocks.append(_section(
                f"*{vs(m['home_team'], m['away_team'])}*\n"
                f"{format_kickoff(m['kickoff_utc'])}  ·  {stage_label(m['stage'])}\n"
                f"{pick_line}"
            ))

            context_parts = [x for x in [format_prob_line(m), format_underdog_line(m, action=True)] if x]
            for part in context_parts:
                blocks.append(_context(part))

    respond(response_type="ephemeral", blocks=blocks, text="FIFA World Cup 2026 — Fixtures")
