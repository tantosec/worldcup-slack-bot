import json
import os

_PATH = os.path.join(os.path.dirname(__file__), "data", "fifa_rankings.json")

with open(_PATH, encoding="utf-8") as _f:
    RANKINGS: dict[str, int] = json.load(_f)

UNKNOWN_RANK = 99


def get_rank(team_name: str) -> int:
    return RANKINGS.get(team_name, UNKNOWN_RANK)
