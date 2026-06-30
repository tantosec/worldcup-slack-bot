import json
import logging
import os

from app.config import COMPETITION_NAME, GROUP_STAGE_MATCH_COUNT
from app.football import stage_label

logger = logging.getLogger(__name__)

# STUB — wire up by setting LLM_PROVIDER=groq and GROQ_API_KEY in .env
# Install: pip install groq
# Docs: https://console.groq.com/docs/quickstart


class GroqProvider:

    def __init__(self, api_key: str):
        # from groq import Groq
        # self._client = Groq(api_key=api_key)
        self._api_key = api_key

    def _call(self, prompt: str) -> dict:
        raise NotImplementedError(
            "Groq provider is a stub. Uncomment the implementation and install the groq package."
        )
        # response = self._client.chat.completions.create(
        #     model="llama-3.3-70b-versatile",
        #     messages=[{"role": "user", "content": prompt}],
        #     response_format={"type": "json_object"},
        #     temperature=0.2,
        # )
        # return json.loads(response.choices[0].message.content)

    def predict_match(self, home_team, away_team, stage, home_prob, draw_prob, away_prob):
        prompt = (
            f"You are a {COMPETITION_NAME} analyst. Predict the most likely final score for this match.\n\n"
            f"Match: {home_team} vs {away_team}\n"
            f"Stage: {stage_label(stage)}\n"
            f"Win probabilities: {home_team} {round(home_prob*100)}% · "
            f"Draw {round(draw_prob*100)}% · {away_team} {round(away_prob*100)}%\n\n"
            f'Respond ONLY with valid JSON: {{"home": <integer>, "away": <integer>, "reasoning": "<one concise sentence>"}}'
        )
        data = self._call(prompt)
        return {
            "home": int(data["home"]),
            "away": int(data["away"]),
            "reasoning": data.get("reasoning"),
        }

    def predict_tournament_picks(self, zebra_bold, zebra_wildcard, players):
        prompt = (
            f"You are a {COMPETITION_NAME} analyst. Generate tournament picks for a player who missed the deadline.\n\n"
            f"Bold zebra teams (standard points): {', '.join(zebra_bold)}\n"
            f"Wildcard zebra teams (3x points): {', '.join(zebra_wildcard)}\n"
            f"Eligible golden boot players: {', '.join(players)}\n\n"
            f"Rules: golden_boot must match exactly from the list; zebra must match exactly; "
            f"zebra_tier is BOLD or WILDCARD; group_goals_guess is total goals across {GROUP_STAGE_MATCH_COUNT} group matches.\n\n"
            f'Respond ONLY with valid JSON: {{"winner":"<team>","golden_boot":"<player>",'
            f'"semi1":"<team>","semi2":"<team>","semi3":"<team>","semi4":"<team>",'
            f'"zebra":"<team>","zebra_tier":"BOLD or WILDCARD","group_goals_guess":<int>,"reasoning":"<two sentences>"}}'
        )
        data = self._call(prompt)
        return {
            "winner": data["winner"],
            "golden_boot": data["golden_boot"],
            "semi1": data["semi1"], "semi2": data["semi2"],
            "semi3": data["semi3"], "semi4": data["semi4"],
            "zebra": data["zebra"],
            "zebra_tier": data["zebra_tier"],
            "group_goals_guess": int(data["group_goals_guess"]),
            "reasoning": data.get("reasoning"),
        }
