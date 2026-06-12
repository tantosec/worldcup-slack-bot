from app import db

MEDALS = {1: ":first_place_medal:", 2: ":second_place_medal:", 3: ":third_place_medal:"}


def handle_leaderboard(respond, client, body):
    with db.db() as conn:
        rows = db.get_leaderboard(conn)

    if not rows:
        respond(response_type="ephemeral", text="No predictions scored yet. Check back after the first match!")
        return

    lines = [":trophy: *FIFA World Cup 2026 — Leaderboard*\n"]
    for i, row in enumerate(rows, start=1):
        medal = MEDALS.get(i, f"`{i}.`")
        user = f"<@{row['slack_user_id']}>"
        pts = row["total_points"]
        exact = row["exact_scores"]
        upsets = row["upsets_called"]
        scored = row["scored_predictions"]
        total = row["total_predictions"]

        detail = f"*{pts} pts*  ·  :dart: {exact} exact  ·  :zap: {upsets} upsets  ·  {scored}/{total} scored"
        lines.append(f"{medal}  {user}  —  {detail}")

    respond(response_type="ephemeral", text="\n".join(lines))
