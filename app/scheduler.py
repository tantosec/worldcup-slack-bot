import logging
import os
from apscheduler.schedulers.background import BackgroundScheduler
from app import db
from app.flags import flag, home, away, vs
from app.football import fetch_all_matches, fetch_top_scorer, format_kickoff, format_score, format_score_note, stage_label, estimate_match_time
from app.odds import fetch_and_store_odds, sync_odds_if_stale, format_prob_line, format_underdog_line
from app.scoring import calculate_points, points_label, score_semi_picks, score_group_goals

logger = logging.getLogger(__name__)


def sync_fixtures():
    """Pull all WC matches from football-data.org and upsert into DB."""
    logger.info("Syncing fixtures…")
    matches = fetch_all_matches()
    if not matches:
        return
    with db.db() as conn:
        skipped = 0
        for m in matches:
            if not m.get("home_team") or not m.get("away_team"):
                skipped += 1
                continue
            db.upsert_match(conn, m)
    logger.info("Synced %d matches (%d skipped — TBD teams)", len(matches) - skipped, skipped)


def score_finished_matches(slack_client=None):
    """Find finished unscored matches, calculate points, DM each predictor."""
    with db.db() as conn:
        unscored = db.get_finished_unscored_matches(conn)

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


def _post_result_summary(slack_client, match, results: list[tuple[str, int, int, int]], leaderboard=None):
    """Post match result to channel with each user's prediction and top 10 leaderboard."""
    channel = os.getenv("RESULTS_CHANNEL")
    if not channel:
        return

    results_sorted = sorted(results, key=lambda r: r[3], reverse=True)
    duration = match["duration"] or "REGULAR"
    ft_label = ":checkered_flag: *Full Time*"
    if duration == "PENALTY_SHOOTOUT":
        ft_label = ":checkered_flag: *Full Time* _(Penalties)_"
    elif duration == "EXTRA_TIME":
        ft_label = ":checkered_flag: *Full Time* _(AET)_"

    lines = [
        f"{ft_label}  ·  {stage_label(match['stage'])}",
        f"*{home(match['home_team'])} {format_score(match)} {away(match['away_team'])}{format_score_note(match)}*",
    ]
    prob_line = format_prob_line(match)
    if prob_line:
        lines.append(prob_line)
    ud_line = format_underdog_line(match)
    if ud_line:
        lines.append(ud_line)
    lines += ["", ":bar_chart: *Predictions:*"]

    for user_id, pred_home, pred_away, pts in results_sorted:
        pred_str = f"{pred_home} - {pred_away}"
        if pts > 0 and pred_home == match["home_score"] and pred_away == match["away_score"]:
            icon = ":dart:"
        elif pts > 0:
            icon = ":white_check_mark:"
        else:
            icon = ":x:"
        lines.append(f"  {icon} <@{user_id}>  `{pred_str}`  —  *{points_label(pts)}*")

    if not results:
        lines.append("  _(no predictions were made for this match)_")

    if leaderboard:
        lines.append("")
        lines.append(":trophy: *Leaderboard*")
        medals = {1: ":first_place_medal:", 2: ":second_place_medal:", 3: ":third_place_medal:"}
        for i, row in enumerate(leaderboard[:10], start=1):
            medal = medals.get(i, f"`{i}.`")
            lines.append(f"  {medal} <@{row['slack_user_id']}>  —  *{row['total_points']} pts*")

    slack_client.chat_postMessage(channel=channel, text="\n".join(lines))


