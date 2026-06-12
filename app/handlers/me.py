import re
import logging

from app import db
from app.flags import flag, home, away, vs
from app.football import format_score, format_score_note, format_kickoff, stage_label
from app.scoring import TOURNAMENT_PICK_POINTS, SEMI_PICK_POINTS

logger = logging.getLogger(__name__)


def handle_me(respond, body, client):
    caller_id = body["user_id"]
    text = (body.get("text") or "").strip()
    logger.info("/mystats called by %s with text: %r", caller_id, text)

    # Parse optional @mention: <@U123456|username> or plain @username
    target_id = caller_id
    viewing_other = False

    mention = re.search(r"<@([A-Z0-9]+)(?:\|[^>]+)?>", text)
    if mention:
        target_id = mention.group(1)
        viewing_other = target_id != caller_id
    elif text.startswith("@"):
        username = text[1:].lower()
        target_id = _lookup_user_by_name(client, username) or caller_id
        viewing_other = target_id != caller_id

    with db.db() as conn:
        if not db.is_enrolled(conn, target_id):
            if viewing_other:
                respond(response_type="ephemeral", text=f":shrug: <@{target_id}> hasn't joined the league yet.")
            else:
                respond(response_type="ephemeral", text=":wave: You're not enrolled yet — use `/register` to join!")
            return

        stats = db.get_user_match_stats(conn, target_id)
        picks = db.get_tournament_pick(conn, target_id)
        rank, total_points = db.get_user_rank_and_total(conn, target_id)
        total_players = len(db.get_enrolled_users(conn))
        picks_locked = _picks_locked(conn)
        picks_revealed = db.picks_reveal_already_sent(conn)
        finished_preds = db.get_user_finished_predictions(conn, target_id)
        upcoming_preds = [] if viewing_other else db.get_user_upcoming_predictions(conn, target_id)

    header = f"<@{target_id}>" if viewing_other else "Your"
    lines = [f":bar_chart: *{header} World Cup 2026 Stats*\n"]

    # Rank + points split
    match_pts = stats["match_points"] or 0
    tournament_pts = (total_points or 0) - match_pts
    lines.append(
        f":trophy: Rank *#{rank}* of {total_players}  ·  *{total_points} pts total*\n"
        f"  Match pts: *{match_pts}*  ·  Tournament pts: *{tournament_pts}*"
    )

    # Tournament picks
    lines.append("\n*━━━━━━━━━━━━━━━━━━━━*")
    lines.append(":crystal_ball: *Tournament Picks*")
    lines.append("*━━━━━━━━━━━━━━━━━━━━*\n")

    if picks and (not viewing_other or picks_revealed):
        _append_picks_lines(lines, picks, picks_locked)
    elif viewing_other and not picks_revealed:
        lines.append("  _Picks are revealed after Matchday 2 locks._")
    elif not picks:
        lines.append("  _(not submitted yet)_")

    # Match predictions
    lines.append("\n*━━━━━━━━━━━━━━━━━━━━*")
    count_str = f"{len(finished_preds)} played"
    if upcoming_preds:
        count_str += f"  ·  {len(upcoming_preds)} upcoming"
    lines.append(f":soccer: *Match Predictions* ({count_str})")
    lines.append("*━━━━━━━━━━━━━━━━━━━━*\n")

    if finished_preds:
        for p in finished_preds:
            actual = format_score(p) + format_score_note(p)
            match_line = f"*{home(p['home_team'])} {actual} {away(p['away_team'])}*"
            pts = p["points"] or 0
            pred_str = f"{p['pred_home']} - {p['pred_away']}"
            if p["pred_home"] == p["home_score"] and p["pred_away"] == p["away_score"]:
                icon = ":dart:"
                result = "Exact score"
            elif pts > 0:
                icon = ":white_check_mark:"
                result = "Correct result"
            else:
                icon = ":x:"
                result = "Wrong"
            lines.append(f"{match_line}\n  {icon} Picked {pred_str}  ·  {result}  ·  *+{pts} pts*")
    else:
        lines.append("  _No finished matches predicted yet._")

    if upcoming_preds:
        lines.append("")
        for p in upcoming_preds:
            lines.append(
                f"*{vs(p['home_team'], p['away_team'])}*  ·  {format_kickoff(p['kickoff_utc'])}\n"
                f"  :pencil: Your pick: *{p['pred_home']} - {p['pred_away']}*"
            )

    respond(response_type="ephemeral", text="\n\n".join(lines))


def _append_picks_lines(lines: list, picks, locked: bool):
    w = picks["winner"]
    w_pts = picks["winner_points"]
    w_line = f"  :first_place_medal: Winner: *{flag(w)} {w}*"
    if w_pts is not None:
        w_line += f"  → *{w_pts} pts*" if w_pts > 0 else "  → :x: 0 pts"
    else:
        w_line += f"  _(+{TOURNAMENT_PICK_POINTS} if correct)_"
    lines.append(w_line)

    gs = picks["top_scorer"]
    s_pts = picks["scorer_points"]
    s_line = f"  :athletic_shoe: Golden Boot: *{gs}*"
    if s_pts is not None:
        s_line += f"  → *{s_pts} pts*" if s_pts > 0 else "  → :x: 0 pts"
    else:
        s_line += f"  _(+{TOURNAMENT_PICK_POINTS} if correct)_"
    lines.append(s_line)

    semis = [picks[f"semi{i}"] for i in range(1, 5) if picks[f"semi{i}"]]
    if semis:
        semi_pts = picks["semi_points"]
        semi_str = "  ·  ".join(f"{flag(t)} {t}" for t in semis)
        semi_line = f"  :four: Semis: {semi_str}"
        if semi_pts is not None:
            semi_line += f"  → *{semi_pts} pts*"
        else:
            semi_line += f"  _(+{SEMI_PICK_POINTS} pts each if correct)_"
        lines.append(semi_line)
    else:
        lines.append("  :four: Semis: _(not picked)_")

    guess = picks["group_goals_guess"]
    if guess is not None:
        gg_pts = picks["group_goals_points"]
        gg_line = f"  :goal_net: Group goals guess: *{guess}*"
        if gg_pts is not None:
            gg_line += f"  → *{gg_pts} pts*" if gg_pts > 0 else "  → :x: 0 pts"
        else:
            gg_line += "  _(pending — scored after group stage)_"
        lines.append(gg_line)

    zebra = picks["zebra"]
    if zebra:
        tier_label = ":black_joker: Wildcard" if picks["zebra_tier"] == "WILDCARD" else "⭐ Bold"
        z_pts = picks["zebra_points"]
        z_line = f"  :zebra_face: Zebra: *{flag(zebra)} {zebra}* ({tier_label})"
        if z_pts is not None:
            z_line += f"  → *{z_pts} pts*" if z_pts > 0 else "  → :x: 0 pts"
        else:
            z_line += "  _(pending)_"
        lines.append(z_line)


def _picks_locked(conn) -> bool:
    kickoff = db.get_first_matchday2_kickoff(conn)
    from app.football import is_kickoff_passed
    return kickoff is not None and is_kickoff_passed(kickoff)


def _lookup_user_by_name(client, username: str) -> str | None:
    """Look up a Slack user ID by their username or display name."""
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
