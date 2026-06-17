from app import db
from app.espn import fetch_match_summary, get_goal_scorers, get_match_stats
from app.flags import home, away, vs
from app.football import format_kickoff, format_score, is_kickoff_passed, stage_label, estimate_match_time
from app.odds import format_prob_line, format_underdog_line


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _context(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


def _divider() -> dict:
    return {"type": "divider"}


def _venue_line(m: dict) -> str | None:
    if not m.get("venue_name"):
        return None
    parts = [m["venue_name"]]
    if m.get("venue_city"):
        parts.append(m["venue_city"])
    return ":round_pushpin: " + ", ".join(parts)


def _enrich_live(match: dict) -> tuple:
    """Fetch ESPN summary for a live match. Returns (scorers, stats, display_clock)."""
    try:
        summary = fetch_match_summary(match["external_id"])
        scorers = get_goal_scorers(summary)
        stats = get_match_stats(summary)
        display_clock = (
            summary.get("header", [{}])[0]
            .get("competitions", [{}])[0]
            .get("status", {})
            .get("displayClock")
        )
        return scorers, stats, display_clock
    except Exception:
        return [], None, None


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
                    f":soccer: {s['scorer_name']} {s['minute']}' _({s['team_name']})_"
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

    respond(response_type="ephemeral", blocks=blocks, text="FIFA World Cup 2026 — Fixtures")
