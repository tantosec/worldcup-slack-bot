import logging
import os
from datetime import timezone
from zoneinfo import ZoneInfo
from apscheduler.schedulers.background import BackgroundScheduler
from app import db
from app.flags import flag, home, away, vs
from app.espn import (
    fetch_live_matches, fetch_all_matches, fetch_match_summary,
    get_goal_scorers, get_match_stats, get_second_half_kickoff, get_display_clock, fetch_top_scorer,
    get_penalty_scores,
)
from app.football import format_kickoff, format_score, format_score_note, stage_label, estimate_match_time
from app.odds import fetch_and_store_odds, sync_odds_if_stale, format_prob_line, format_underdog_line
from app.scoring import calculate_points, points_label, score_semi_picks, score_group_goals

logger = logging.getLogger(__name__)


# ─── Block Kit helpers ────────────────────────────────────────────────────────

def _block_section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _block_context(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


def _block_divider() -> dict:
    return {"type": "divider"}


def _block_fields(pairs: list) -> list:
    """Build section blocks with 2-column fields. Chunked at 5 pairs (10 fields) per block."""
    blocks = []
    for i in range(0, len(pairs), 5):
        chunk = pairs[i:i + 5]
        fields = []
        for left, right in chunk:
            fields.append({"type": "mrkdwn", "text": left})
            fields.append({"type": "mrkdwn", "text": right})
        blocks.append({"type": "section", "fields": fields})
    return blocks


def _post_attachment(slack_client, channel: str, fallback: str, color: str, blocks: list):
    slack_client.chat_postMessage(
        channel=channel,
        text=fallback,
        attachments=[{"color": color, "blocks": blocks}],
    )


# ─── Fixture sync ─────────────────────────────────────────────────────────────

def sync_fixtures():
    """Pull live + upcoming WC matches from ESPN and upsert into DB."""
    logger.info("Syncing fixtures…")
    matches = fetch_live_matches()
    if not matches:
        return
    with db.db() as conn:
        for m in matches:
            db.upsert_match_espn(conn, m)
    logger.info("ESPN sync: updated %d matches", len(matches))


def sync_all_fixtures():
    """Sync today-onwards fixtures from ESPN — picks up newly confirmed knockout teams."""
    logger.info("Full fixture import from ESPN…")
    matches = fetch_all_matches()
    if not matches:
        return
    with db.db() as conn:
        for m in matches:
            db.upsert_match_espn(conn, m)
    logger.info("ESPN full import: %d matches", len(matches))


# ─── Match scoring ────────────────────────────────────────────────────────────

def score_finished_matches(slack_client=None):
    """Find finished unscored matches, calculate points, DM each predictor."""
    with db.db() as conn:
        unscored = db.get_finished_unscored_matches(conn)

    scored_any = False
    for match in unscored:
        logger.info("Scoring match %s: %s %s–%s %s",
                    match["external_id"], match["home_team"],
                    match["home_score"], match["away_score"], match["away_team"])

        with db.db() as conn:
            predictions = db.get_predictions_for_match(conn, match["id"])

            results = []
            for pred in predictions:
                pts = calculate_points(
                    pred["home_score"], pred["away_score"],
                    match["home_score"], match["away_score"],
                    match["home_team"], match["away_team"],
                    match["stage"],
                    match=match,
                )
                db.update_prediction_points(conn, pred["id"], pts)
                results.append((pred["slack_user_id"], pred["home_score"], pred["away_score"], pts))

        with db.db() as conn:
            leaderboard = db.get_leaderboard(conn)

        if slack_client:
            try:
                _post_result_summary(slack_client, match, results, leaderboard)
                logger.info("Posted result summary for match %s", match["id"])
            except Exception as exc:
                logger.error("Failed to post result summary for match %s, will retry: %s", match["id"], exc)
                continue
            for user_id, pred_home, pred_away, pts in results:
                _dm_points_earned(slack_client, user_id, match, pred_home, pred_away, pts)

        with db.db() as conn:
            db.mark_match_scored(conn, match["id"])
        scored_any = True

    if scored_any:
        # A match just finished — ESPN may now have real team names for the next
        # round's fixtures (replacing TBD placeholders). Sync immediately.
        sync_all_fixtures()


def _post_result_summary(slack_client, match, results: list, leaderboard=None) -> dict:
    channel = os.getenv("RESULTS_CHANNEL")
    if not channel:
        return dict(match)

    # Convert to mutable dict so penalty scores can be injected before building score_text
    match = dict(match)

    # Fetch summary up-front: needed for penalty scores (must appear in score_text)
    # and for goal scorers / stats. A single fetch covers all three.
    goals = []
    stats = None
    try:
        summary = fetch_match_summary(match["external_id"])
        goals = get_goal_scorers(summary)
        stats = get_match_stats(summary)
        if match["duration"] == "PENALTY_SHOOTOUT":
            pen_home, pen_away = get_penalty_scores(summary)
            if pen_home is not None and pen_away is not None:
                match["penalties_home"] = pen_home
                match["penalties_away"] = pen_away
                with db.db() as conn:
                    db.update_match_penalties(conn, match["id"], pen_home, pen_away)
    except Exception as exc:
        logger.warning("Could not fetch ESPN stats for full-time: %s", exc)

    results_sorted = sorted(results, key=lambda r: r[3], reverse=True)
    duration = match["duration"] or "REGULAR"

    ft_label = "🏁  *FULL TIME*"
    if duration == "PENALTY_SHOOTOUT":
        ft_label = "🏁  *FULL TIME*  _(Penalties)_"
    elif duration == "EXTRA_TIME":
        ft_label = "🏁  *FULL TIME*  _(AET)_"

    score_text = (
        f"*{home(match['home_team'])} {format_score(match)} {away(match['away_team'])}*\n"
        f"{stage_label(match['stage'])}"
    )

    blocks = [
        _block_section(ft_label),
        _block_divider(),
        _block_section(score_text),
    ]

    prob_line = format_prob_line(match)
    ud_line = format_underdog_line(match)
    if prob_line:
        blocks.append(_block_context(prob_line))
    if ud_line:
        blocks.append(_block_context(ud_line))

    # Goal scorers and stats (already fetched above)
    if goals:
        home_goals = [g for g in goals if g["team_name"] == match["home_team"]]
        away_goals = [g for g in goals if g["team_name"] == match["away_team"]]
        goal_lines = []
        if home_goals:
            goal_lines.append(flag(match["home_team"]) + "  " + "  ·  ".join(
                f":soccer: *{g['scorer_name']}* {g['minute']}'{g['suffix']}" for g in home_goals
            ))
        if away_goals:
            goal_lines.append(flag(match["away_team"]) + "  " + "  ·  ".join(
                f":soccer: *{g['scorer_name']}* {g['minute']}'{g['suffix']}" for g in away_goals
            ))
        if goal_lines:
            blocks.append(_block_context("\n".join(goal_lines)))
    if stats and stats.get("home_possession"):
        blocks.append(_block_context(
            f":bar_chart:  {match['home_team']} {stats['home_possession']}% poss "
            f"·  {stats['home_shots_on_target']} shots on target  ·  "
            f"{match['away_team']} {stats['away_possession']}% poss "
            f"·  {stats['away_shots_on_target']} shots on target"
        ))

    blocks.append(_block_divider())
    blocks.append(_block_section("🔮  *Predictions*"))

    if results_sorted:
        pred_pairs = []
        for user_id, pred_home, pred_away, pts in results_sorted:
            pred_str = f"{pred_home} - {pred_away}"
            if pts > 0 and pred_home == match["home_score"] and pred_away == match["away_score"]:
                icon = ":dart:"
                label = "Exact!"
            elif pts > 0:
                icon = ":white_check_mark:"
                label = "Correct"
            else:
                icon = ":x:"
                label = "Wrong"
            pred_pairs.append((
                f"{icon}  <@{user_id}>  `{pred_str}`  {label}",
                f"*{points_label(pts)}*",
            ))
        blocks.extend(_block_fields(pred_pairs))
    else:
        blocks.append(_block_section("_(no predictions were made for this match)_"))

    if leaderboard:
        blocks.append(_block_divider())
        blocks.append(_block_section("🏆  *Leaderboard*"))
        medals = {1: ":first_place_medal:", 2: ":second_place_medal:", 3: ":third_place_medal:"}
        lb_pairs = []
        for i, row in enumerate(leaderboard[:10], start=1):
            medal = medals.get(i, f"`{i}.`")
            exact = row["exact_scores"] or 0
            bonus_parts = []
            if row["winner_points"]:
                bonus_parts.append(f":first_place_medal: _(+{row['winner_points']})_")
            if row["scorer_points"]:
                bonus_parts.append(f":athletic_shoe: _(+{row['scorer_points']})_")
            if row["zebra_points"]:
                bonus_parts.append(f":zebra_face: _(+{row['zebra_points']})_")
            if row["semi_points"]:
                bonus_parts.append(f":four: _(+{row['semi_points']})_")
            if row["group_goals_points"]:
                bonus_parts.append(f":goal_net: _(+{row['group_goals_points']})_")
            right = f"*{row['total_points']} pts*  ·  :dart: {exact}"
            if bonus_parts:
                right += "\n" + "  ·  ".join(bonus_parts)
            lb_pairs.append((f"{medal}  <@{row['slack_user_id']}>", right))
        blocks.extend(_block_fields(lb_pairs))

    _post_attachment(
        slack_client, channel,
        f"Full Time: {match['home_team']} {format_score(match)} {match['away_team']}",
        "#2e7d32", blocks,
    )


def _dm_points_earned(slack_client, user_id: str, match, pred_home: int, pred_away: int, pts: int):
    actual = format_score(match) + format_score_note(match)
    predicted = f"{pred_home} - {pred_away}"
    duration = match["duration"] or "REGULAR"

    if pts == 0:
        result_icon = ":x:"
        result_text = f"Picked `{predicted}` — no points this time"
    elif pred_home == match["home_score"] and pred_away == match["away_score"]:
        result_icon = ":dart:"
        result_text = f"Picked `{predicted}` — exact score!"
    else:
        result_icon = ":white_check_mark:"
        result_text = f"Picked `{predicted}` — correct result"

    extra_notes = []
    if match["stage"] != "GROUP_STAGE":
        mult = _stage_multiplier_label(match["stage"])
        extra_notes.append(f"×{mult} {stage_label(match['stage'])} multiplier")
    if duration == "PENALTY_SHOOTOUT":
        extra_notes.append("went to penalties")
    elif duration == "EXTRA_TIME":
        extra_notes.append("AET")

    with db.db() as conn:
        rank, total = db.get_user_rank_and_total(conn, user_id)
    rank_txt = f"#{rank}" if rank else "—"

    blocks = [
        _block_section(
            f"⚽  *{home(match['home_team'])} {actual} {away(match['away_team'])}*"
        ),
        _block_divider(),
        _block_section(f"{result_icon}  {result_text}"),
    ]
    if extra_notes:
        blocks.append(_block_context("_" + "  ·  ".join(extra_notes) + "_"))
    blocks.append(_block_context(
        f"*{points_label(pts)}* earned  ·  *{total} pts* total  ·  Rank *{rank_txt}*"
    ))

    try:
        slack_client.chat_postMessage(
            channel=user_id,
            text=f"Full Time: {match['home_team']} {actual} {match['away_team']} — {points_label(pts)}",
            blocks=blocks,
        )
    except Exception as exc:
        logger.error("Failed to DM %s: %s", user_id, exc)


# ─── Goal notifications ───────────────────────────────────────────────────────

def send_goal_notifications(slack_client):
    """Post a goal alert whenever the live score changes."""
    channel = os.getenv("RESULTS_CHANNEL")
    if not channel:
        return

    with db.db() as conn:
        matches = db.get_matches_with_score_change(conn)

    for match in matches:
        curr_home = match["home_score"]
        curr_away = match["away_score"]

        # First sync at 0-0: just mark notified, don't post
        if curr_home == 0 and curr_away == 0:
            with db.db() as conn:
                db.mark_score_notified(conn, match["id"], curr_home, curr_away)
            continue

        # Penalty shootout: suppress per-kick notifications, full-time result covers it
        if match["duration"] == "PENALTY_SHOOTOUT":
            with db.db() as conn:
                db.mark_score_notified(conn, match["id"], curr_home, curr_away)
            continue

        prev_home = match["notified_home_score"] if match["notified_home_score"] is not None else 0
        prev_away = match["notified_away_score"] if match["notified_away_score"] is not None else 0

        new_home = curr_home - prev_home
        new_away = curr_away - prev_away
        new_total = new_home + new_away

        scoring_teams = []
        if new_home > 0:
            scoring_teams.append(f"{flag(match['home_team'])} {match['home_team']}")
        if new_away > 0:
            scoring_teams.append(f"{flag(match['away_team'])} {match['away_team']}")

        # Fetch scorer names from ESPN
        scorer_lines = []
        display_clock = None
        try:
            summary = fetch_match_summary(match["external_id"])
            all_goals = get_goal_scorers(summary)
            display_clock = get_display_clock(summary)
            if new_home > 0:
                home_goals = [g for g in all_goals if g["team_name"] == match["home_team"]]
                for g in home_goals[-new_home:]:
                    scorer_lines.append(f"{flag(match['home_team'])}  :soccer: *{g['scorer_name']}* {g['minute']}'{g['suffix']}")
            if new_away > 0:
                away_goals = [g for g in all_goals if g["team_name"] == match["away_team"]]
                for g in away_goals[-new_away:]:
                    scorer_lines.append(f"{flag(match['away_team'])}  :soccer: *{g['scorer_name']}* {g['minute']}'{g['suffix']}")
        except Exception as exc:
            logger.warning("Could not fetch ESPN goal details: %s", exc)

        if new_total == 1:
            header_text = f":soccer:  *GOOOOOOOAAAALLLLL!*\n{scoring_teams[0]} scores!"
        elif len(scoring_teams) == 1:
            header_text = f":soccer:  *GOOOOOOOAAAALLLLL!*\n{scoring_teams[0]} scores {new_total}!"
        else:
            header_text = f":soccer:  *GOALS!*\n{'  ·  '.join(scoring_teams)} both score!"

        match_time = estimate_match_time(match["kickoff_utc"], match["status"], display_clock)
        score_text = (
            f"*{home(match['home_team'])} {curr_home} - {curr_away} {away(match['away_team'])}*"
            f"  ·  _{match_time}_"
        )

        blocks = [
            _block_section(header_text),
            _block_divider(),
            _block_section(score_text),
        ]

        if scorer_lines:
            blocks.append(_block_context("\n".join(scorer_lines)))

        prob_line = format_prob_line(match)
        ud_line = format_underdog_line(match)
        if prob_line:
            blocks.append(_block_context(prob_line))
        if ud_line:
            blocks.append(_block_context(ud_line))

        with db.db() as conn:
            preds = db.get_match_predictions_all_users(conn, match["id"])

        scorers = []
        for p in preds:
            if p["home_score"] is None:
                continue
            pts = calculate_points(
                p["home_score"], p["away_score"],
                curr_home, curr_away,
                match["home_team"], match["away_team"],
                match["stage"],
                match=match,
            )
            if pts > 0:
                exact = p["home_score"] == curr_home and p["away_score"] == curr_away
                icon = ":dart:" if exact else ":white_check_mark:"
                scorers.append((icon, p["slack_user_id"], p["home_score"], p["away_score"], pts))

        if scorers:
            blocks.append(_block_divider())
            blocks.append(_block_section("🔮  *Scoring right now*"))
            scorer_pairs = [
                (
                    f"{icon}  <@{uid}>  `{ph} - {pa}`",
                    f"*+{points_label(pts)}*",
                )
                for icon, uid, ph, pa, pts in sorted(scorers, key=lambda x: -x[4])
            ]
            blocks.extend(_block_fields(scorer_pairs))

        try:
            _post_attachment(
                slack_client, channel,
                f"Goal! {match['home_team']} {curr_home} - {curr_away} {match['away_team']}",
                "#f4c430", blocks,
            )
            with db.db() as conn:
                db.mark_score_notified(conn, match["id"], curr_home, curr_away)
        except Exception as exc:
            logger.error("Failed to post goal notification for match %s: %s", match["id"], exc)


# ─── Halftime notifications ───────────────────────────────────────────────────

def send_halftime_notifications(slack_client):
    """Post a halftime summary when a match reaches half time."""
    channel = os.getenv("RESULTS_CHANNEL")
    if not channel:
        return

    with db.db() as conn:
        matches = db.get_matches_needing_halftime_notification(conn)

    for match in matches:
        curr_home = match["home_score"] or 0
        curr_away = match["away_score"] or 0

        score_text = (
            f"*{home(match['home_team'])} {curr_home} - {curr_away} {away(match['away_team'])}*"
            f"  ·  _{stage_label(match['stage'])}_"
        )

        blocks = [
            _block_section("⏸  *HALF TIME*"),
            _block_divider(),
            _block_section(score_text),
        ]

        # Goal scorers from ESPN
        try:
            summary = fetch_match_summary(match["external_id"])
            goals = get_goal_scorers(summary)
            stats = get_match_stats(summary)
            if goals:
                home_goals = [g for g in goals if g["team_name"] == match["home_team"]]
                away_goals = [g for g in goals if g["team_name"] == match["away_team"]]
                goal_lines = []
                if home_goals:
                    goal_lines.append(flag(match["home_team"]) + "  " + "  ·  ".join(
                        f":soccer: *{g['scorer_name']}* {g['minute']}'{g['suffix']}" for g in home_goals
                    ))
                if away_goals:
                    goal_lines.append(flag(match["away_team"]) + "  " + "  ·  ".join(
                        f":soccer: *{g['scorer_name']}* {g['minute']}'{g['suffix']}" for g in away_goals
                    ))
                if goal_lines:
                    blocks.append(_block_context("\n".join(goal_lines)))
            elif curr_home == 0 and curr_away == 0:
                blocks.append(_block_context("_No goals in the first half_"))
            if stats and stats.get("home_possession"):
                blocks.append(_block_context(
                    f":bar_chart:  {match['home_team']} {stats['home_possession']}% poss "
                    f"·  {stats['home_shots_on_target']} shots on target  ·  "
                    f"{match['away_team']} {stats['away_possession']}% poss "
                    f"·  {stats['away_shots_on_target']} shots on target"
                ))
            second_half_dt = get_second_half_kickoff(summary)
            if second_half_dt:
                _disp_tz = ZoneInfo(os.getenv("DISPLAY_TIMEZONE", "Australia/Sydney"))
                second_half_local = second_half_dt.astimezone(_disp_tz)
                blocks.append(_block_context(
                    f":clock2:  _Second half expected around {second_half_local.strftime('%-I:%M %p %Z')}_"
                ))
        except Exception as exc:
            logger.warning("Could not fetch ESPN halftime stats: %s", exc)

        # Current prediction standings
        with db.db() as conn:
            preds = db.get_match_predictions_all_users(conn, match["id"])

        scorers = []
        for p in preds:
            if p["home_score"] is None:
                continue
            pts = calculate_points(
                p["home_score"], p["away_score"],
                curr_home, curr_away,
                match["home_team"], match["away_team"],
                match["stage"],
                match=match,
            )
            if pts > 0:
                exact = p["home_score"] == curr_home and p["away_score"] == curr_away
                icon = ":dart:" if exact else ":white_check_mark:"
                scorers.append((icon, p["slack_user_id"], p["home_score"], p["away_score"], pts))

        if scorers:
            blocks.append(_block_divider())
            blocks.append(_block_section("🔮  *Scoring at half time*"))
            scorer_pairs = [
                (
                    f"{icon}  <@{uid}>  `{ph} - {pa}`",
                    f"*+{points_label(pts)}*",
                )
                for icon, uid, ph, pa, pts in sorted(scorers, key=lambda x: -x[4])
            ]
            blocks.extend(_block_fields(scorer_pairs))

        try:
            _post_attachment(
                slack_client, channel,
                f"Half Time: {match['home_team']} {curr_home} - {curr_away} {match['away_team']}",
                "#ff8f00", blocks,
            )
            with db.db() as conn:
                db.mark_halftime_notified(conn, match["id"])
        except Exception as exc:
            logger.error("Failed to post halftime notification for match %s: %s", match["id"], exc)


# ─── Second half notifications ────────────────────────────────────────────────

def send_second_half_notifications(slack_client):
    """Post a 'second half underway' message when a match resumes after halftime."""
    channel = os.getenv("RESULTS_CHANNEL")
    if not channel:
        return

    with db.db() as conn:
        matches = db.get_matches_needing_second_half_notification(conn)

    for match in matches:
        curr_home = match["home_score"] or 0
        curr_away = match["away_score"] or 0

        score_text = (
            f"*{home(match['home_team'])} {curr_home} - {curr_away} {away(match['away_team'])}*"
            f"  ·  _{stage_label(match['stage'])}_"
        )

        blocks = [
            _block_section(":large_green_circle:  *SECOND HALF UNDERWAY*"),
            _block_divider(),
            _block_section(score_text),
        ]

        try:
            summary = fetch_match_summary(match["external_id"])
            goals = get_goal_scorers(summary)
            if goals:
                home_goals = [g for g in goals if g["team_name"] == match["home_team"]]
                away_goals = [g for g in goals if g["team_name"] == match["away_team"]]
                goal_lines = []
                if home_goals:
                    goal_lines.append(flag(match["home_team"]) + "  " + "  ·  ".join(
                        f":soccer: *{g['scorer_name']}* {g['minute']}'{g['suffix']}" for g in home_goals
                    ))
                if away_goals:
                    goal_lines.append(flag(match["away_team"]) + "  " + "  ·  ".join(
                        f":soccer: *{g['scorer_name']}* {g['minute']}'{g['suffix']}" for g in away_goals
                    ))
                if goal_lines:
                    blocks.append(_block_context("\n".join(goal_lines)))
        except Exception as exc:
            logger.warning("Could not fetch ESPN second half details: %s", exc)

        try:
            _post_attachment(
                slack_client, channel,
                f"Second Half: {match['home_team']} {curr_home} - {curr_away} {match['away_team']}",
                "#43a047", blocks,
            )
            with db.db() as conn:
                db.mark_second_half_notified(conn, match["id"])
        except Exception as exc:
            logger.error("Failed to post second half notification for match %s: %s", match["id"], exc)


# ─── Extra time notifications ─────────────────────────────────────────────────

def send_extra_time_notifications(slack_client):
    """Post an extra time alert when a knockout match goes beyond 90 minutes."""
    channel = os.getenv("RESULTS_CHANNEL")
    if not channel:
        return

    with db.db() as conn:
        matches = db.get_matches_needing_extra_time_notification(conn)

    for match in matches:
        curr_home = match["home_score"] or 0
        curr_away = match["away_score"] or 0

        blocks = [
            _block_section(":stopwatch:  *EXTRA TIME*"),
            _block_divider(),
            _block_section(
                f"*{home(match['home_team'])} {curr_home} - {curr_away} {away(match['away_team'])}*"
                f"  ·  _{stage_label(match['stage'])}_"
            ),
        ]

        try:
            summary = fetch_match_summary(match["external_id"])
            goals = get_goal_scorers(summary)
            if goals:
                home_goals = [g for g in goals if g["team_name"] == match["home_team"]]
                away_goals = [g for g in goals if g["team_name"] == match["away_team"]]
                goal_lines = []
                if home_goals:
                    goal_lines.append(flag(match["home_team"]) + "  " + "  ·  ".join(
                        f":soccer: *{g['scorer_name']}* {g['minute']}'{g['suffix']}" for g in home_goals
                    ))
                if away_goals:
                    goal_lines.append(flag(match["away_team"]) + "  " + "  ·  ".join(
                        f":soccer: *{g['scorer_name']}* {g['minute']}'{g['suffix']}" for g in away_goals
                    ))
                if goal_lines:
                    blocks.append(_block_context("\n".join(goal_lines)))
            elif curr_home == 0 and curr_away == 0:
                blocks.append(_block_context("_Still goalless after 90 minutes_"))
        except Exception as exc:
            logger.warning("Could not fetch ESPN extra time details: %s", exc)

        try:
            _post_attachment(
                slack_client, channel,
                f"Extra Time: {match['home_team']} {curr_home} - {curr_away} {match['away_team']}",
                "#7b1fa2", blocks,
            )
            with db.db() as conn:
                db.mark_extra_time_notified(conn, match["id"])
        except Exception as exc:
            logger.error("Failed to post extra time notification for match %s: %s", match["id"], exc)


# ─── Penalty shootout notifications ──────────────────────────────────────────

def send_shootout_notifications(slack_client):
    """Post a penalty shootout alert when a match goes to spot kicks."""
    channel = os.getenv("RESULTS_CHANNEL")
    if not channel:
        return

    with db.db() as conn:
        matches = db.get_matches_needing_shootout_notification(conn)

    for match in matches:
        curr_home = match["home_score"] or 0
        curr_away = match["away_score"] or 0

        blocks = [
            _block_section(":goal_net:  *PENALTY SHOOTOUT*"),
            _block_divider(),
            _block_section(
                f"*{home(match['home_team'])} {curr_home} - {curr_away} {away(match['away_team'])}*"
                f"  ·  _{stage_label(match['stage'])}_ _(AET)_"
            ),
        ]

        try:
            summary = fetch_match_summary(match["external_id"])
            goals = get_goal_scorers(summary)
            if goals:
                home_goals = [g for g in goals if g["team_name"] == match["home_team"]]
                away_goals = [g for g in goals if g["team_name"] == match["away_team"]]
                goal_lines = []
                if home_goals:
                    goal_lines.append(flag(match["home_team"]) + "  " + "  ·  ".join(
                        f":soccer: *{g['scorer_name']}* {g['minute']}'{g['suffix']}" for g in home_goals
                    ))
                if away_goals:
                    goal_lines.append(flag(match["away_team"]) + "  " + "  ·  ".join(
                        f":soccer: *{g['scorer_name']}* {g['minute']}'{g['suffix']}" for g in away_goals
                    ))
                if goal_lines:
                    blocks.append(_block_context("\n".join(goal_lines)))
        except Exception as exc:
            logger.warning("Could not fetch ESPN shootout details: %s", exc)

        try:
            _post_attachment(
                slack_client, channel,
                f"Penalty Shootout: {match['home_team']} {curr_home} - {curr_away} {match['away_team']}",
                "#c62828", blocks,
            )
            with db.db() as conn:
                db.mark_shootout_notified(conn, match["id"])
        except Exception as exc:
            logger.error("Failed to post shootout notification for match %s: %s", match["id"], exc)


# ─── Kickoff announcements ────────────────────────────────────────────────────

def send_kickoff_announcements(slack_client):
    """Post all predictions to channel when a match kicks off."""
    channel = os.getenv("RESULTS_CHANNEL")
    if not channel:
        return

    with db.db() as conn:
        matches = db.get_matches_needing_kickoff_announcement(conn)

    for match in matches:
        with db.db() as conn:
            all_preds = db.get_match_predictions_all_users(conn, match["id"])
            enrolled = db.get_enrolled_users(conn)

        venue_parts = [stage_label(match["stage"]), format_kickoff(match["kickoff_utc"])]
        if match["venue_name"]:
            venue_parts.append(f"{match['venue_name']}, {match['venue_city'] or ''}")

        blocks = [
            _block_section("⚽  *KICKOFF!*"),
            _block_divider(),
            _block_section(
                f"*{vs(match['home_team'], match['away_team'])}*\n"
                + "  ·  ".join(venue_parts)
            ),
        ]

        prob_line = format_prob_line(match)
        ud_line = format_underdog_line(match, action=True)
        if prob_line:
            blocks.append(_block_context(prob_line))
        if ud_line:
            blocks.append(_block_context(ud_line))

        blocks.append(_block_divider())
        blocks.append(_block_section("🔮  *Predictions*"))

        predicted_ids = {p["slack_user_id"] for p in all_preds if p["home_score"] is not None}
        pred_pairs = [
            (f"<@{p['slack_user_id']}>", f"`{p['home_score']} - {p['away_score']}`")
            for p in all_preds if p["home_score"] is not None
        ]

        if pred_pairs:
            blocks.extend(_block_fields(pred_pairs))
        else:
            blocks.append(_block_section("_(no predictions made)_"))

        no_pred = [u["slack_user_id"] for u in enrolled if u["slack_user_id"] not in predicted_ids]
        if no_pred:
            blocks.append(_block_context(
                ":x: No pick: " + "  ".join(f"<@{u}>" for u in no_pred)
            ))

        try:
            _post_attachment(
                slack_client, channel,
                f"Kickoff: {match['home_team']} vs {match['away_team']}",
                "#1565c0", blocks,
            )
            with db.db() as conn:
                db.mark_kickoff_announced(conn, match["id"])
        except Exception as exc:
            logger.error("Failed to post kickoff announcement: %s", exc)


# ─── Kickoff reminders ────────────────────────────────────────────────────────

def send_kickoff_reminders(slack_client):
    """Post a channel reminder ~1 hour before kickoff, tagging unpredicted users."""
    channel = os.getenv("RESULTS_CHANNEL")
    if not channel:
        return

    with db.db() as conn:
        sync_odds_if_stale(conn, max_age_minutes=60)
        matches = db.get_matches_needing_reminder(conn)

    for match in matches:
        with db.db() as conn:
            unpredicted = db.get_unpredicted_enrolled_users(conn, match["id"])

        blocks = [
            _block_section("⏰  *KICKOFF IN ~1 HOUR*"),
            _block_divider(),
            _block_section(
                f"*{vs(match['home_team'], match['away_team'])}*\n"
                f"{stage_label(match['stage'])}  ·  {format_kickoff(match['kickoff_utc'])}"
            ),
        ]

        prob_line = format_prob_line(match)
        ud_line = format_underdog_line(match, action=True)
        if prob_line:
            blocks.append(_block_context(prob_line))
        if ud_line:
            blocks.append(_block_context(ud_line))

        blocks.append(_block_divider())

        if unpredicted:
            mentions = "  ".join(f"<@{u}>" for u in unpredicted)
            blocks.append(_block_section(
                f"🔔  {mentions}\nHaven't predicted yet — use `/predict` before kickoff! 🔒"
            ))
        else:
            blocks.append(_block_section(":white_check_mark:  All predictions are in!"))

        try:
            _post_attachment(
                slack_client, channel,
                f"Kickoff reminder: {match['home_team']} vs {match['away_team']} in ~1 hour",
                "#f9a825", blocks,
            )
            with db.db() as conn:
                db.mark_reminder_sent(conn, match["id"])
        except Exception as exc:
            logger.error("Failed to post kickoff reminder: %s", exc)


# ─── Matchday wrap ────────────────────────────────────────────────────────────

def send_matchday_wrap(slack_client):
    """Post an end-of-day summary once all matches on a given date are scored."""
    channel = os.getenv("RESULTS_CHANNEL")
    if not channel:
        return

    with db.db() as conn:
        dates = db.get_dates_needing_wrap(conn)

    for match_date in dates:
        with db.db() as conn:
            matches = db.get_matches_on_date(conn, match_date)
            top_earners = db.get_day_top_earners(conn, match_date)

        total_goals = sum(
            (m["home_score"] or 0) + (m["away_score"] or 0)
            + (m["et_home"] or 0) + (m["et_away"] or 0)
            for m in matches
        )

        results_text = "\n".join(
            f"{home(m['home_team'])} *{m['home_score']} - {m['away_score']}* {away(m['away_team'])}"
            for m in matches
        )

        blocks = [
            _block_section(f"📅  *MATCHDAY WRAP  ·  {match_date}*"),
            _block_divider(),
            _block_section(results_text),
            _block_context(
                f":goal_net: *{total_goals} goals* across "
                f"*{len(matches)}* match{'es' if len(matches) != 1 else ''}"
            ),
        ]

        if top_earners:
            blocks.append(_block_divider())
            blocks.append(_block_section("⭐  *Top earners today*"))
            medals = [":first_place_medal:", ":second_place_medal:", ":third_place_medal:"]
            earn_pairs = [
                (
                    f"{medals[i] if i < len(medals) else f'`{i+1}.`'}  <@{row['slack_user_id']}>",
                    f"*+{row['day_pts']} pts*",
                )
                for i, row in enumerate(top_earners)
            ]
            blocks.extend(_block_fields(earn_pairs))

        try:
            _post_attachment(
                slack_client, channel,
                f"Matchday Wrap — {match_date}",
                "#6a1b9a", blocks,
            )
            with db.db() as conn:
                db.mark_wrap_sent(conn, match_date)
        except Exception as exc:
            logger.error("Failed to post matchday wrap for %s: %s", match_date, exc)


# ─── Picks reveal ─────────────────────────────────────────────────────────────

def post_picks_reveal(slack_client, force: bool = False):
    """Post everyone's tournament picks to the channel once picks are locked."""
    channel = os.getenv("RESULTS_CHANNEL")
    if not channel:
        return

    with db.db() as conn:
        if not force and db.picks_reveal_already_sent(conn):
            return
        from app.football import is_kickoff_passed
        kickoff = db.get_first_matchday2_kickoff(conn)
        if not force and (kickoff is None or not is_kickoff_passed(kickoff)):
            return
        picks = db.get_all_picks_for_reveal(conn)
        if not picks:
            return
        db.mark_picks_reveal_sent(conn)

    blocks = [
        _block_section("🔒  *Tournament picks are locked!*\nHere's what everyone chose:"),
        _block_divider(),
    ]

    for p in picks:
        pick_lines = [f"*<@{p['slack_user_id']}>*"]

        if p["winner"]:
            pick_lines.append(f":first_place_medal: Winner: *{flag(p['winner'])} {p['winner']}*")
        if p["top_scorer"]:
            pick_lines.append(f":athletic_shoe: Golden Boot: *{p['top_scorer']}*")

        semis = [p[f"semi{i}"] for i in range(1, 5) if p[f"semi{i}"]]
        if semis:
            pick_lines.append(":four: Semis: " + "  ·  ".join(f"{flag(s)} {s}" for s in semis))

        if p["group_goals_guess"] is not None:
            pick_lines.append(f":goal_net: Group goals: *{p['group_goals_guess']}*")

        if p["zebra"]:
            tier = ":black_joker: Wildcard" if p["zebra_tier"] == "WILDCARD" else "⭐ Bold"
            pick_lines.append(f":zebra_face: Zebra: *{flag(p['zebra'])} {p['zebra']}* ({tier})")

        blocks.append(_block_section("\n".join(pick_lines)))

    try:
        _post_attachment(
            slack_client, channel,
            "Tournament picks are locked!",
            "#1e88e5", blocks,
        )
        logger.info("Picks reveal posted")
    except Exception as exc:
        logger.error("Failed to post picks reveal: %s", exc)


# ─── Phase wrap ───────────────────────────────────────────────────────────────

_STAGE_DISPLAY = {
    "LAST_32":        "Round of 32",
    "LAST_16":        "Round of 16",
    "QUARTER_FINALS": "Quarter-finals",
    "SEMI_FINALS":    "Semi-finals",
    "FINAL":          "The Final",
    "WINNER":         "Champions",
}

_PHASE_HEADERS = {
    "GROUP_STAGE":    ":soccer:  *Group Stage Complete!*",
    "LAST_32":        ":checkered_flag:  *Round of 32 Complete!*",
    "LAST_16":        ":checkered_flag:  *Round of 16 Complete!*",
    "QUARTER_FINALS": ":fire:  *Quarter-finals Complete!*",
    "SEMI_FINALS":    ":star2:  *Semi-finals Complete!*",
    "THIRD_PLACE":    ":third_place_medal:  *3rd Place Match Result*",
    "FINAL":          ":trophy:  *World Cup 2026 — It's All Over!*",
}

_PHASE_COLORS = {
    "GROUP_STAGE":    "#7b1fa2",
    "LAST_32":        "#1565c0",
    "LAST_16":        "#00838f",
    "QUARTER_FINALS": "#e65100",
    "SEMI_FINALS":    "#f9a825",
    "THIRD_PLACE":    "#78909c",
    "FINAL":          "#c8a400",
}

_NEXT_PHASE_LABEL = {
    "GROUP_STAGE":    "Round of 32",
    "LAST_32":        "Round of 16",
    "LAST_16":        "Quarter-final",
    "QUARTER_FINALS": "Semi-final",
    "SEMI_FINALS":    "Final",
}


def _build_group_goals_blocks(picks, actual_goals: int) -> list:
    blocks = [
        _block_divider(),
        _block_section(f":goal_net:  *Group Stage Goals*  ·  Actual total: *{actual_goals} goals*"),
    ]
    pairs = []
    for p in picks:
        if p["group_goals_guess"] is None:
            continue
        guess = p["group_goals_guess"]
        pts = p["group_goals_points"] or 0
        diff = abs(guess - actual_goals)
        if diff == 0:
            icon = ":dart:"
            note = "Exact!"
        elif pts > 0:
            icon = ":white_check_mark:"
            note = f"off by {diff}"
        else:
            icon = ":x:"
            note = f"off by {diff}"
        pairs.append((
            f"{icon}  <@{p['slack_user_id']}>  guessed *{guess}*",
            f"*{pts} pts*  ·  _{note}_",
        ))
    if pairs:
        blocks.extend(_block_fields(pairs))
    else:
        blocks.append(_block_section("_(no group goals guesses made)_"))
    return blocks


def _build_zebra_blocks(wrap_stage: str, picks) -> list:
    blocks = [
        _block_divider(),
        _block_section(":zebra_face:  *Zebra Picks*"),
    ]
    with db.db() as conn:
        last32_count = db.get_last32_fixture_count(conn) if wrap_stage == "GROUP_STAGE" else 16
        is_group = wrap_stage == "GROUP_STAGE"
        pairs = []
        for p in picks:
            zebra = p["zebra"]
            if not zebra:
                continue
            tier_label = "Wildcard" if p["zebra_tier"] == "WILDCARD" else "Bold"
            pts = p["zebra_points"] or 0
            left = f"<@{p['slack_user_id']}>  {flag(zebra)} *{zebra}*  _{tier_label}_"
            if is_group:
                if db.team_has_last32_fixture(conn, zebra):
                    right = "✅ Advancing to R32"
                elif last32_count < 16:
                    right = "⏳ Status TBD"
                else:
                    right = "❌ Eliminated"
            else:
                team_matches = db.get_team_knockout_stages(conn, zebra)
                stage_key = _team_furthest_stage_for(zebra, team_matches) if team_matches else None
                if stage_key == "WINNER":
                    right = f"*{pts} pts*  :trophy: Champions!"
                elif stage_key:
                    right = f"*{pts} pts*  ·  _{_STAGE_DISPLAY.get(stage_key, stage_key)}_"
                else:
                    right = f"*{pts} pts*  ·  _Eliminated_"
            pairs.append((left, right))
    if pairs:
        blocks.extend(_block_fields(pairs))
    else:
        blocks.append(_block_section("_(no zebra picks made)_"))
    return blocks


def _build_semi_picks_blocks(picks, actual_semis: list) -> list:
    blocks = [
        _block_divider(),
        _block_section(":four:  *Semi-finalist Picks*"),
    ]
    if len(actual_semis) < 4:
        blocks.append(_block_context("_Semi-finalist scoring pending — all 4 teams not yet confirmed_"))
        return blocks
    actual_set = set(actual_semis)
    pairs = []
    for p in picks:
        user_picks = [p[f"semi{i}"] for i in range(1, 5) if p[f"semi{i}"]]
        if not user_picks:
            continue
        pts = p["semi_points"] or 0
        correct_count = sum(1 for t in user_picks if t in actual_set)
        pick_parts = []
        for t in user_picks:
            icon = "✅" if t in actual_set else "❌"
            pick_parts.append(f"{icon} {flag(t)} {t}")
        pairs.append((
            "<@{}>  ".format(p["slack_user_id"]) + "  ·  ".join(pick_parts),
            f"*{pts} pts*  ·  _{correct_count}/4 correct_",
        ))
    if pairs:
        blocks.extend(_block_fields(pairs))
    else:
        blocks.append(_block_section("_(no semi-finalist picks made)_"))
    return blocks


def _build_winner_blocks(picks, actual_winner: str | None) -> list:
    winner_line = f"  ·  Champion: *{flag(actual_winner)} {actual_winner}*" if actual_winner else ""
    blocks = [
        _block_divider(),
        _block_section(f":first_place_medal:  *Winner Picks*{winner_line}"),
    ]
    pairs = []
    for p in picks:
        if not p["winner"]:
            continue
        correct = actual_winner and p["winner"] == actual_winner
        pts = p["winner_points"] or 0
        icon = ":trophy:" if correct else ":x:"
        pairs.append((
            f"{icon}  <@{p['slack_user_id']}>  {flag(p['winner'])} *{p['winner']}*",
            f"*{pts} pts*",
        ))
    if pairs:
        blocks.extend(_block_fields(pairs))
    else:
        blocks.append(_block_section("_(no winner picks made)_"))
    return blocks


def _build_golden_boot_blocks(picks, scorer_name: str | None) -> list:
    scorer_line = f"  ·  Winner: *{scorer_name}*" if scorer_name else ""
    blocks = [
        _block_divider(),
        _block_section(f":athletic_shoe:  *Golden Boot Picks*{scorer_line}"),
    ]
    pairs = []
    for p in picks:
        if not p["top_scorer"]:
            continue
        correct = scorer_name and p["top_scorer"].strip().lower() == scorer_name.strip().lower()
        pts = p["scorer_points"] or 0
        icon = ":trophy:" if correct else ":x:"
        pairs.append((
            f"{icon}  <@{p['slack_user_id']}>  *{p['top_scorer']}*",
            f"*{pts} pts*",
        ))
    if pairs:
        blocks.extend(_block_fields(pairs))
    else:
        blocks.append(_block_section("_(no golden boot picks made)_"))
    return blocks


def send_phase_wrap(slack_client):
    """Post a rich phase-complete summary with full leaderboard once a round is done."""
    channel = os.getenv("RESULTS_CHANNEL")
    if not channel:
        return

    with db.db() as conn:
        stages = db.get_stages_needing_phase_wrap(conn)

    for stage in stages:
        # ─── Inline scoring to eliminate race condition with scheduler jobs ───
        score_zebra_picks_job()
        if stage == "GROUP_STAGE":
            score_group_goals_job()
        if stage == "QUARTER_FINALS":
            score_semi_picks_job()
        if stage == "FINAL":
            score_winner_picks_job()
            score_golden_boot_job()

        with db.db() as conn:
            matches = db.get_matches_by_stage(conn, stage)
            stats = db.get_stage_stats(conn, stage)
            leaderboard = db.get_leaderboard_with_breakdown(conn)
            picks = db.get_all_tournament_picks(conn)
            upcoming_stages = db.get_upcoming_stages(conn)

        header_text = _PHASE_HEADERS.get(stage, f":checkered_flag:  *{stage_label(stage)} Complete!*")
        color = _PHASE_COLORS.get(stage, "#555555")

        blocks = [
            _block_section(header_text),
            _block_divider(),
        ]

        # ─── Match results ───
        if stage == "GROUP_STAGE":
            total = stats["match_count"] or 0
            goals = stats["total_goals"] or 0
            draws = stats["draws"] or 0
            avg = round(goals / total, 1) if total else 0
            blocks.append(_block_section(
                f"*{total} matches*  ·  *{goals} goals*  ·  *{avg} per game*  ·  *{draws} draws*"
            ))
        elif stage == "THIRD_PLACE":
            m = matches[0] if matches else None
            if m:
                blocks.append(_block_section(
                    f"{home(m['home_team'])} *{format_score(m)}* {away(m['away_team'])}"
                    f"{format_score_note(m)}{_advance_note(m, stage)}"
                ))
        else:
            result_lines = []
            for m in matches:
                result_lines.append(
                    f"{home(m['home_team'])} *{format_score(m)}* {away(m['away_team'])}"
                    f"{format_score_note(m)}{_advance_note(m, stage)}"
                )
            if result_lines:
                blocks.append(_block_section("\n".join(result_lines)))

        # ─── Group goals picks section (GROUP_STAGE only) ───
        if stage == "GROUP_STAGE":
            with db.db() as conn:
                actual_goals = db.sum_group_goals(conn)
            blocks.extend(_build_group_goals_blocks(picks, actual_goals))

        # ─── Zebra picks (all stages) ───
        blocks.extend(_build_zebra_blocks(stage, picks))

        # ─── Semi-finalist picks (QUARTER_FINALS only) ───
        if stage == "QUARTER_FINALS":
            with db.db() as conn:
                actual_semis = db.get_confirmed_semi_teams(conn)
            blocks.extend(_build_semi_picks_blocks(picks, actual_semis))

        # ─── Winner and golden boot picks (FINAL only) ───
        if stage == "FINAL":
            with db.db() as conn:
                actual_winner = db.get_tournament_winner(conn)
            scorer_name = fetch_top_scorer()
            blocks.extend(_build_winner_blocks(picks, actual_winner))
            blocks.extend(_build_golden_boot_blocks(picks, scorer_name))

        # ─── Leaderboard ───
        blocks.append(_block_divider())
        medals = {1: ":first_place_medal:", 2: ":second_place_medal:", 3: ":third_place_medal:"}

        if stage == "FINAL":
            # Full-width per-user sections to accommodate the point breakdown
            blocks.append(_block_section(":trophy:  *Final Standings — World Cup 2026 Prediction League*"))
            for i, row in enumerate(leaderboard, start=1):
                medal = medals.get(i, f"`{i}.`")
                exact = row["exact_scores"] or 0
                parts = []
                if row["winner_points"]:
                    parts.append(f":first_place_medal: Winner: _(+{row['winner_points']})_")
                if row["scorer_points"]:
                    parts.append(f":athletic_shoe: Golden Boot: _(+{row['scorer_points']})_")
                if row["zebra_points"]:
                    parts.append(f":zebra_face: Zebra: _(+{row['zebra_points']})_")
                if row["semi_points"]:
                    parts.append(f":four: Semis: _(+{row['semi_points']})_")
                if row["group_goals_points"]:
                    parts.append(f":goal_net: Group goals: _(+{row['group_goals_points']})_")
                breakdown = "  ·  ".join(parts) if parts else "no bonus points"
                blocks.append(_block_section(
                    f"{medal}  <@{row['slack_user_id']}>  *{row['total_points']} pts*  ·  :dart: {exact} exact\n"
                    f"_{breakdown}_"
                ))
        elif stage == "THIRD_PLACE":
            # Minimal — top-3 leaderboard only, no pick breakdowns
            blocks.append(_block_section(":bar_chart:  *Leaderboard (Top 3)*"))
            lb_pairs = []
            for i, row in enumerate(leaderboard[:3], start=1):
                medal = medals.get(i, f"`{i}.`")
                exact = row["exact_scores"] or 0
                lb_pairs.append((
                    f"{medal}  <@{row['slack_user_id']}>",
                    f"*{row['total_points']} pts*  ·  :dart: {exact}",
                ))
            blocks.extend(_block_fields(lb_pairs))
        else:
            blocks.append(_block_section(":bar_chart:  *Leaderboard*"))
            lb_pairs = []
            for i, row in enumerate(leaderboard, start=1):
                medal = medals.get(i, f"`{i}.`")
                exact = row["exact_scores"] or 0
                bonus_parts = []
                if row["winner_points"]:
                    bonus_parts.append(f":first_place_medal: _(+{row['winner_points']})_")
                if row["scorer_points"]:
                    bonus_parts.append(f":athletic_shoe: _(+{row['scorer_points']})_")
                if row["zebra_points"]:
                    bonus_parts.append(f":zebra_face: _(+{row['zebra_points']})_")
                if row["semi_points"]:
                    bonus_parts.append(f":four: _(+{row['semi_points']})_")
                if row["group_goals_points"]:
                    bonus_parts.append(f":goal_net: _(+{row['group_goals_points']})_")
                right = f"*{row['total_points']} pts*  ·  :dart: {exact}"
                if bonus_parts:
                    right += "\n" + "  ·  ".join(bonus_parts)
                lb_pairs.append((f"{medal}  <@{row['slack_user_id']}>", right))
            blocks.extend(_block_fields(lb_pairs))

        # ─── FINAL champion message ───
        if stage == "FINAL":
            if leaderboard:
                winner_row = leaderboard[0]
                blocks.append(_block_section(
                    f":confetti_ball: Congratulations <@{winner_row['slack_user_id']}> — "
                    f"*{winner_row['total_points']} pts*! :trophy:"
                ))
            blocks.append(_block_section("Thanks for playing — see you at the next one! :soccer:"))

        # ─── Next stage CTA (not for THIRD_PLACE or FINAL) ───
        elif stage != "THIRD_PLACE":
            next_label = _NEXT_PHASE_LABEL.get(stage)
            has_next_fixtures = any(s != stage for s in upcoming_stages) if upcoming_stages else False
            if next_label and has_next_fixtures:
                blocks.append(_block_divider())
                blocks.append(_block_section(
                    f":bell:  *{next_label} predictions are now live!*\n"
                    f"Use `/predict` to lock in your picks before the first match kicks off."
                ))
            elif next_label:
                blocks.append(_block_divider())
                blocks.append(_block_section(
                    f":hourglass:  *{next_label} fixtures coming soon* — check back shortly!"
                ))

        try:
            _post_attachment(
                slack_client, channel,
                f"{stage_label(stage)} Complete!",
                color, blocks,
            )
            with db.db() as conn:
                db.mark_phase_wrap_sent(conn, stage)
            logger.info("Phase wrap posted for %s", stage)
        except Exception as exc:
            logger.error("Failed to post phase wrap for %s: %s", stage, exc)


# ─── Tournament scoring jobs ──────────────────────────────────────────────────

_STAGE_DEPTH = {
    "LAST_32": 1, "LAST_16": 2, "QUARTER_FINALS": 3,
    "SEMI_FINALS": 4, "THIRD_PLACE": 4, "FINAL": 5,
}


def _team_furthest_stage(matches) -> str | None:
    from app.scoring import ZEBRA_POINTS
    best = None
    won_final = False
    for m in matches:
        depth = _STAGE_DEPTH.get(m["stage"], 0)
        if best is None or depth > _STAGE_DEPTH.get(best, 0):
            best = m["stage"]
        if m["stage"] == "FINAL":
            won_final = m["winner"] is not None
    if best == "FINAL" and won_final:
        return "WINNER"
    return best if best in ZEBRA_POINTS or best == "FINAL" else None


def _team_furthest_stage_for(team_name: str, matches) -> str | None:
    from app.scoring import ZEBRA_POINTS
    best_depth = 0
    best_stage = None
    won_final = False
    for m in matches:
        depth = _STAGE_DEPTH.get(m["stage"], 0)
        if depth > best_depth:
            best_depth = depth
            best_stage = m["stage"]
        if m["stage"] == "FINAL":
            if m["winner"] == "HOME_TEAM" and m["home_team"] == team_name:
                won_final = True
            elif m["winner"] == "AWAY_TEAM" and m["away_team"] == team_name:
                won_final = True
    if best_stage is None:
        return None
    if best_stage == "FINAL" and won_final:
        return "WINNER"
    if best_stage == "THIRD_PLACE":
        return "SEMI_FINALS"
    return best_stage if best_stage in ZEBRA_POINTS else None


def score_winner_picks_job():
    from app.scoring import TOURNAMENT_PICK_POINTS
    with db.db() as conn:
        if db.winner_picks_already_scored(conn):
            return
        actual_winner = db.get_tournament_winner(conn)
        if not actual_winner:
            return
        picks = db.get_all_tournament_picks(conn)
        for pick in picks:
            pts = TOURNAMENT_PICK_POINTS if pick["winner"] == actual_winner else 0
            db.update_winner_points(conn, pick["slack_user_id"], pts)
    logger.info("Winner picks scored — actual winner: %s", actual_winner)


def score_zebra_picks_job():
    from app.scoring import zebra_points as calc_zebra_pts
    with db.db() as conn:
        picks = db.get_all_tournament_picks(conn)
        for pick in picks:
            zebra = pick["zebra"]
            if not zebra:
                continue
            matches = db.get_team_knockout_stages(conn, zebra)
            if not matches:
                continue
            is_wildcard = pick["zebra_tier"] == "WILDCARD"
            stage_key = _team_furthest_stage_for(zebra, matches)
            if not stage_key:
                continue
            pts = calc_zebra_pts(stage_key, is_wildcard)
            db.update_zebra_points(conn, pick["slack_user_id"], pts)
    logger.info("Zebra picks scored")


def score_semi_picks_job():
    with db.db() as conn:
        if db.semi_picks_already_scored(conn):
            return
        actual_semis = db.get_confirmed_semi_teams(conn)
        if len(actual_semis) < 4:
            return
        actual_set = set(actual_semis)
        picks = db.get_all_tournament_picks(conn)
        for pick in picks:
            user_picks = {pick[f"semi{i}"] for i in range(1, 5) if pick[f"semi{i}"]}
            if not user_picks:
                continue
            pts = score_semi_picks(user_picks, actual_set)
            db.update_semi_points(conn, pick["slack_user_id"], pts)
    logger.info("Semi-finalist picks scored")


def score_group_goals_job():
    GROUP_STAGE_MATCH_COUNT = 72
    with db.db() as conn:
        if db.group_goals_already_scored(conn):
            return
        finished = db.count_finished_group_matches(conn)
        if finished < GROUP_STAGE_MATCH_COUNT:
            return
        actual = db.sum_group_goals(conn)
        picks = db.get_all_tournament_picks(conn)
        guesses = [p["group_goals_guess"] for p in picks if p["group_goals_guess"] is not None]
        if not guesses:
            return
        for pick in picks:
            if pick["group_goals_guess"] is None:
                continue
            pts = score_group_goals(pick["group_goals_guess"], actual, guesses)
            db.update_group_goals_points(conn, pick["slack_user_id"], pts)
    logger.info("Group stage goals guesses scored (actual: %d)", actual)


def score_golden_boot_job():
    from app.scoring import TOURNAMENT_PICK_POINTS
    with db.db() as conn:
        if db.scorer_picks_already_scored(conn):
            return
        if not db.get_tournament_winner(conn):
            return
        player_name = fetch_top_scorer()
        if not player_name:
            return
        picks = db.get_all_tournament_picks(conn)
        awarded = []
        for pick in picks:
            if pick["top_scorer"] and pick["top_scorer"].strip().lower() == player_name.strip().lower():
                db.update_scorer_points(conn, pick["slack_user_id"], TOURNAMENT_PICK_POINTS)
                awarded.append(pick["slack_user_id"])
            else:
                db.update_scorer_points(conn, pick["slack_user_id"], 0)
    logger.info("Golden boot scored — winner: %s, awarded to: %s", player_name, awarded)


# ─── Utilities ────────────────────────────────────────────────────────────────

def _advance_note(match, stage: str = "") -> str:
    winner_field = match["winner"]

    if winner_field == "HOME_TEAM":
        team_name = match["home_team"]
    elif winner_field == "AWAY_TEAM":
        team_name = match["away_team"]
    else:
        return ""

    duration = match["duration"] or "REGULAR"
    if duration == "PENALTY_SHOOTOUT":
        how = " _(by pens)_"
    elif duration == "EXTRA_TIME":
        how = " _(aet)_"
    else:
        how = ""

    if stage == "FINAL":
        return ""
    if stage == "THIRD_PLACE":
        return f"  →  {flag(team_name)} *{team_name}* wins 3rd place :third_place_medal:{how}"
    return f"  →  {flag(team_name)} *{team_name}* advances{how}"


def _stage_multiplier_label(stage: str) -> str:
    from app.scoring import STAGE_MULTIPLIERS
    m = STAGE_MULTIPLIERS.get(stage, 1.0)
    return str(int(m)) if m == int(m) else str(m)


# ─── Scheduler setup ─────────────────────────────────────────────────────────

def live_updates(slack_client=None):
    """Fast poll: sync live ESPN scores then fire goal/halftime notifications."""
    sync_fixtures()
    send_goal_notifications(slack_client)
    send_halftime_notifications(slack_client)
    send_second_half_notifications(slack_client)
    send_extra_time_notifications(slack_client)
    send_shootout_notifications(slack_client)


def start_scheduler(slack_client=None) -> BackgroundScheduler:
    poll_interval = int(os.getenv("POLL_INTERVAL", "60"))
    live_poll = int(os.getenv("LIVE_POLL_INTERVAL", "10"))

    scheduler = BackgroundScheduler(timezone="UTC")

    # Full fixture import on startup, then every 1h to pick up knockout teams
    sync_all_fixtures()
    scheduler.add_job(sync_all_fixtures, "interval", hours=1, id="sync_all_fixtures")

    def sync_odds_job():
        with db.db() as conn:
            fetch_and_store_odds(conn)

    scheduler.add_job(sync_odds_job, "interval", hours=6, id="sync_odds")

    # Fast poll: live score sync + goal + halftime notifications
    scheduler.add_job(
        lambda: live_updates(slack_client),
        "interval", seconds=live_poll, id="live_updates",
    )

    scheduler.add_job(
        lambda: score_finished_matches(slack_client),
        "interval", seconds=poll_interval, id="score_matches",
    )
    scheduler.add_job(
        lambda: post_picks_reveal(slack_client),
        "interval", seconds=poll_interval, id="picks_reveal",
    )
    scheduler.add_job(
        lambda: send_kickoff_announcements(slack_client),
        "interval", seconds=poll_interval, id="kickoff_announcements",
    )
    scheduler.add_job(
        lambda: send_kickoff_reminders(slack_client),
        "interval", seconds=poll_interval, id="kickoff_reminders",
    )
    scheduler.add_job(
        lambda: send_matchday_wrap(slack_client),
        "interval", seconds=poll_interval, id="matchday_wrap",
    )
    scheduler.add_job(
        lambda: send_phase_wrap(slack_client),
        "interval", seconds=poll_interval, id="phase_wrap",
    )
    scheduler.add_job(
        score_winner_picks_job,
        "interval", seconds=poll_interval, id="score_winner_picks",
    )
    scheduler.add_job(
        score_zebra_picks_job,
        "interval", seconds=poll_interval, id="score_zebra_picks",
    )
    scheduler.add_job(
        score_semi_picks_job,
        "interval", seconds=poll_interval, id="score_semi_picks",
    )
    scheduler.add_job(
        score_group_goals_job,
        "interval", seconds=poll_interval, id="score_group_goals",
    )
    scheduler.add_job(
        score_golden_boot_job,
        "interval", seconds=poll_interval, id="score_golden_boot",
    )

    scheduler.start()
    logger.info("Scheduler started (live poll %ds, other jobs %ds)", live_poll, poll_interval)
    return scheduler