def _dm_points_earned(slack_client, user_id: str, match, pred_home: int, pred_away: int, pts: int):
    """DM a user their points after a match is scored."""
    actual = format_score(match) + format_score_note(match)
    predicted = f"{pred_home} - {pred_away}"

    if pts == 0:
        result_line = f":x: *{predicted}* — no points this time"
    elif pred_home == match["home_score"] and pred_away == match["away_score"]:
        result_line = f":dart: *{predicted}* — exact score!"
    else:
        result_line = f":white_check_mark: *{predicted}* — correct result"

    with db.db() as conn:
        rank, total = db.get_user_rank_and_total(conn, user_id)

    rank_txt = f"#{rank}" if rank else "—"
    stage_txt = stage_label(match["stage"])
    multiplier_note = f" _(×{_stage_multiplier_label(match['stage'])} {stage_txt})_" if match["stage"] != "GROUP_STAGE" else ""
    duration = match["duration"] or "REGULAR"
    if duration == "PENALTY_SHOOTOUT":
        multiplier_note += " _(went to penalties)_"
    elif duration == "EXTRA_TIME":
        multiplier_note += " _(AET)_"

    try:
        slack_client.chat_postMessage(
            channel=user_id,
            text=(
                f":checkered_flag: *{home(match['home_team'])} {actual} {away(match['away_team'])}*\n"
                f"Your prediction: {result_line}{multiplier_note}\n"
                f"Points earned: *{points_label(pts)}*\n"
                f"Your total: *{total} pts*  ·  Rank *{rank_txt}*"
            ),
        )
    except Exception as exc:
        logger.error("Failed to DM %s: %s", user_id, exc)


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

        prev_home = match["notified_home_score"] if match["notified_home_score"] is not None else 0
        prev_away = match["notified_away_score"] if match["notified_away_score"] is not None else 0

        new_home = curr_home - prev_home
        new_away = curr_away - prev_away
        new_total = new_home + new_away

        # Build header
        scoring_teams = []
        if new_home > 0:
            scoring_teams.append(f"{flag(match['home_team'])} {match['home_team']}")
        if new_away > 0:
            scoring_teams.append(f"{flag(match['away_team'])} {match['away_team']}")

        if new_total == 1:
            header = f":rotating_light: *GOAAAAAL! {scoring_teams[0]} scores!*"
        elif len(scoring_teams) == 1:
            header = f":rotating_light: *GOALS! {scoring_teams[0]} scores {new_total}!*"
        else:
            header = f":rotating_light: *GOALS! {'  ·  '.join(scoring_teams)} both score!*"

        match_time = estimate_match_time(match["kickoff_utc"], match["status"])
        lines = [
            header,
            f"*{home(match['home_team'])} {curr_home} - {curr_away} {away(match['away_team'])}*  ·  _{match_time}_",
        ]
        prob_line = format_prob_line(match)
        if prob_line:
            lines.append(prob_line)
        ud_line = format_underdog_line(match)
        if ud_line:
            lines.append(ud_line)

        # Show who is currently scoring points at this live score
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
            )
            if pts > 0:
                exact = p["home_score"] == curr_home and p["away_score"] == curr_away
                icon = ":dart:" if exact else ":white_check_mark:"
                scorers.append((icon, p["slack_user_id"], p["home_score"], p["away_score"], pts))

        if scorers:
            lines.append("")
            lines.append(":crystal_ball: *Scoring points right now:*")
            for icon, user_id, ph, pa, pts in sorted(scorers, key=lambda x: -x[4]):
                lines.append(f"  {icon} <@{user_id}>  `{ph} - {pa}`  —  *+{points_label(pts)}*")

        try:
            slack_client.chat_postMessage(channel=channel, text="\n".join(lines))
            with db.db() as conn:
                db.mark_score_notified(conn, match["id"], curr_home, curr_away)
        except Exception as exc:
            logger.error("Failed to post goal notification for match %s: %s", match["id"], exc)


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

        lines = [
            f":soccer: *Kickoff!*  ·  {stage_label(match['stage'])}",
            f"*{vs(match['home_team'], match['away_team'])}*",
            f":calendar: {format_kickoff(match['kickoff_utc'])}",
        ]
        prob_line = format_prob_line(match)
        if prob_line:
            lines.append(prob_line)
        ud_line = format_underdog_line(match, action=True)
        if ud_line:
            lines.append(ud_line)
        lines += ["", ":bar_chart: *Predictions:*"]

        predicted_ids = {p["slack_user_id"] for p in all_preds if p["home_score"] is not None}

        for p in all_preds:
            if p["home_score"] is not None:
                lines.append(f"  <@{p['slack_user_id']}>  `{p['home_score']} - {p['away_score']}`")

        no_pred = [u["slack_user_id"] for u in enrolled if u["slack_user_id"] not in predicted_ids]
        if no_pred:
            lines.append("")
            lines.append(":x: No prediction: " + "  ".join(f"<@{u}>" for u in no_pred))

        try:
            slack_client.chat_postMessage(channel=channel, text="\n".join(lines))
            with db.db() as conn:
                db.mark_kickoff_announced(conn, match["id"])
        except Exception as exc:
            logger.error("Failed to post kickoff announcement: %s", exc)


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

        lines = [
            f":alarm_clock: *Kickoff in ~1 hour!*",
            f"*{vs(match['home_team'], match['away_team'])}*",
            f":calendar: {format_kickoff(match['kickoff_utc'])}  ·  {stage_label(match['stage'])}",
        ]
        prob_line = format_prob_line(match)
        if prob_line:
            lines.append(prob_line)
        ud_line = format_underdog_line(match, action=True)
        if ud_line:
            lines.append(ud_line)

        if unpredicted:
            mentions = "  ".join(f"<@{u}>" for u in unpredicted)
            lines.append(f"\n{mentions}")
            lines.append("Haven't predicted yet — use `/predict`!")
        else:
            lines.append(":white_check_mark: All predictions are in!")

        try:
            slack_client.chat_postMessage(channel=channel, text="\n".join(lines))
            with db.db() as conn:
                db.mark_reminder_sent(conn, match["id"])
        except Exception as exc:
            logger.error("Failed to post kickoff reminder: %s", exc)


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

        lines = [f":soccer: *Matchday Wrap — {match_date}*", ""]

        for m in matches:
            lines.append(
                f"  {home(m['home_team'])} *{m['home_score']} - {m['away_score']}* {away(m['away_team'])}"
            )

        lines.append(f"\n:goal_net: *{total_goals} goals* across {len(matches)} match{'es' if len(matches) != 1 else ''}")

        if top_earners:
            lines.append("\n:bar_chart: *Top earners today:*")
            medals = [":first_place_medal:", ":second_place_medal:", ":third_place_medal:"]
            for i, row in enumerate(top_earners):
                medal = medals[i] if i < len(medals) else "  "
                lines.append(f"  {medal} <@{row['slack_user_id']}> — *{row['day_pts']} pts*")

        try:
            slack_client.chat_postMessage(channel=channel, text="\n".join(lines))
            with db.db() as conn:
                db.mark_wrap_sent(conn, match_date)
        except Exception as exc:
            logger.error("Failed to post matchday wrap for %s: %s", match_date, exc)


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

    lines = [
        ":lock: *Tournament picks are locked! Here's what everyone chose:*",
        "",
    ]

    for p in picks:
        user_lines = [f"<@{p['slack_user_id']}>"]

        if p["winner"]:
            user_lines.append(f"  :first_place_medal: Winner: *{flag(p['winner'])} {p['winner']}*")
        if p["top_scorer"]:
            user_lines.append(f"  :athletic_shoe: Golden Boot: *{p['top_scorer']}*")

        semis = [p[f"semi{i}"] for i in range(1, 5) if p[f"semi{i}"]]
        if semis:
            semi_str = "  ·  ".join(f"{flag(s)} {s}" for s in semis)
            user_lines.append(f"  :four: Semis: {semi_str}")

        if p["group_goals_guess"] is not None:
            user_lines.append(f"  :goal_net: Group goals: *{p['group_goals_guess']}*")

        if p["zebra"]:
            tier = ":black_joker: Wildcard" if p["zebra_tier"] == "WILDCARD" else "⭐ Bold"
            user_lines.append(f"  :zebra_face: Zebra: *{flag(p['zebra'])} {p['zebra']}* ({tier})")

        lines.append("\n".join(user_lines))

    try:
        slack_client.chat_postMessage(channel=channel, text="\n\n".join(lines))
        logger.info("Picks reveal posted")
    except Exception as exc:
        logger.error("Failed to post picks reveal: %s", exc)


