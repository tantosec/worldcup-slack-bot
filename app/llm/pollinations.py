import json
import logging
import time

import requests

from app.football import stage_label

logger = logging.getLogger(__name__)

_URL = "https://text.pollinations.ai/"
_TIMEOUT = 40
_RETRY_SLEEP = 30
_MAX_RETRIES = 5


def _call(prompt: str, attempt: int = 1) -> dict:
    r = requests.post(
        _URL,
        json={
            "messages": [{"role": "user", "content": prompt}],
            "model": "openai",
            "seed": 42,
            "jsonMode": True,
        },
        timeout=_TIMEOUT,
    )
    if r.status_code == 429:
        raise RuntimeError(f"rate limited (429)")
    r.raise_for_status()
    return json.loads(r.text)


def _with_retries(prompt: str) -> dict:
    last_exc = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            result = _call(prompt, attempt)
            return result
        except Exception as exc:
            last_exc = exc
            logger.warning("Pollinations attempt %d/%d failed: %s", attempt, _MAX_RETRIES, exc)
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_SLEEP)
    raise RuntimeError(f"All {_MAX_RETRIES} Pollinations attempts failed: {last_exc}")


class PollinationsProvider:

    def predict_match(self, home_team, away_team, stage, home_prob, draw_prob, away_prob):
        prompt = (
            f"You are a World Cup 2026 analyst. Predict the most likely final score for this match.\n\n"
            f"Match: {home_team} vs {away_team}\n"
            f"Stage: {stage_label(stage)}\n"
            f"Win probabilities: {home_team} {round(home_prob*100)}% · "
            f"Draw {round(draw_prob*100)}% · {away_team} {round(away_prob*100)}%\n\n"
            f'Respond ONLY with valid JSON, no other text: '
            f'{{"home": <integer>, "away": <integer>, "reasoning": "<one concise sentence>"}}'
        )
        data = _with_retries(prompt)
        return {
            "home": int(data["home"]),
            "away": int(data["away"]),
            "reasoning": data.get("reasoning"),
        }

    def predict_tournament_picks(self, zebra_bold, zebra_wildcard, players):
        prompt = (
            f"You are a World Cup 2026 analyst. Generate tournament picks for a player who missed the deadline.\n\n"
            f"Bold zebra teams (standard points): {', '.join(zebra_bold)}\n"
            f"Wildcard zebra teams (3x points): {', '.join(zebra_wildcard)}\n"
            f"Eligible golden boot players (midfielders and attackers): {', '.join(players)}\n\n"
            f"Rules:\n"
            f"- winner must be a real WC 2026 team\n"
            f"- golden_boot must be spelled exactly as in the eligible players list\n"
            f"- semi1-4 must be 4 different real WC 2026 teams\n"
            f"- zebra must be spelled exactly as in one of the zebra lists\n"
            f"- zebra_tier must be BOLD or WILDCARD matching which list the team is from\n"
            f"- group_goals_guess: integer, total goals across all 72 group stage matches\n\n"
            f"Respond ONLY with valid JSON, no other text:\n"
            f'{{"winner":"<team>","golden_boot":"<player>","semi1":"<team>","semi2":"<team>",'
            f'"semi3":"<team>","semi4":"<team>","zebra":"<team>","zebra_tier":"BOLD or WILDCARD",'
            f'"group_goals_guess":<integer>,"reasoning":"<two sentences>"}}'
        )
        data = _with_retries(prompt)
        return {
            "winner": data["winner"],
            "golden_boot": data["golden_boot"],
            "semi1": data["semi1"],
            "semi2": data["semi2"],
            "semi3": data["semi3"],
            "semi4": data["semi4"],
            "zebra": data["zebra"],
            "zebra_tier": data["zebra_tier"],
            "group_goals_guess": int(data["group_goals_guess"]),
            "reasoning": data.get("reasoning"),
        }
