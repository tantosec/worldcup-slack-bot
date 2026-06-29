import logging
import os

from app import db
from app.flags import flag
from app.football import stage_label
from app.llm import get_provider
from app.llm.fallback import FallbackProvider
from app.scoring import ZEBRA_BOLD, ZEBRA_WILDCARD

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    return os.getenv("AUTO_PICK_ENABLED", "true").lower() == "true"


def _load_goal_threat_players() -> list[str]:
    import json as _json
    import os as _os
    path = _os.path.join(_os.path.dirname(__file__), "players.json")
    data = _json.load(open(path))
    return [p["name"] for p in data if p["position"] in ("Midfield", "Offence")]


def generate_auto_match_pick(match) -> tuple[int, int, str | None, str]:
    """Call LLM (or fallback) for a match. Returns (home, away, reasoning, provider)."""
    home_prob = match["home_odds"] or 0.33
    draw_prob = match["draw_odds"] or 0.33
    away_prob = match["away_odds"] or 0.34

    provider = get_provider()
    provider_name = type(provider).__name__.replace("Provider", "").lower()

    try:
        result = provider.predict_match(
            match["home_team"], match["away_team"],
            match["stage"], home_prob, draw_prob, away_prob,
        )
        logger.info(
            "Auto match pick generated via %s: %s %d-%d %s",
            provider_name, match["home_team"], result["home"], result["away"], match["away_team"],
        )
        return result["home"], result["away"], result.get("reasoning"), provider_name
    except Exception as exc:
        logger.warning("LLM provider %s failed: %s — using fallback", provider_name, exc)
        fallback = FallbackProvider()
        result = fallback.predict_match(
            match["home_team"], match["away_team"],
            match["stage"], home_prob, draw_prob, away_prob,
        )
        return result["home"], result["away"], None, "fallback"


def generate_auto_tournament_picks() -> tuple[dict, str]:
    """Call LLM (or fallback) for tournament picks. Returns (picks_dict, provider_name)."""
    players = _load_goal_threat_players()
    provider = get_provider()
    provider_name = type(provider).__name__.replace("Provider", "").lower()

    try:
        result = provider.predict_tournament_picks(ZEBRA_BOLD, ZEBRA_WILDCARD, players)
        logger.info("Auto tournament picks generated via %s: winner=%s", provider_name, result["winner"])
        return result, provider_name
    except Exception as exc:
        logger.warning("LLM provider %s failed: %s — using fallback", provider_name, exc)
        fallback = FallbackProvider()
        result = fallback.predict_tournament_picks(ZEBRA_BOLD, ZEBRA_WILDCARD, players)
        return result, "fallback"


def get_or_generate_auto_match_pick(match):
    """Return cached auto pick for this match, or generate and store one."""
    with db.db() as conn:
        cached = db.get_auto_match_pick(conn, match["id"])
        if cached:
            return cached

    home, away, reasoning, provider_name = generate_auto_match_pick(match)

    with db.db() as conn:
        db.save_auto_match_pick(conn, match["id"], home, away, reasoning, provider_name)
        return db.get_auto_match_pick(conn, match["id"])


def get_or_generate_auto_tournament_picks():
    """Return cached auto tournament picks, or generate and store them."""
    with db.db() as conn:
        cached = db.get_auto_tournament_picks(conn)
        if cached:
            return cached

    picks, provider_name = generate_auto_tournament_picks()

    with db.db() as conn:
        db.save_auto_tournament_picks(
            conn,
            winner=picks["winner"],
            top_scorer=picks["golden_boot"],
            semi1=picks["semi1"], semi2=picks["semi2"],
            semi3=picks["semi3"], semi4=picks["semi4"],
            zebra=picks["zebra"],
            zebra_tier=picks["zebra_tier"],
            group_goals_guess=picks["group_goals_guess"],
            reasoning=picks.get("reasoning"),
            provider=provider_name,
        )
        return db.get_auto_tournament_picks(conn)


