from app import db

MEDALS = {1: ":first_place_medal:", 2: ":second_place_medal:", 3: ":third_place_medal:"}


def _bonus_icons(row) -> str:
    parts = []
    if row["winner_points"]:
        parts.append(f":first_place_medal: _(+{row['winner_points']})_")
    if row["scorer_points"]:
        parts.append(f":athletic_shoe: _(+{row['scorer_points']})_")
    if row["zebra_points"]:
        parts.append(f":zebra_face: _(+{row['zebra_points']})_")
    if row["semi_points"]:
        parts.append(f":four: _(+{row['semi_points']})_")
    if row["group_goals_points"]:
        parts.append(f":goal_net: _(+{row['group_goals_points']})_")
    return "  ·  ".join(parts)


def handle_leaderboard(respond, client, body):
    with db.db() as conn:
        rows = db.get_leaderboard(conn)

    if not rows:
        respond(response_type="ephemeral", text="No predictions scored yet. Check back after the first match!")
        return

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "🏆 FIFA World Cup 2026 — Leaderboard", "emoji": True}},
        {"type": "divider"},
    ]

    pairs = []
    for i, row in enumerate(rows, start=1):
        medal = MEDALS.get(i, f"`{i}.`")
        exact = row["exact_scores"] or 0
        upsets = row["upsets_called"] or 0
        bonus = _bonus_icons(row)
        right = f"*{row['total_points']} pts*  ·  :dart: {exact}  ·  :zap: {upsets}"
        if bonus:
            right += f"\n{bonus}"
        pairs.append((f"{medal}  <@{row['slack_user_id']}>", right))

    for i in range(0, len(pairs), 5):
        chunk = pairs[i:i + 5]
        fields = []
        for left, right in chunk:
            fields.append({"type": "mrkdwn", "text": left})
            fields.append({"type": "mrkdwn", "text": right})
        blocks.append({"type": "section", "fields": fields})

    respond(response_type="ephemeral", blocks=blocks, text="FIFA World Cup 2026 — Leaderboard")
