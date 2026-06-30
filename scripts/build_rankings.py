#!/usr/bin/env python3
"""
Build fifa_rankings.json from the official FIFA API.

Fetches the current FIFA/Coca-Cola World Rankings and writes a
{team_name: rank} mapping to app/fifa_rankings.json by default.

Usage:
    python scripts/build_rankings.py                    # men's ranking → app/fifa_rankings.json
    python scripts/build_rankings.py --gender women
    python scripts/build_rankings.py --output data/wc2026/fifa_rankings.json
"""

import argparse
import json
import os
import sys

import requests

FIFA_RANKINGS_URL = "https://api.fifa.com/api/v3/rankings/"

GENDER_MAP = {
    "men":   1,
    "women": 2,
}

# FIFA uses different official names than ESPN's displayName.
# Keys are FIFA names, values are ESPN displayNames.
FIFA_TO_ESPN: dict[str, str] = {
    "IR Iran":               "Iran",
    "Korea Republic":        "South Korea",
    "USA":                   "United States",
    "Côte d'Ivoire":         "Ivory Coast",
    "Bosnia and Herzegovina":"Bosnia-Herzegovina",
    "Cabo Verde":            "Cape Verde",
}


def fetch_rankings(gender: str, count: int = 211) -> list[dict]:
    gender_id = GENDER_MAP.get(gender)
    if gender_id is None:
        print(f"ERROR: unknown gender '{gender}'. Use 'men' or 'women'.", file=sys.stderr)
        sys.exit(1)

    resp = requests.get(
        FIFA_RANKINGS_URL,
        params={"gender": gender_id, "count": count, "language": "en"},
        headers={
            "User-Agent": "worldcup-slack-bot/build-rankings",
            "Accept": "application/json",
            "Origin": "https://inside.fifa.com",
            "Referer": "https://inside.fifa.com/fifa-world-ranking/men",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    results = data.get("Results") or []
    if not results:
        print("ERROR: API returned no results.", file=sys.stderr)
        sys.exit(1)
    return results


def build_rankings(gender: str, output_path: str):
    print(f"Fetching FIFA {gender}'s rankings → {output_path}")

    results = fetch_rankings(gender)
    print(f"  {len(results)} nations returned")

    rankings: dict[str, int] = {}
    for entry in results:
        name_entries = entry.get("TeamName", [])
        name = next(
            (n["Description"] for n in name_entries if n.get("Locale") == "en-GB"),
            name_entries[0]["Description"] if name_entries else None,
        )
        rank = entry.get("Rank")
        if name and rank:
            rankings[FIFA_TO_ESPN.get(name, name)] = rank

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(rankings, f, ensure_ascii=False, indent=2, sort_keys=False)

    print(f"Done. {len(rankings)} nations written to {output_path}")
    print(f"  #1: {next(iter(rankings))}")
    print(f"  #211: {list(rankings.keys())[-1]}")


def main():
    parser = argparse.ArgumentParser(description="Build fifa_rankings.json from official FIFA API")
    parser.add_argument("--gender", default="men", choices=["men", "women"], help="Gender (default: men)")
    parser.add_argument("--output", default="app/data/fifa_rankings.json",   help="Output file path (default: app/data/fifa_rankings.json)")
    args = parser.parse_args()

    build_rankings(args.gender, args.output)


if __name__ == "__main__":
    main()
