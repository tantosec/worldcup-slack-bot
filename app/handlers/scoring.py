import os
from app.scoring import (
    TOURNAMENT_PICK_POINTS, SEMI_PICK_POINTS,
    GROUP_GOALS_WIN_POINTS, GROUP_GOALS_NEAR_POINTS,
    ZEBRA_POINTS, ZEBRA_WILDCARD_MULTIPLIER, ZEBRA_BOLD, ZEBRA_WILDCARD,
)

_MAX_WILDCARD_WINNER = ZEBRA_POINTS["WINNER"] * ZEBRA_WILDCARD_MULTIPLIER
_ORG = os.getenv("ORG_NAME", "TantoSec")

SCORING_BLOCKS = [
    {
        "type": "header",
        "text": {"type": "plain_text", "text": f"⚽ {_ORG} World Cup 2026 — Scoring Rules", "emoji": True},
    },
    {"type": "divider"},
    {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                "*Match Predictions*\n"
                ":dart: Exact score → *9 pts*\n"
                ":white_check_mark: Correct result (W/D/L) → *3 pts*\n"
                ":zap: Upset bonus → *+2 pts* _(predicted the underdog wins — and they did!)_\n"
                "_Knockout matches: scored on 90-minute result. Extra time & penalties don't count._"
            ),
        },
    },
    {
        "type": "context",
        "elements": [{
            "type": "mrkdwn",
            "text": (
                "*Underdog:* Bookmaker odds — favourite must be ≥25% more likely to win. "
                "Falls back to FIFA rankings when odds aren't available. "
                "A draw does *not* trigger the bonus."
            ),
        }],
    },
    {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                "*Knockout multipliers*\n"
                "R32 / R16 → ×1.5  ·  QF → ×2  ·  SF / 3rd → ×2.5  ·  :trophy: Final → ×3\n"
                "_(exact score in the Final = 27 pts!)_"
            ),
        },
    },
    {"type": "divider"},
    {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                f"*Tournament Picks* _(lock before Matchday 2 on 18 Jun)_\n"
                f":first_place_medal: World Cup Winner → *{TOURNAMENT_PICK_POINTS} pts*\n"
                f":athletic_shoe: Golden Boot (top scorer) → *{TOURNAMENT_PICK_POINTS} pts*\n"
                f":four: Semi-finalists → *{SEMI_PICK_POINTS} pts each* ({SEMI_PICK_POINTS * 4} pts max)"
            ),
        },
    },
    {"type": "divider"},
    {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                f"*:goal_net: Group Stage Total Goals* _(optional)_\n"
                f"Guess total goals across all 72 group matches\n"
                f"1st closest → *{GROUP_GOALS_WIN_POINTS} pts*  ·  2nd closest → *{GROUP_GOALS_NEAR_POINTS} pts*"
            ),
        },
    },
    {"type": "divider"},
    {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                f"*:zebra_face: Zebra Pick* _(optional — pick an underdog)_\n"
                f"R32 *{ZEBRA_POINTS['LAST_32']}*  ·  "
                f"R16 *{ZEBRA_POINTS['LAST_16']}*  ·  "
                f"QF *{ZEBRA_POINTS['QUARTER_FINALS']}*  ·  "
                f"SF *{ZEBRA_POINTS['SEMI_FINALS']}*  ·  "
                f"Final *{ZEBRA_POINTS['FINAL']}*  ·  "
                f"Winner :fire: *{ZEBRA_POINTS['WINNER']}* pts"
            ),
        },
    },
    {
        "type": "context",
        "elements": [{
            "type": "mrkdwn",
            "text": (
                f"⭐ Bold ({len(ZEBRA_BOLD)} teams) — standard points  ·  "
                f":black_joker: Wildcard ({len(ZEBRA_WILDCARD)} teams) — *×{ZEBRA_WILDCARD_MULTIPLIER} all points* "
                f"_(up to {_MAX_WILDCARD_WINNER} pts if your zebra wins it all!)_"
            ),
        }],
    },
    {"type": "divider"},
    {
        "type": "context",
        "elements": [{
            "type": "mrkdwn",
            "text": (
                ":lock: *Match predictions can be updated any time before kickoff — they lock when the match starts.* "
                "Tournament picks can be updated until Matchday 2 begins on *18 Jun*."
            ),
        }],
    },
]


def handle_scoring(respond, body):
    respond(response_type="ephemeral", blocks=SCORING_BLOCKS, text=f"{_ORG} World Cup 2026 — Scoring Rules")
