import logging
import os
import requests

from app.fifa_rankings import get_rank
from app.flags import flag

logger = logging.getLogger(__name__)

BASE_URL = "https://api.the-odds-api.com/v4"
SPORT = "soccer_fifa_world_cup"

# Mapping from The Odds API team names → our football-data.org team names
TEAM_NAME_MAP: dict[str, str] = {
    "USA":                      "United States",
    "United States of America": "United States",
    "Côte d'Ivoire":            "Ivory Coast",
    "Cote d'Ivoire":            "Ivory Coast",
    "DR Congo":                 "Congo DR",
    "Democratic Republic of Congo": "Congo DR",
    "Cape Verde":               "Cape Verde Islands",
    "Bosnia & Herzegovina":     "Bosnia-Herzegovina",
    "Bosnia and Herzegovina":   "Bosnia-Herzegovina",
    "Curacao":                  "Curaçao",
    "South Korea":              "South Korea",
    "Korea Republic":           "South Korea",
}


def _normalize(name: str) -> str:
    return TEAM_NAME_MAP.get(name, name)


def _avg_odds(bookmakers: list[dict], outcome_name: str) -> float | None:
    prices = []
    for bm in bookmakers:
        for market in bm.get("markets", []):
            if market["key"] != "h2h":
                continue
            for o in market.get("outcomes", []):
                if o["name"] == outcome_name:
                    prices.append(o["price"])
    return sum(prices) / len(prices) if prices else None


def fetch_and_store_odds(conn) -> None:
    api_key = os.getenv("ODDS_API_KEY")
    if not api_key:
        logger.warning("ODDS_API_KEY not set — skipping odds sync")
        return

    try:
        resp = requests.get(
            f"{BASE_URL}/sports/{SPORT}/odds/",
            params={
                "apiKey":      api_key,
                "regions":     "eu",
                "markets":     "h2h",
                "oddsFormat":  "decimal",
            },
            timeout=15,
        )
        resp.raise_for_status()
        remaining = resp.headers.get("x-requests-remaining", "?")
        used = resp.headers.get("x-requests-used", "?")
        logger.info("Odds API: used=%s remaining=%s", used, remaining)
        data = resp.json()
    except Exception as exc:
        logger.error("Failed to fetch odds: %s", exc)
        return

    updated = 0
    for event in data:
        home = _normalize(event.get("home_team", ""))
        away = _normalize(event.get("away_team", ""))
        bookmakers = event.get("bookmakers", [])

        home_odds = _avg_odds(bookmakers, event["home_team"])
        away_odds = _avg_odds(bookmakers, event["away_team"])
        draw_odds = _avg_odds(bookmakers, "Draw")

        if not home_odds or not away_odds or not draw_odds:
            continue

        row = conn.execute(
            "SELECT id FROM matches WHERE home_team = ? AND away_team = ?",
            (home, away),
        ).fetchone()

        if not row:
            logger.debug("Odds: no DB match for %s vs %s", home, away)
            continue

        conn.execute(
            "UPDATE matches SET home_odds = ?, draw_odds = ?, away_odds = ? WHERE id = ?",
            (home_odds, draw_odds, away_odds, row["id"]),
        )
        updated += 1

    logger.info("Odds sync: updated %d matches", updated)


def odds_to_probs(home_odds: float, draw_odds: float, away_odds: float) -> tuple[int, int, int]:
    """Convert decimal odds to normalized win probabilities (sum to 100)."""
    raw_h = 1 / home_odds
    raw_d = 1 / draw_odds
    raw_a = 1 / away_odds
    total = raw_h + raw_d + raw_a
    return (
        round(raw_h / total * 100),
        round(raw_d / total * 100),
        round(raw_a / total * 100),
    )


def format_prob_line(match) -> str | None:
    """Return formatted probability line or None if no odds stored."""
    h_odds = match["home_odds"]
    d_odds = match["draw_odds"]
    a_odds = match["away_odds"]
    if not h_odds or not d_odds or not a_odds:
        return None
    h_pct, d_pct, a_pct = odds_to_probs(h_odds, d_odds, a_odds)
    home = match["home_team"]
    away = match["away_team"]
    return f":bar_chart: {flag(home)} {home} *{h_pct}%*  ·  Draw *{d_pct}%*  ·  {flag(away)} {away} *{a_pct}%*"


def get_underdog(match) -> str | None:
    """Return the underdog team name.

    Primary: compare win probabilities from odds (draw excluded — irrelevant to
    relative team strength). Only show underdog when gap >= 15 percentage points.
    Tiebreaker: FIFA ranking gap >= 15 positions when odds are unavailable or gap
    is too small to be meaningful.
    """
    h_odds = match["home_odds"]
    d_odds = match["draw_odds"]
    a_odds = match["away_odds"]

    if h_odds and d_odds and a_odds:
        h_pct, _d_pct, a_pct = odds_to_probs(h_odds, d_odds, a_odds)
        gap = abs(h_pct - a_pct)
        if gap >= 15:
            return match["home_team"] if h_pct < a_pct else match["away_team"]
        # Gap too small — fall through to ranking tiebreaker

    # Fallback: FIFA ranking (higher number = weaker team = underdog)
    home_rank = get_rank(match["home_team"])
    away_rank = get_rank(match["away_team"])
    if abs(home_rank - away_rank) >= 15:
        return match["home_team"] if home_rank > away_rank else match["away_team"]

    return None


def format_underdog_line(match, action: bool = False) -> str | None:
    """Return underdog line. action=True shows 'Upset pick: X wins → +2 bonus pts'."""
    underdog = get_underdog(match)
    if not underdog:
        return None
    if action:
        return f":zap: Upset pick: {flag(underdog)} {underdog} wins → *+2 bonus pts*"
    return f":zap: Underdog: {flag(underdog)} {underdog}"
