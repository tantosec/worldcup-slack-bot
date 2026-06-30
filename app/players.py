import json
import os
import unicodedata

_PATH = os.path.join(os.path.dirname(__file__), "data", "players.json")

with open(_PATH, encoding="utf-8") as _f:
    _PLAYERS: list[dict] = json.load(_f)


def _normalize(s: str) -> str:
    """Lowercase + strip accents for fuzzy matching."""
    return unicodedata.normalize("NFD", s.lower()).encode("ascii", "ignore").decode()


def search(query: str, limit: int = 20) -> list[dict]:
    """Return players whose name contains the query (accent-insensitive)."""
    q = _normalize(query.strip())
    if not q:
        return []
    return [p for p in _PLAYERS if q in _normalize(p["name"])][:limit]


def all_forwards(limit: int = 100) -> list[dict]:
    """Return attackers/midfielders — useful as default options before typing."""
    return [p for p in _PLAYERS if p["position"] == "Offence"][:limit]
