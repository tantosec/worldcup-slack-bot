#!/usr/bin/env python3
"""
One-off repair for the 3rd-place match that was mis-tagged as GROUP_STAGE.

ESPN's season slug for the third-place playoff is "3rd-place-match", but the
old _STAGE_MAP only had "3rd-place", so the match silently fell back to the
GROUP_STAGE default. Two consequences:
  1. It never appeared under the finals in /mystats.
  2. Its predictions were scored at the GROUP_STAGE 1.0x multiplier instead of
     THIRD_PLACE's 3.0x.

This script:
  - re-tags the 3rd-place match's stage to THIRD_PLACE (identified via ESPN,
    by external_id), and
  - recomputes that match's predictions.points using the SAME logic as
    score_finished_matches (90-min result, upset bonus, stage multiplier,
    auto-pick multiplier) and UPDATEs them directly.

It sends NO Slack DMs or channel posts. Totals/ranks recompute live from
SUM(predictions.points), so they self-correct. The belated THIRD_PLACE phase
wrap is intentionally NOT suppressed here — the scheduler will post it.

Usage (inside the container):
    python /tmp/repair_third_place.py            # dry-run, prints before/after
    python /tmp/repair_third_place.py --apply    # writes the changes
"""

import os
import sys

from app import db
from app.espn import fetch_all_matches
from app.config import TOURNAMENT_START
from app.scoring import calculate_points


def _auto_pick_multiplier() -> float:
    try:
        return float(os.getenv("AUTO_PICK_POINTS_MULTIPLIER", "0.75"))
    except ValueError:
        return 0.75


def main(apply: bool):
    # 1. Identify the third-place match(es) authoritatively from ESPN.
    espn_matches = fetch_all_matches(from_date=TOURNAMENT_START)
    third_ext_ids = [m["external_id"] for m in espn_matches if m.get("stage") == "THIRD_PLACE"]
    if not third_ext_ids:
        print("ERROR: ESPN returned no THIRD_PLACE match — aborting.", file=sys.stderr)
        sys.exit(1)
    print(f"ESPN third-place external_id(s): {third_ext_ids}")

    auto_mult = _auto_pick_multiplier()

    with db.db() as conn:
        for ext_id in third_ext_ids:
            row = conn.execute(
                "SELECT * FROM matches WHERE external_id = ?", (ext_id,)
            ).fetchone()
            if row is None:
                print(f"  external_id {ext_id}: no DB row found — skipping.")
                continue

            match = dict(row)
            print(f"\nMatch {match['id']}: {match['home_team']} vs {match['away_team']}"
                  f"  (current stage={match['stage']}, status={match['status']})")

            act_home = match["home_score_90"] if match["home_score_90"] is not None else match["home_score"]
            act_away = match["away_score_90"] if match["away_score_90"] is not None else match["away_score"]
            print(f"  90-min result used for scoring: {act_home} - {act_away}")

            # Score against the CORRECT stage regardless of the stored value.
            match["stage"] = "THIRD_PLACE"

            preds = conn.execute(
                "SELECT * FROM predictions WHERE match_id = ?", (match["id"],)
            ).fetchall()

            for p in preds:
                if p["home_score"] is None:
                    continue
                new_pts = calculate_points(
                    p["home_score"], p["away_score"],
                    act_home, act_away,
                    match["home_team"], match["away_team"],
                    "THIRD_PLACE",
                    match=match,
                )
                if p["is_auto"]:
                    new_pts = int(new_pts * auto_mult)
                old_pts = p["points"]
                flag = "" if old_pts == new_pts else "  <-- CHANGED"
                print(f"    {p['slack_user_id']}: pick {p['home_score']}-{p['away_score']}"
                      f"  {old_pts} -> {new_pts} pts{flag}")
                if apply:
                    conn.execute(
                        "UPDATE predictions SET points = ? WHERE id = ?",
                        (new_pts, p["id"]),
                    )

            if apply:
                conn.execute(
                    "UPDATE matches SET stage = 'THIRD_PLACE' WHERE id = ?",
                    (match["id"],),
                )
                print(f"  APPLIED: stage -> THIRD_PLACE, {len(preds)} predictions recomputed.")
            else:
                print(f"  (dry-run) would set stage -> THIRD_PLACE and recompute {len(preds)} predictions.")

    print("\nDone." + ("" if apply else "  (dry-run — no changes written; re-run with --apply)"))


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
