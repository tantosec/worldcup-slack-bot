from app import db
from app.config import COMPETITION_NAME
from app.flags import flag

MEDALS = {1: ":first_place_medal:", 2: ":second_place_medal:", 3: ":third_place_medal:"}


def _bonus_icons(row, confirmed_semis=None, zebra_statuses=None) -> str:
    parts = []
    if row["winner_points"]:
        w = row["picked_winner"]
        f = flag(w) + " " if w else ""
        parts.append(f":first_place_medal: _({f}+{row['winner_points']})_")
    if row["scorer_points"]:
        parts.append(f":athletic_shoe: _(+{row['scorer_points']})_")
    zebra_pts = row["zebra_points"]
    if zebra_pts is not None:
        z = row["picked_zebra"]
        f = flag(z) + " " if z else ""
        if zebra_pts == 0:
            status_icon = " :skull:"
        elif zebra_statuses and z and zebra_statuses.get(z):
            status_icon = " :skull:"
        elif zebra_pts > 0:
            status_icon = " :fire:"
        else:
            status_icon = ""
        parts.append(f":zebra_face: _({f}+{zebra_pts}{status_icon})_")
    if row["semi_points"]:
        if confirmed_semis:
            correct = [row[f"semi{i}"] for i in range(1, 5)
                       if row[f"semi{i}"] and row[f"semi{i}"] in confirmed_semis]
            flags_str = " ".join(flag(t) for t in correct) + " " if correct else ""
        else:
            flags_str = ""
        parts.append(f":four: _({flags_str}+{row['semi_points']})_")
    if row["group_goals_points"]:
        parts.append(f":goal_net: _(+{row['group_goals_points']})_")
    return "  ·  ".join(parts)


def handle_leaderboard(respond, client, body):
    with db.db() as conn:
        rows = db.get_leaderboard(conn)
        confirmed_semis = db.get_confirmed_semi_teams(conn)
        zebra_teams = {row["picked_zebra"] for row in rows if row["picked_zebra"]}
        zebra_statuses = {t: db.team_knocked_out(conn, t) for t in zebra_teams}

    if not rows:
        respond(response_type="ephemeral", text="No predictions scored yet. Check back after the first match!")
        return

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"🏆 {COMPETITION_NAME} — Leaderboard", "emoji": True}},
        {"type": "divider"},
    ]

    pairs = []
    for i, row in enumerate(rows, start=1):
        medal = MEDALS.get(i, f"`{i}.`")
        exact = row["exact_scores"] or 0
        upsets = row["upsets_called"] or 0
        bonus = _bonus_icons(row, confirmed_semis, zebra_statuses)
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

    respond(response_type="ephemeral", blocks=blocks, text=f"{COMPETITION_NAME} — Leaderboard")