def apply_auto_picks_for_match(slack_client, match) -> list[str]:
    """Apply cached auto pick to all users missing a prediction. Returns list of affected user IDs."""
    if not _enabled():
        return []

    with db.db() as conn:
        auto_pick = db.get_auto_match_pick(conn, match["id"])
        if not auto_pick:
            logger.warning("apply_auto_picks called but no auto pick cached for match %d", match["id"])
            return []
        missing = db.get_unpredicted_enrolled_users(conn, match["id"])
        for uid in missing:
            db.insert_auto_prediction(conn, uid, match["id"], auto_pick["pred_home"], auto_pick["pred_away"])

    if missing:
        _notify_match_auto_picks(slack_client, match, auto_pick, missing)

    return missing


def apply_auto_tournament_picks(slack_client) -> list[str]:
    """Apply cached auto tournament picks to users who have none. Returns affected user IDs."""
    if not _enabled():
        return []

    with db.db() as conn:
        auto_picks = db.get_auto_tournament_picks(conn)
        if not auto_picks:
            logger.warning("apply_auto_tournament_picks called but no auto tournament picks cached")
            return []
        missing = db.get_users_without_tournament_picks(conn)
        for uid in missing:
            db.insert_auto_tournament_pick(
                conn, uid,
                auto_picks["winner"], auto_picks["top_scorer"],
                auto_picks["semi1"], auto_picks["semi2"],
                auto_picks["semi3"], auto_picks["semi4"],
                auto_picks["zebra"], auto_picks["zebra_tier"],
                auto_picks["group_goals_guess"],
            )

    if missing:
        _notify_tournament_auto_picks(slack_client, auto_picks, missing)

    return missing


def _auto_pick_pct() -> int:
    try:
        return int(float(os.getenv("AUTO_PICK_POINTS_MULTIPLIER", "0.75")) * 100)
    except ValueError:
        return 75


def _notify_match_auto_picks(slack_client, match, auto_pick, user_ids: list[str]):
    reasoning_line = (
        f"\n> _{auto_pick['reasoning']}_"
        if auto_pick.get("reasoning") else ""
    )
    pct = _auto_pick_pct()
    for uid in user_ids:
        try:
            slack_client.chat_postMessage(
                channel=uid,
                text=(
                    f":robot_face: *Auto-pick applied — "
                    f"{match['home_team']} vs {match['away_team']}* "
                    f"({stage_label(match['stage'])})\n\n"
                    f"You hadn't predicted this match so I picked "
                    f"*{auto_pick['pred_home']} - {auto_pick['pred_away']}* for you.{reasoning_line}\n\n"
                    f":warning: Auto-picks only earn *{pct}% of the points* a correct prediction would score. "
                    f"Next time, use `/predict` before kickoff to earn *full points!*"
                ),
            )
        except Exception as exc:
            logger.warning("Failed to DM auto-pick notification to %s: %s", uid, exc)


def _notify_tournament_auto_picks(slack_client, auto_picks, user_ids: list[str]):
    from app.scoring import ZEBRA_WILDCARD
    tier_label = ":black_joker: Wildcard" if auto_picks["zebra_tier"] == "WILDCARD" else "⭐ Bold"
    semis = "  ·  ".join(
        f"{flag(auto_picks[f'semi{i}'])} {auto_picks[f'semi{i}']}" for i in range(1, 5)
        if auto_picks[f"semi{i}"]
    )
    reasoning_line = (
        f"\n> _{auto_picks['reasoning']}_"
        if auto_picks.get("reasoning") else ""
    )
    for uid in user_ids:
        try:
            slack_client.chat_postMessage(
                channel=uid,
                text=(
                    f":robot_face: *Tournament picks auto-generated!*\n\n"
                    f"You missed the deadline so I picked for you:{reasoning_line}\n\n"
                    f":first_place_medal: Winner: *{flag(auto_picks['winner'])} {auto_picks['winner']}*\n"
                    f":athletic_shoe: Golden Boot: *{auto_picks['top_scorer']}*\n"
                    f":four: Semis: {semis}\n"
                    f":zebra_face: Zebra: *{flag(auto_picks['zebra'])} {auto_picks['zebra']}* ({tier_label})\n"
                    f":goal_net: Group goals guess: *{auto_picks['group_goals_guess']}*\n\n"
                    f"These count for full points. You're all set!"
                ),
            )
        except Exception as exc:
            logger.warning("Failed to DM tournament auto-pick notification to %s: %s", uid, exc)