_PHASE_HEADERS = {
    "GROUP_STAGE":    ":soccer: *Group Stage Complete!*",
    "LAST_32":        ":checkered_flag: *Round of 32 Complete!*",
    "LAST_16":        ":checkered_flag: *Round of 16 Complete!*",
    "QUARTER_FINALS": ":fire: *Quarter-finals Complete!*",
    "SEMI_FINALS":    ":star2: *Semi-finals Complete!*",
    "THIRD_PLACE":    ":third_place_medal: *3rd Place Match Result*",
    "FINAL":          ":trophy: *World Cup 2026 — It's All Over!*",
}

_NEXT_PHASE_LABEL = {
    "GROUP_STAGE":    "Round of 32",
    "LAST_32":        "Round of 16",
    "LAST_16":        "Quarter-final",
    "QUARTER_FINALS": "Semi-final",
    "SEMI_FINALS":    "Final",
}


def send_phase_wrap(slack_client):
    """Post a rich phase-complete summary with full leaderboard once a round is done."""
    channel = os.getenv("RESULTS_CHANNEL")
    if not channel:
        return

    with db.db() as conn:
        stages = db.get_stages_needing_phase_wrap(conn)

    for stage in stages:
        with db.db() as conn:
            matches = db.get_matches_by_stage(conn, stage)
            stats = db.get_stage_stats(conn, stage)
            leaderboard = db.get_leaderboard(conn)
            upcoming_stages = db.get_upcoming_stages(conn)

        lines = [_PHASE_HEADERS.get(stage, f":checkered_flag: *{stage_label(stage)} Complete!*"), ""]

        # Results section — list individually for knockouts, summary only for group stage
        is_group = stage == "GROUP_STAGE"
        if is_group:
            total = stats["match_count"] or 0
            goals = stats["total_goals"] or 0
            draws = stats["draws"] or 0
            avg = round(goals / total, 1) if total else 0
            lines.append(f":bar_chart: *{total} matches  ·  {goals} goals  ·  {avg} per game  ·  {draws} draws*")
        else:
            lines.append("*Results:*")
            for m in matches:
                advance = _advance_note(m)
                lines.append(
                    f"  {home(m['home_team'])} *{format_score(m)}* {away(m['away_team'])}{format_score_note(m)}{advance}"
                )

        # Full leaderboard
        lines.append("")
        if stage == "FINAL":
            lines.append(":trophy: *Final Standings — World Cup 2026 Prediction League*")
        else:
            lines.append(":bar_chart: *Leaderboard*")

        medals = {1: ":first_place_medal:", 2: ":second_place_medal:", 3: ":third_place_medal:"}
        for i, row in enumerate(leaderboard, start=1):
            medal = medals.get(i, f"`{i}.`")
            exact = row["exact_scores"] or 0
            lines.append(
                f"  {medal} <@{row['slack_user_id']}>  —  *{row['total_points']} pts*"
                f"  ·  :dart: {exact} exact"
            )

        # Next phase callout
        if stage == "FINAL":
            if leaderboard:
                winner_row = leaderboard[0]
                lines.append(
                    f"\n:confetti_ball: Congratulations <@{winner_row['slack_user_id']}> — "
                    f"*{winner_row['total_points']} pts*! :trophy:"
                )
            lines.append("\nThanks for playing — see you at the next one! :soccer:")
        else:
            next_label = _NEXT_PHASE_LABEL.get(stage)
            has_next_fixtures = any(
                s != stage for s in upcoming_stages
            ) if upcoming_stages else False

            if next_label and has_next_fixtures:
                lines.append(f"\n:bell: *{next_label} predictions are now live!*")
                lines.append("Use `/predict` to lock in your picks before the first match kicks off.")
            elif next_label:
                lines.append(f"\n:hourglass: *{next_label} fixtures coming soon* — check back shortly!")

        try:
            slack_client.chat_postMessage(channel=channel, text="\n".join(lines))
            with db.db() as conn:
                db.mark_phase_wrap_sent(conn, stage)
            logger.info("Phase wrap posted for %s", stage)
        except Exception as exc:
            logger.error("Failed to post phase wrap for %s: %s", stage, exc)


