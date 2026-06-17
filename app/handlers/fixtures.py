import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app import db
from app.espn import fetch_match_summary, get_goal_scorers, get_match_stats
from app.flags import home, away, vs
from app.football import format_kickoff, format_score, is_kickoff_passed, stage_label, estimate_match_time
from app.odds import format_prob_line, format_underdog_line

SHOW_MORE_ACTION = "fixtures_show_more"


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


def _upcoming_blocks(matches: list, user_preds: dict) -> list:
    blocks = []
    for m in matches:
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
    return blocks


def _build_fixtures_blocks(slack_user_id: str, show_all_upcoming: bool = False) -> list | None:
    with db.db() as conn:
        live_matches = db.get_live_matches(conn)
        upcoming = db.get_upcoming_matches(conn, limit=20)
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

    _tz = ZoneInfo(os.getenv("DISPLAY_TIMEZONE", "Australia/Sydney"))
    today_local = datetime.now(tz=_tz).date().isoformat()
    # Compare kickoff UTC dates converted to display timezone
    def _kickoff_local_date(m) -> str:
        return datetime.fromisoformat(m["kickoff_utc"].replace("Z", "+00:00")).astimezone(_tz).date().isoformat()

    today_upcoming = [m for m in upcoming if _kickoff_local_date(m) == today_local]
    later_upcoming = [m for m in upcoming if _kickoff_local_date(m) > today_local]

    # If nothing is on today, surface the next day's matches so the list isn't empty
    if not today_upcoming and later_upcoming and not show_all_upcoming:
        next_day = _kickoff_local_date(later_upcoming[0])
        today_upcoming = [m for m in later_upcoming if _kickoff_local_date(m) == next_day]
        later_upcoming = [m for m in later_upcoming if _kickoff_local_date(m) > next_day]

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "FIFA World Cup 2026 — Fixtures", "emoji": True}},
    ]

    # ── Live matches ─────────────────────────────────────────────────────────────
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

    # ── Upcoming matches ──────────────────────────────────────────────────────────
    candidates = today_upcoming + (later_upcoming if show_all_upcoming else [])
    remaining_after = [] if not show_all_upcoming else []

    # Fit as many matches as possible within Slack's 50-block limit (keep 3 buffer)
    MAX_BLOCKS = 47
    upcoming_section_overhead = 3  # divider + header + divider
    fit = []
    spare = MAX_BLOCKS - len(blocks) - upcoming_section_overhead
    for m in candidates:
        cost = 1 + len([x for x in [format_prob_line(m), format_underdog_line(m, action=True)] if x])
        if spare - cost < 1:  # keep 1 spare for the overflow note or button
            remaining_after = candidates[candidates.index(m):]
            break
        fit.append(m)
        spare -= cost

    if fit or later_upcoming:
        blocks += [_divider(), _section(":calendar:  *Upcoming*"), _divider()]
        blocks += _upcoming_blocks(fit, user_preds)

        if not show_all_upcoming and later_upcoming:
            blocks.append({
                "type": "actions",
                "elements": [{
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": f"Show {len(later_upcoming)} more upcoming matches",
                        "emoji": True,
                    },
                    "action_id": SHOW_MORE_ACTION,
                }],
            })
        elif remaining_after:
            blocks.append(_context(f"_…and {len(remaining_after)} more matches not shown — check the FIFA app for the full schedule_"))

    return blocks


def handle_fixtures(respond, body):
    slack_user_id = body["user_id"]
    blocks = _build_fixtures_blocks(slack_user_id)
    if blocks is None:
        respond(response_type="ephemeral", text="No fixtures found. The fixture list may not be loaded yet.")
        return
    respond(response_type="ephemeral", blocks=blocks, text="FIFA World Cup 2026 — Fixtures")


def handle_fixtures_show_more(ack, respond, body):
    ack()
    slack_user_id = body["user"]["id"]
    blocks = _build_fixtures_blocks(slack_user_id, show_all_upcoming=True)
    if blocks is None:
        respond(response_type="ephemeral", text="No fixtures found.")
        return
    respond(replace_original=True, blocks=blocks, text="FIFA World Cup 2026 — Fixtures")
