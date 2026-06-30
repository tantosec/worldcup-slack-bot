from app import db
from app.config import COMPETITION_NAME, TOURNAMENT_PICK_POINTS, SEMI_PICK_POINTS, GROUP_GOALS_WIN_POINTS, ZEBRA_POINTS, ZEBRA_WILDCARD_MULTIPLIER
from app.football import is_kickoff_passed

_MAX_WILDCARD_WINNER = ZEBRA_POINTS["WINNER"] * ZEBRA_WILDCARD_MULTIPLIER


def handle_enroll(respond, body):
    slack_user_id = body["user_id"]

    with db.db() as conn:
        enrolled = db.enroll_user(conn, slack_user_id)
        first_kickoff = db.get_first_match_kickoff(conn)
        lock_display = db.format_picks_lock_short(conn)

    tournament_started = first_kickoff is not None and is_kickoff_passed(first_kickoff)

    if not enrolled:
        respond(
            f":wave: You're already registered in the {COMPETITION_NAME} Prediction League!\n"
            "Use `/predict` to submit your picks."
        )
        return

    if tournament_started:
        respond(
            f":tada: *Welcome to the {COMPETITION_NAME} Prediction League!*\n"
            "\n"
            "The tournament is already underway — you can still predict all upcoming matches!\n"
            "\n"
            "*How to score points:*\n"
            "  :dart: Exact score → *9 pts* · ×1.5–3 in knockouts\n"
            "  :white_check_mark: Correct result → *3 pts*\n"
            "  :zap: Upset bonus → *+2 pts*\n"
            "\n"
            "  `/predict`     — predict match scores (pick a date, fill in scores)\n"
            "  `/fixtures`    — upcoming matches\n"
            "  `/results`     — recent results & your points\n"
            "  `/leaderboard` — full standings\n"
            "\n"
            "Good luck! :trophy:"
        )
    else:
        respond(
            f":tada: *Welcome to the {COMPETITION_NAME} Prediction League!*\n"
            "\n"
            "*How to score points:*\n"
            "  :dart: Exact score → *9 pts* · ×1.5–3 in knockouts\n"
            "  :white_check_mark: Correct result → *3 pts*\n"
            "  :zap: Upset bonus → *+2 pts*\n"
            f"  :trophy: Winner / :athletic_shoe: Golden Boot → *{TOURNAMENT_PICK_POINTS} pts each*\n"
            f"  :four: Semi-finalists → *{SEMI_PICK_POINTS} pts per correct team*\n"
            f"  :goal_net: Group stage goals guess → *up to {GROUP_GOALS_WIN_POINTS} pts*\n"
            f"  :zebra_face: Zebra Pick → underdog bonus (up to *{_MAX_WILDCARD_WINNER} pts* for a Wildcard winner!)\n"
            "\n"
            ":lock: Predictions are *locked once submitted* — choose wisely!\n"
            "\n"
            f"  `/picks`       — tournament picks: winner, golden boot & zebra *(locks {lock_display})*\n"
            "  `/predict`     — predict match scores (pick a date, fill in scores)\n"
            "  `/fixtures`    — upcoming matches\n"
            "  `/results`     — recent results & your points\n"
            "  `/leaderboard` — full standings\n"
            "\n"
            "Good luck! :trophy:"
        )