_STAGE_DEPTH = {
    "LAST_32": 1, "LAST_16": 2, "QUARTER_FINALS": 3,
    "SEMI_FINALS": 4, "THIRD_PLACE": 4, "FINAL": 5,
}


def _team_furthest_stage(matches) -> str | None:
    """
    Given a team's finished knockout matches, return the stage key for scoring:
    WINNER if they won the final, otherwise the deepest stage they appeared in.
    """
    from app.scoring import ZEBRA_POINTS
    best = None
    won_final = False
    for m in matches:
        depth = _STAGE_DEPTH.get(m["stage"], 0)
        if best is None or depth > _STAGE_DEPTH.get(best, 0):
            best = m["stage"]
        if m["stage"] == "FINAL":
            team_won = (
                (m["winner"] == "HOME_TEAM" and m["home_team"] == m["home_team"]) or
                (m["winner"] == "AWAY_TEAM" and m["away_team"] == m["away_team"])
            )
            # determine which side this team was on
            won_final = m["winner"] is not None  # refined below per-pick
    # Return the best ZEBRA_POINTS key or None if team didn't make it past groups
    if best == "FINAL" and won_final:
        return "WINNER"  # caller checks actual winner separately
    return best if best in ZEBRA_POINTS or best == "FINAL" else None


