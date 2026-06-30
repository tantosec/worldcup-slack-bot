#!/usr/bin/env python3
"""
Build players.json from ESPN Core API.

Fetches all squad players for a FIFA World Cup season, including name,
team (country), and position. Writes to app/players.json by default.

Usage:
    python scripts/build_players.py                  # defaults: year=2026, league=fifa.world
    python scripts/build_players.py --year 2030
    python scripts/build_players.py --league fifa.wwc --year 2027
    python scripts/build_players.py --output data/wc2026/players.json
"""

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

CORE_BASE = "https://sports.core.api.espn.com/v2/sports/soccer/leagues"
SITE_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"

POSITION_MAP = {
    "Goalkeeper": "Goalkeeper",
    "Defender":   "Defence",
    "Midfielder": "Midfield",
    "Forward":    "Offence",
}

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "worldcup-slack-bot/build-players"})


def get(url, params=None):
    resp = SESSION.get(url, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_team_names(league: str) -> dict[str, str]:
    """Return {team_id: displayName} for all teams in the competition."""
    data = get(f"{SITE_BASE}/{league}/teams", params={"limit": 100})
    teams = data["sports"][0]["leagues"][0]["teams"]
    return {t["team"]["id"]: t["team"]["displayName"] for t in teams}


def fetch_team_ids(league: str, year: int) -> list[str]:
    """Return list of team IDs for a specific season."""
    data = get(f"{CORE_BASE}/{league}/seasons/{year}/teams", params={"limit": 100})
    ids = []
    for item in data["items"]:
        m = re.search(r"/teams/(\d+)", item["$ref"])
        if m:
            ids.append(m.group(1))
    return ids


def fetch_athlete_ids_for_team(league: str, year: int, team_id: str) -> list[str]:
    data = get(
        f"{CORE_BASE}/{league}/seasons/{year}/teams/{team_id}/athletes",
        params={"limit": 50},
    )
    ids = []
    for item in data["items"]:
        m = re.search(r"/athletes/(\d+)", item["$ref"])
        if m:
            ids.append(m.group(1))
    return ids


def fetch_athlete(league: str, year: int, athlete_id: str) -> dict | None:
    try:
        data = get(f"{CORE_BASE}/{league}/seasons/{year}/athletes/{athlete_id}")
        pos_name = data.get("position", {}).get("name", "")
        return {
            "fullName": data.get("fullName", ""),
            "position_raw": pos_name,
        }
    except Exception as e:
        print(f"  WARNING: failed to fetch athlete {athlete_id}: {e}", file=sys.stderr)
        return None


def build_players(league: str, year: int, output_path: str, workers: int = 20):
    print(f"Building players.json for {league} {year} → {output_path}")

    print("Fetching team names...")
    team_names = fetch_team_names(league)
    print(f"  Found {len(team_names)} teams in site API")

    print("Fetching season team IDs...")
    team_ids = fetch_team_ids(league, year)
    print(f"  {len(team_ids)} teams in {year} season")

    # Collect (team_id, athlete_id) pairs
    print("Fetching athlete IDs per team...")
    athlete_pairs: list[tuple[str, str]] = []
    for team_id in team_ids:
        try:
            ids = fetch_athlete_ids_for_team(league, year, team_id)
            for aid in ids:
                athlete_pairs.append((team_id, aid))
        except Exception as e:
            print(f"  WARNING: failed to fetch roster for team {team_id}: {e}", file=sys.stderr)

    total = len(athlete_pairs)
    print(f"  {total} athletes to fetch across {len(team_ids)} teams")

    # Fetch all athletes in parallel
    print(f"Fetching athlete details ({workers} workers)...")
    players = []
    done = 0

    def fetch_one(pair):
        team_id, athlete_id = pair
        result = fetch_athlete(league, year, athlete_id)
        return team_id, result

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_one, pair): pair for pair in athlete_pairs}
        for future in as_completed(futures):
            done += 1
            if done % 100 == 0 or done == total:
                print(f"  {done}/{total}", end="\r", flush=True)
            team_id, data = future.result()
            if data is None:
                continue
            team_name = team_names.get(team_id, f"Unknown({team_id})")
            position = POSITION_MAP.get(data["position_raw"], data["position_raw"])
            if not position:
                print(
                    f"  WARNING: unknown position '{data['position_raw']}' for {data['fullName']}",
                    file=sys.stderr,
                )
            players.append({
                "name":     data["fullName"],
                "team":     team_name,
                "position": position,
            })

    print()  # newline after progress

    # Sort: by team, then by position order, then by name
    position_order = {"Goalkeeper": 0, "Defence": 1, "Midfield": 2, "Offence": 3}
    players.sort(key=lambda p: (p["team"], position_order.get(p["position"], 9), p["name"]))

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(players, f, ensure_ascii=False, indent=2)

    print(f"Done. {len(players)} players written to {output_path}")

    # Summary
    from collections import Counter
    by_pos = Counter(p["position"] for p in players)
    for pos, count in sorted(by_pos.items()):
        print(f"  {pos}: {count}")


def main():
    parser = argparse.ArgumentParser(description="Build players.json from ESPN API")
    parser.add_argument("--year",    type=int, default=2026,             help="Season year (default: 2026)")
    parser.add_argument("--league",  default="fifa.world",               help="ESPN league slug (default: fifa.world)")
    parser.add_argument("--output",  default="app/data/players.json",     help="Output file path (default: app/data/players.json)")
    parser.add_argument("--workers", type=int, default=20,               help="Parallel HTTP workers (default: 20)")
    args = parser.parse_args()

    build_players(args.league, args.year, args.output, args.workers)


if __name__ == "__main__":
    main()
