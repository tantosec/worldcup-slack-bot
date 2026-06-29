import re
import logging

from app import db
from app.flags import flag, home, away, vs
from app.football import format_score, format_score_note, format_kickoff, stage_label
from app.odds import format_prob_line, format_underdog_line, get_underdog
from app.scoring import TOURNAMENT_PICK_POINTS, SEMI_PICK_POINTS

logger = logging.getLogger(__name__)

MYSTATS_UPCOMING_PAGE_ACTION = "mystats_upcoming_page"
_UPCOMING_PAGE_SIZE = 5


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _context(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


def _divider() -> dict:
    return {"type": "divider"}


def _build_me_blocks(target_id: str, caller_id: str, client, upcoming_page: int = 0) -> tuple[list, str]:
    """Build blocks for /mystats. Returns (blocks, title)."""
    viewing_other = target_id != caller_id

    with db.db() as conn:
        stats = db.get_user_match_stats(conn, target_id)
        picks = db.get_tournament_pick(conn, target_id)
        rank, total_points = db.get_user_rank_and_total(conn, target_id)
        total_players = len(db.get_enrolled_users(conn))
        picks_locked = _picks_locked(conn)
        picks_revealed = db.picks_reveal_already_sent(conn)
        finished_preds = db.get_user_finished_predictions(conn, target_id)
        upcoming_preds = [] if viewing_other else db.get_user_upcoming_predictions(conn, target_id)
        missed, still_to_predict = (0, 0) if viewing_other else db.get_user_prediction_gaps(conn, target_id)

    if viewing_other:
        try:
            user_info = client.users_info(user=target_id)
            display_name = (
                user_info["user"]["profile"].get("display_name")
                or user_info["user"]["profile"].get("real_name")
                or "Unknown"
            )
            header_name = f"{display_name}'s"
        except Exception:
            header_name = "Their"
    else:
        header_name = "Your"

    match_pts = stats["match_points"] or 0
    tournament_pts = (total_points or 0) - match_pts

    bonus_fields = []
    if picks:
        if picks["winner_points"] is not None:
            bonus_fields.append({"type": "mrkdwn", "text": f":first_place_medal: *Winner*\n{picks['winner_points']} pts"})
        if picks["scorer_points"] is not None:
            bonus_fields.append({"type": "mrkdwn", "text": f":athletic_shoe: *Golden Boot*\n{picks['scorer_points']} pts"})
        if picks["zebra_points"] is not None:
            bonus_fields.append({"type": "mrkdwn", "text": f":zebra_face: *Zebra*\n{picks['zebra_points']} pts"})
        if picks["semi_points"] is not None:
            bonus_fields.append({"type": "mrkdwn", "text": f":four: *Semis*\n{picks['semi_points']} pts"})
        if picks["group_goals_points"] is not None:
            bonus_fields.append({"type": "mrkdwn", "text": f":goal_net: *Group goals*\n{picks['group_goals_points']} pts"})

    title = f"📊 {header_name} World Cup 2026 Stats"
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": title, "emoji": True}},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f":trophy: *Rank*\n#{rank} of {total_players}"},
                {"type": "mrkdwn", "text": f"*Total*\n{total_points} pts"},
                {"type": "mrkdwn", "text": f":soccer: *Match pts*\n{match_pts}"},
                {"type": "mrkdwn", "text": f":crystal_ball: *Tournament pts*\n{tournament_pts}"},
            ],
        },
    ]
    if bonus_fields:
        blocks.append({"type": "section", "fields": bonus_fields})

    # ── Tournament Picks ──────────────────────────────────────────────────────
    blocks += [_divider(), _section("🔮  *Tournament Picks*")]

    if picks and (not viewing_other or picks_revealed):
        blocks.append(_section(_picks_text(picks, picks_locked)))
    elif viewing_other and not picks_revealed:
        blocks.append(_context("_Picks are revealed after Matchday 2 locks._"))
    else:
        blocks.append(_context("_(not submitted yet)_"))

    # ── Match Predictions ─────────────────────────────────────────────────────
    count_str = f"{len(finished_preds)} played"
    if upcoming_preds:
        count_str += f"  ·  {len(upcoming_preds)} upcoming"
    if not viewing_other:
        if still_to_predict:
            count_str += f"  ·  :pencil: {still_to_predict} to predict"
        if missed:
            count_str += f"  ·  :x: {missed} missed"
    blocks += [_divider(), _section(f"⚽  *Match Predictions* ({count_str})")]

    if finished_preds:
        by_stage = {}
        for p in finished_preds:
            by_stage.setdefault(p["stage"], []).append(p)

        for stage, stage_preds in by_stage.items():
            blocks.append(_context(f"*{stage_label(stage)}*"))
            pairs = []
            for p in stage_preds:
                actual = format_score(p) + format_score_note(p)
                pts = p["points"] or 0
                pred_str = f"{p['pred_home']} - {p['pred_away']}"

                if p["pred_home"] == p["act_home"] and p["pred_away"] == p["act_away"]:
                    icon = ":dart:"
                elif pts > 0:
                    icon = ":white_check_mark:"
                else:
                    icon = ":x:"

                upset_flag = ""
                if pts > 0:
                    underdog = get_underdog(p)
                    if underdog:
                        underdog_won = (
                            (underdog == p["home_team"] and p["home_score"] > p["away_score"]) or
                            (underdog == p["away_team"] and p["away_score"] > p["home_score"])
                        )
                        pred_underdog_wins = (
                            (underdog == p["home_team"] and p["pred_home"] > p["pred_away"]) or
                            (underdog == p["away_team"] and p["pred_away"] > p["pred_home"])
                        )
                        if underdog_won and pred_underdog_wins:
                            upset_flag = "  :zap:"

                pairs.append((
                    f"{icon}  {home(p['home_team'])} {actual} {away(p['away_team'])}",
                    f"`{pred_str}`  *+{pts} pts*{upset_flag}",
                ))

            for i in range(0, len(pairs), 5):
                chunk = pairs[i:i + 5]
                fields = []
                for left, right in chunk:
                    fields.append({"type": "mrkdwn", "text": left})
                    fields.append({"type": "mrkdwn", "text": right})
                blocks.append({"type": "section", "fields": fields})
    else:
        blocks.append(_context("_No finished matches predicted yet._"))

    # ── Upcoming (paginated) ──────────────────────────────────────────────────
    if upcoming_preds:
        total_upcoming = len(upcoming_preds)
        total_pages = max(1, -(-total_upcoming // _UPCOMING_PAGE_SIZE))
        upcoming_page = max(0, min(upcoming_page, total_pages - 1))
        start = upcoming_page * _UPCOMING_PAGE_SIZE
        shown = upcoming_preds[start:start + _UPCOMING_PAGE_SIZE]

        blocks += [_divider(), _section("⏰  *Upcoming*")]
        for p in shown:
            venue_parts = [x for x in [p["venue_name"], p["venue_city"]] if x]
            venue_str = ("  ·  " + ", ".join(venue_parts)) if venue_parts else ""
            blocks.append(_section(
                f"*{vs(p['home_team'], p['away_team'])}*  ·  {format_kickoff(p['kickoff_utc'])}{venue_str}\n"
                f":pencil: Your pick: *{p['pred_home']} - {p['pred_away']}*"
            ))
            context_parts = [x for x in [format_prob_line(p), format_underdog_line(p, action=True)] if x]
            if context_parts:
                blocks.append(_context("  ·  ".join(context_parts)))

        nav_elements = []
        if upcoming_page > 0:
            nav_elements.append({
                "type": "button",
                "text": {"type": "plain_text", "text": "← Previous", "emoji": True},
                "action_id": MYSTATS_UPCOMING_PAGE_ACTION,
                "value": f"{target_id}:{upcoming_page - 1}",
            })
        if upcoming_page < total_pages - 1:
            nav_elements.append({
                "type": "button",
                "text": {"type": "plain_text", "text": "Next →", "emoji": True},
                "action_id": MYSTATS_UPCOMING_PAGE_ACTION,
                "value": f"{target_id}:{upcoming_page + 1}",
            })
        if nav_elements:
            blocks.append({"type": "actions", "elements": nav_elements})
        if total_pages > 1:
            blocks.append(_context(f"_Page {upcoming_page + 1} of {total_pages}  ·  {total_upcoming} upcoming predictions_"))

    return blocks, title


def handle_me(respond, body, client):
    caller_id = body["user_id"]
    text = (body.get("text") or "").strip()
    logger.info("/mystats called by %s with text: %r", caller_id, text)

    target_id = caller_id

    mention = re.search(r"<@([A-Z0-9]+)(?:\|[^>]+)?>", text)
    if mention:
        target_id = mention.group(1)
    elif text.startswith("@"):
        username = text[1:].lower()
        target_id = _lookup_user_by_name(client, username) or caller_id

    with db.db() as conn:
        if not db.is_enrolled(conn, target_id):
            if target_id != caller_id:
                respond(response_type="ephemeral", text=f":shrug: <@{target_id}> hasn't joined the league yet.")
            else:
                respond(response_type="ephemeral", text=":wave: You're not enrolled yet — use `/register` to join!")
            return

    blocks, title = _build_me_blocks(target_id, caller_id, client, upcoming_page=0)
    respond(response_type="ephemeral", blocks=blocks, text=title)


def handle_mystats_upcoming_page(ack, respond, body, client):
    ack()
    logger.info("mystats_upcoming_page called, value=%r, response_url=%r",
                body["actions"][0]["value"], body.get("response_url", "MISSING"))
    caller_id = body["user"]["id"]
    with db.db() as conn:
        if not db.is_enrolled(conn, caller_id):
            respond(replace_original=True, text=":wave: You're not enrolled in the league anymore.")
            return
    value = body["actions"][0]["value"]
    target_id, page_str = value.rsplit(":", 1)
    page = int(page_str)
    blocks, title = _build_me_blocks(target_id, caller_id, client, upcoming_page=page)
    try:
        result = respond(replace_original=True, blocks=blocks, text=title)
        logger.info("respond status=%s body=%r", result.status_code, result.body)
    except Exception as exc:
        logger.exception("respond failed: %s", exc)


def _picks_text(picks, locked: bool) -> str:
    lines = []

    w = picks["winner"]
    w_pts = picks["winner_points"]
    w_line = f":first_place_medal: Winner: *{flag(w)} {w}*"
    if w_pts is not None:
        w_line += f"  → *{w_pts} pts*" if w_pts > 0 else "  → :x: 0 pts"
    else:
        w_line += f"  _(+{TOURNAMENT_PICK_POINTS} if correct)_"
    lines.append(w_line)

    gs = picks["top_scorer"]
    s_pts = picks["scorer_points"]
    s_line = f":athletic_shoe: Golden Boot: *{gs}*"
    if s_pts is not None:
        s_line += f"  → *{s_pts} pts*" if s_pts > 0 else "  → :x: 0 pts"
    else:
        s_line += f"  _(+{TOURNAMENT_PICK_POINTS} if correct)_"
    lines.append(s_line)

    semis = [picks[f"semi{i}"] for i in range(1, 5) if picks[f"semi{i}"]]
    if semis:
        semi_pts = picks["semi_points"]
        semi_str = "  ·  ".join(f"{flag(t)} {t}" for t in semis)
        semi_line = f":four: Semis: {semi_str}"
        if semi_pts is not None:
            semi_line += f"  → *{semi_pts} pts*"
        else:
            semi_line += f"  _(+{SEMI_PICK_POINTS} pts each if correct)_"
        lines.append(semi_line)
    else:
        lines.append(":four: Semis: _(not picked)_")

    guess = picks["group_goals_guess"]
    if guess is not None:
        gg_pts = picks["group_goals_points"]
        gg_line = f":goal_net: Group goals guess: *{guess}*"
        if gg_pts is not None:
            gg_line += f"  → *{gg_pts} pts*" if gg_pts > 0 else "  → :x: 0 pts"
        else:
            gg_line += "  _(pending — scored after group stage)_"
        lines.append(gg_line)

    zebra = picks["zebra"]
    if zebra:
        tier_label = ":black_joker: Wildcard" if picks["zebra_tier"] == "WILDCARD" else "⭐ Bold"
        z_pts = picks["zebra_points"]
        z_line = f":zebra_face: Zebra: *{flag(zebra)} {zebra}* ({tier_label})"
        if z_pts is not None:
            z_line += f"  → *{z_pts} pts*" if z_pts > 0 else "  → :x: 0 pts"
        else:
            z_line += "  _(pending)_"
        lines.append(z_line)

    return "\n".join(lines)


def _picks_locked(conn) -> bool:
    kickoff = db.get_first_matchday2_kickoff(conn)
    from app.football import is_kickoff_passed
    return kickoff is not None and is_kickoff_passed(kickoff)


def _lookup_user_by_name(client, username: str) -> str | None:
    try:
        resp = client.users_list()
        for member in resp.get("members", []):
            if member.get("deleted") or member.get("is_bot"):
                continue
            if (member.get("name", "").lower() == username or
                    member.get("profile", {}).get("display_name", "").lower() == username):
                return member["id"]
    except Exception as exc:
        logger.error("Failed to look up user %r: %s", username, exc)
    return None