def _team_furthest_stage_for(team_name: str, matches) -> str | None:
    """Return the ZEBRA_POINTS key for how far team_name progressed."""
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
        return "SEMI_FINALS"  # THIRD_PLACE not in ZEBRA_POINTS, treat as semi
    return best_stage if best_stage in ZEBRA_POINTS else None


def score_winner_picks_job():
    """Score tournament winner picks once the FINAL is complete."""
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
    """
    Score zebra picks progressively — runs every poll cycle and updates
    each user's zebra_points based on how far their team has gone so far.
    Skips users whose zebra team hasn't entered the knockout stage yet.
    """
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
    """Score semi-finalist picks once all 4 semi-final fixtures are confirmed."""
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
    """Score group stage total goals guesses once all 72 group matches are finished."""
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
    """Score golden boot picks automatically once the Final is finished."""
    from app.scoring import TOURNAMENT_PICK_POINTS
    with db.db() as conn:
        if db.scorer_picks_already_scored(conn):
            return
        if not db.get_tournament_winner(conn):
            # Final not finished yet
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


def _advance_note(match) -> str:
    """Return ' → Team advances (AET)' etc. for knockout matches."""
    winner_field = match["winner"]  # HOME_TEAM / AWAY_TEAM / null
    duration = match["duration"] or "REGULAR"

    if winner_field == "HOME_TEAM":
        team_name = match["home_team"]
    elif winner_field == "AWAY_TEAM":
        team_name = match["away_team"]
    else:
        return ""

    suffix = ""
    if duration == "PENALTY_SHOOTOUT":
        suffix = " _(pens)_"
    elif duration == "EXTRA_TIME":
        suffix = " _(AET)_"

    return f"  → {flag(team_name)} *{team_name}* advances{suffix}"


def _stage_multiplier_label(stage: str) -> str:
    from app.scoring import STAGE_MULTIPLIERS
    m = STAGE_MULTIPLIERS.get(stage, 1.0)
    return str(int(m)) if m == int(m) else str(m)


def start_scheduler(slack_client=None) -> BackgroundScheduler:
    poll_interval = int(os.getenv("POLL_INTERVAL", "60"))

    scheduler = BackgroundScheduler(timezone="UTC")

    scheduler.add_job(sync_fixtures, "interval", seconds=poll_interval, id="sync_fixtures")

    def sync_odds_job():
        with db.db() as conn:
            fetch_and_store_odds(conn)

    scheduler.add_job(sync_odds_job, "interval", hours=6, id="sync_odds")

    scheduler.add_job(
        lambda: score_finished_matches(slack_client),
        "interval", seconds=poll_interval, id="score_matches",
    )
    scheduler.add_job(
        lambda: post_picks_reveal(slack_client),
        "interval", seconds=poll_interval, id="picks_reveal",
    )
    scheduler.add_job(
        lambda: send_goal_notifications(slack_client),
        "interval", seconds=poll_interval, id="goal_notifications",
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
    logger.info("Scheduler started (poll every %ds)", poll_interval)
    return scheduler
