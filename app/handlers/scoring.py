import os
from app.scoring import (
    TOURNAMENT_PICK_POINTS, SEMI_PICK_POINTS,
    GROUP_GOALS_WIN_POINTS, GROUP_GOALS_NEAR_POINTS, GROUP_GOALS_NEAR_RANGE,
    ZEBRA_POINTS, ZEBRA_WILDCARD_MULTIPLIER, ZEBRA_BOLD, ZEBRA_WILDCARD,
)

_MAX_WILDCARD_WINNER = ZEBRA_POINTS["WINNER"] * ZEBRA_WILDCARD_MULTIPLIER
_ORG = os.getenv("ORG_NAME", "TantoSec")

SCORING_TEXT = (
    f"*:soccer: {_ORG} World Cup 2026 — Scoring Rules*\n"
    "\n"
    "*Match Predictions*\n"
    "  :dart: Exact score → *9 pts*\n"
    "  :white_check_mark: Correct result (W/D/L) → *3 pts*\n"
    "  :zap: Upset bonus → *+2 pts* _(predicted the underdog wins — and they did!)_\n"
    "\n"
    "  *How is the underdog determined?*\n"
    "  We use live betting odds (win probability only — draw excluded).\n"
    "  The team with the lower win % is the underdog, but only when the gap\n"
    "  is ≥15 percentage points — otherwise the match is too close to call.\n"
    "  When odds aren't available yet, FIFA rankings are used as a fallback\n"
    "  (gap ≥ 15 positions). A draw does *not* trigger the upset bonus —\n"
    "  you need to predict the underdog wins outright.\n"
    "\n"
    "  *Knockout stage multipliers:*\n"
    "  Round of 32 / Round of 16 → ×1.5\n"
    "  Quarter-finals → ×2\n"
    "  Semi-finals / 3rd Place → ×2.5\n"
    "  :trophy: Final → ×3 _(exact score in the final = 27 pts!)_\n"
    "\n"
    f"*Tournament Picks* _(lock before Matchday 2 on 18 Jun)_\n"
    f"  :first_place_medal: World Cup Winner → *{TOURNAMENT_PICK_POINTS} pts*\n"
    f"  :athletic_shoe: Golden Boot (top scorer) → *{TOURNAMENT_PICK_POINTS} pts*\n"
    f"  :four: Semi-finalists → *{SEMI_PICK_POINTS} pts each* ({SEMI_PICK_POINTS * 4} pts max)\n"
    "\n"
    f"*:goal_net: Group Stage Total Goals* _(optional)_\n"
    f"  Guess total goals across all 72 group matches\n"
    f"  Closest guess → *{GROUP_GOALS_WIN_POINTS} pts* · Within ±{GROUP_GOALS_NEAR_RANGE} → *{GROUP_GOALS_NEAR_POINTS} pts*\n"
    "\n"
    f"*:zebra_face: Zebra Pick* _(optional — pick an underdog)_\n"
    f"  Round of 32 → *{ZEBRA_POINTS['LAST_32']} pts*\n"
    f"  Round of 16 → *{ZEBRA_POINTS['LAST_16']} pts*\n"
    f"  Quarter-finals → *{ZEBRA_POINTS['QUARTER_FINALS']} pts*\n"
    f"  Semi-finals → *{ZEBRA_POINTS['SEMI_FINALS']} pts*\n"
    f"  Final → *{ZEBRA_POINTS['FINAL']} pts*\n"
    f"  Winner :fire: → *{ZEBRA_POINTS['WINNER']} pts*\n"
    f"\n"
    f"  ⭐ Bold ({len(ZEBRA_BOLD)} teams) — standard points\n"
    f"  :black_joker: Wildcard ({len(ZEBRA_WILDCARD)} teams) — *×{ZEBRA_WILDCARD_MULTIPLIER} all points* "
    f"_(up to {_MAX_WILDCARD_WINNER} pts if your zebra wins it all!)_\n"
    "\n"
    ":lock: *Match predictions are locked once submitted.* "
    "Tournament picks can be updated until Matchday 2 begins on *18 Jun*."
)


def handle_scoring(respond, body):
    respond(SCORING_TEXT)
