from app import db
from app.flags import FLAGS, flag
from app.football import is_kickoff_passed
from app.scoring import (
    TOURNAMENT_PICK_POINTS, SEMI_PICK_POINTS,
    GROUP_GOALS_WIN_POINTS, GROUP_GOALS_NEAR_POINTS,
    ZEBRA_POINTS, ZEBRA_WILDCARD_MULTIPLIER,
    ZEBRA_BOLD, ZEBRA_WILDCARD,
)

import json

CALLBACK_ID = "submit_tournament_picks"
WINNER_ACTION = "pick_winner"
OPEN_PICKS_MODAL_ACTION = "open_picks_modal_view"
PICKS_MODAL_PREV_ACTION = "picks_modal_prev"
PICKS_MODAL_NEXT_ACTION = "picks_modal_next"
PICKS_MODAL_PAGE_SIZE = 10
_EPHEMERAL_PREVIEW = 3
SCORER_ACTION = "pick_scorer"
ZEBRA_ACTION = "pick_zebra"
SEMI_ACTIONS = ["pick_semi1", "pick_semi2", "pick_semi3", "pick_semi4"]
GOALS_ACTION = "pick_group_goals"

WC_TEAMS = sorted(FLAGS.keys())

_SCORING_RUNDOWN = (
    "*:soccer: Match Predictions*\n"
    "  :dart: Exact score → *9 pts*\n"
    "  :white_check_mark: Correct result → *3 pts*\n"
    "  :zap: Upset bonus → *+2 pts*\n"
    "  Stage multipliers: ×1.5 (R32/R16) · ×2 (QF) · ×2.5 (SF) · ×3 (Final)\n"
    "  _Knockouts: scored on 90-min result — ET & penalties don't count_\n"
    "\n"
    f"*:trophy: Tournament Picks* _({TOURNAMENT_PICK_POINTS} pts each if correct)_\n"
    "  :first_place_medal: World Cup Winner\n"
    "  :athletic_shoe: Golden Boot (top scorer)\n"
    "\n"
    f"*:four: Semi-finalists* _(pick all 4 — {SEMI_PICK_POINTS} pts per correct team, {SEMI_PICK_POINTS * 4} pts max)_\n"
    "\n"
    f"*:goal_net: Group Stage Goals* _(guess total goals across all 72 group matches)_\n"
    f"  1st closest → *{GROUP_GOALS_WIN_POINTS} pts* · "
    f"2nd closest → *{GROUP_GOALS_NEAR_POINTS} pts*\n"
    "\n"
    "*:zebra_face: Zebra Pick* _(pick an underdog — points if they go far!)_\n"
    f"  R32 → *{ZEBRA_POINTS['LAST_32']} pts* · "
    f"R16 → *{ZEBRA_POINTS['LAST_16']} pts* · "
    f"QF → *{ZEBRA_POINTS['QUARTER_FINALS']} pts*\n"
    f"  SF → *{ZEBRA_POINTS['SEMI_FINALS']} pts* · "
    f"Final → *{ZEBRA_POINTS['FINAL']} pts* · "
    f"Winner → *{ZEBRA_POINTS['WINNER']} pts*\n"
    f"  :black_joker: Wildcard tier = *×{ZEBRA_WILDCARD_MULTIPLIER}* all of the above"
)


def open_picks_modal(client, trigger_id: str, slack_user_id: str, response_url: str = "", channel_id: str = ""):
    with db.db() as conn:
        if not db.is_enrolled(conn, slack_user_id):
            client.chat_postEphemeral(
                channel=slack_user_id,
                user=slack_user_id,
                text=":wave: You need to join the league first — use `/register` to sign up!",
            )
            return

        locked = _picks_locked(conn)
        existing = db.get_tournament_pick(conn, slack_user_id)

    if locked and not existing:
        client.chat_postEphemeral(
            channel=slack_user_id,
            user=slack_user_id,
            text=":lock: Tournament picks are locked — Matchday 2 has already begun.",
        )
        return

    if locked and existing:
        with db.db() as conn:
            all_picks = db.get_all_picks_for_reveal(conn)
            own_zebra_knocked_out = db.team_knocked_out(conn, existing["zebra"]) if existing["zebra"] else None

        # DM: just the user's own picks
        from app.handlers.me import _picks_text
        client.chat_postMessage(
            channel=slack_user_id,
            text=f":lock: *Your Tournament Picks*\n{_picks_text(existing, locked=True, zebra_knocked_out=own_zebra_knocked_out)}",
        )

        # Channel ephemeral: preview of others + "see all" button
        preview_blocks = _build_picks_preview_blocks(all_picks, slack_user_id)
        target_channel = channel_id or slack_user_id
        if response_url:
            from slack_sdk.webhook import WebhookClient
            WebhookClient(response_url).send(response_type="ephemeral", blocks=preview_blocks, text="🔮 Everyone's Picks")
        else:
            client.chat_postEphemeral(channel=target_channel, user=slack_user_id, blocks=preview_blocks, text="🔮 Everyone's Picks")
        return

    team_options = [
        {
            "text": {"type": "plain_text", "text": f"{flag(t)} {t}", "emoji": True},
            "value": t,
        }
        for t in WC_TEAMS
    ]

    initial_winner = None
    initial_scorer = None
    initial_semis = [None, None, None, None]
    initial_zebra = None
    initial_goals = None
    if existing:
        initial_winner = next((o for o in team_options if o["value"] == existing["winner"]), None)
        initial_scorer = existing["top_scorer"]
        initial_semis = [existing[f"semi{i}"] for i in range(1, 5)]
        initial_zebra = existing["zebra"]
        initial_goals = existing["group_goals_guess"]

    winner_block = {
        "type": "input",
        "block_id": "block_winner",
        "label": {"type": "plain_text", "text": ":trophy: World Cup Winner"},
        "element": {
            "type": "static_select",
            "action_id": WINNER_ACTION,
            "placeholder": {"type": "plain_text", "text": "Pick the winner…"},
            "options": team_options,
        },
    }
    if initial_winner:
        winner_block["element"]["initial_option"] = initial_winner

    scorer_block = {
        "type": "input",
        "block_id": "block_scorer",
        "label": {"type": "plain_text", "text": ":athletic_shoe: Golden Boot (top scorer)"},
        "hint": {"type": "plain_text", "text": "Start typing a player's name to search all 1,249 WC squad players."},
        "element": {
            "type": "external_select",
            "action_id": SCORER_ACTION,
            "placeholder": {"type": "plain_text", "text": "Search player…"},
            "min_query_length": 2,
        },
    }
    if initial_scorer:
        scorer_block["element"]["initial_option"] = {
            "text": {"type": "plain_text", "text": initial_scorer},
            "value": initial_scorer,
        }

    semi_blocks = []
    labels = [":one:", ":two:", ":three:", ":four:"]
    for i, (action, label, initial) in enumerate(zip(SEMI_ACTIONS, labels, initial_semis)):
        block = {
            "type": "input",
            "block_id": f"block_semi{i + 1}",
            "label": {"type": "plain_text", "text": f"{label} Semi-finalist", "emoji": True},
            "element": {
                "type": "static_select",
                "action_id": action,
                "placeholder": {"type": "plain_text", "text": "Pick a team…"},
                "options": team_options,
            },
        }
        if initial:
            block["element"]["initial_option"] = next(
                (o for o in team_options if o["value"] == initial), None
            )
        semi_blocks.append(block)

    def _zebra_option(team_name: str) -> dict:
        return {
            "text": {"type": "plain_text", "text": f"{flag(team_name)} {team_name}", "emoji": True},
            "value": team_name,
        }

    zebra_block = {
        "type": "input",
        "block_id": "block_zebra",
        "optional": True,
        "label": {"type": "plain_text", "text": ":zebra_face: Zebra Pick (optional)"},
        "hint": {
            "type": "plain_text",
            "text": (
                f"Bold ({len(ZEBRA_BOLD)} teams) = standard points. "
                f"Wildcard ({len(ZEBRA_WILDCARD)} teams) = ×{ZEBRA_WILDCARD_MULTIPLIER} all points."
            ),
        },
        "element": {
            "type": "static_select",
            "action_id": ZEBRA_ACTION,
            "placeholder": {"type": "plain_text", "text": "Pick an underdog…"},
            "option_groups": [
                {
                    "label": {"type": "plain_text", "text": f"Bold Picks ({len(ZEBRA_BOLD)} teams)"},
                    "options": [_zebra_option(t) for t in sorted(ZEBRA_BOLD)],
                },
                {
                    "label": {"type": "plain_text", "text": f"Wildcard x{ZEBRA_WILDCARD_MULTIPLIER} pts ({len(ZEBRA_WILDCARD)} teams)"},
                    "options": [_zebra_option(t) for t in sorted(ZEBRA_WILDCARD)],
                },
            ],
        },
    }
    if initial_zebra:
        zebra_block["element"]["initial_option"] = _zebra_option(initial_zebra)

    goals_block = {
        "type": "input",
        "block_id": "block_group_goals",
        "optional": True,
        "label": {"type": "plain_text", "text": ":goal_net: Group Stage Total Goals (optional)"},
        "hint": {
            "type": "plain_text",
            "text": f"Guess the total goals across all 72 group matches. 1st closest wins {GROUP_GOALS_WIN_POINTS} pts, 2nd closest wins {GROUP_GOALS_NEAR_POINTS} pts.",
        },
        "element": {
            "type": "plain_text_input",
            "action_id": GOALS_ACTION,
            "placeholder": {"type": "plain_text", "text": "e.g. 156"},
            "max_length": 4,
        },
    }
    if initial_goals is not None:
        goals_block["element"]["initial_value"] = str(initial_goals)

    client.views_open(
        trigger_id=trigger_id,
        view={
            "type": "modal",
            "callback_id": CALLBACK_ID,
            "title": {"type": "plain_text", "text": "Tournament Picks", "emoji": True},
            "submit": {"type": "plain_text", "text": "Lock In"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": _SCORING_RUNDOWN},
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            ":lock: These lock when *Matchday 2 begins (18 Jun)* — "
                            "you can update any time before then."
                        ),
                    },
                },
                winner_block,
                scorer_block,
                {"type": "divider"},
                *semi_blocks,
                {"type": "divider"},
                zebra_block,
                goals_block,
            ],
        },
    )


def handle_picks_submit(ack, body, client):
    values = body["view"]["state"]["values"]
    slack_user_id = body["user"]["id"]

    winner = values["block_winner"][WINNER_ACTION]["selected_option"]["value"]
    scorer_sel = values["block_scorer"][SCORER_ACTION].get("selected_option")
    top_scorer = scorer_sel["value"].strip() if scorer_sel else None

    semis = []
    for i, action in enumerate(SEMI_ACTIONS):
        sel = values[f"block_semi{i + 1}"][action].get("selected_option")
        semis.append(sel["value"] if sel else None)

    zebra_sel = values["block_zebra"][ZEBRA_ACTION].get("selected_option")
    zebra = zebra_sel["value"] if zebra_sel else None
    zebra_tier = _zebra_tier(zebra) if zebra else None

    goals_raw = (values["block_group_goals"][GOALS_ACTION].get("value") or "").strip()
    group_goals_guess = None

    if not top_scorer:
        ack(response_action="errors", errors={"block_scorer": "Select a player from the search results."})
        return

    if goals_raw:
        if not goals_raw.isdigit():
            ack(response_action="errors", errors={"block_group_goals": "Enter a whole number."})
            return
        group_goals_guess = int(goals_raw)

    # Validate all 4 semis are distinct
    filled_semis = [s for s in semis if s]
    if filled_semis and len(filled_semis) < 4:
        ack(response_action="errors", errors={
            "block_semi1": "Pick all 4 semi-finalists or leave all blank."
        })
        return
    if len(set(filled_semis)) < len(filled_semis):
        ack(response_action="errors", errors={
            "block_semi1": "Each semi-finalist must be a different team."
        })
        return

    with db.db() as conn:
        if _picks_locked(conn):
            ack(response_action="errors", errors={
                "block_winner": "Picks are locked — Matchday 2 has already begun."
            })
            return
        db.upsert_tournament_pick(
            conn, slack_user_id, winner, top_scorer, zebra, zebra_tier,
            semis[0], semis[1], semis[2], semis[3], group_goals_guess,
        )

    ack()

    semi_line = ""
    if filled_semis:
        semi_line = "\n  :four: Semis: " + "  ·  ".join(f"*{flag(t)} {t}*" for t in filled_semis)
    zebra_line = ""
    if zebra:
        tier_label = ":black_joker: Wildcard" if zebra_tier == "WILDCARD" else "⭐ Bold"
        zebra_line = f"\n  :zebra_face: Zebra: *{flag(zebra)} {zebra}* ({tier_label})"
    goals_line = f"\n  :goal_net: Group goals guess: *{group_goals_guess}*" if group_goals_guess is not None else ""

    client.chat_postEphemeral(
        channel=slack_user_id,
        user=slack_user_id,
        text=(
            f":white_check_mark: Tournament picks saved!\n"
            f"  :trophy: Winner: *{flag(winner)} {winner}*\n"
            f"  :athletic_shoe: Golden Boot: *{top_scorer}*"
            f"{semi_line}{zebra_line}{goals_line}\n\n"
            f"You can update these any time before Matchday 2 begins on *18 Jun*."
        ),
    )


def _picks_locked(conn) -> bool:
    kickoff = db.get_first_matchday2_kickoff(conn)
    return kickoff is not None and is_kickoff_passed(kickoff)


def _zebra_tier(team_name: str) -> str:
    return "WILDCARD" if team_name in ZEBRA_WILDCARD else "BOLD"


def _build_picks_preview_blocks(all_picks: list, caller_id: str) -> list:
    from app.handlers.me import _picks_text
    others = [p for p in all_picks if p["slack_user_id"] != caller_id]

    zebra_teams = {p["zebra"] for p in others[:_EPHEMERAL_PREVIEW] if p["zebra"]}
    zebra_statuses = {}
    if zebra_teams:
        with db.db() as conn:
            for team in zebra_teams:
                zebra_statuses[team] = db.team_knocked_out(conn, team)

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "🔮 Everyone's Picks", "emoji": True}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": "_Picks are locked. Points update as the tournament progresses._"}]},
    ]
    for p in others[:_EPHEMERAL_PREVIEW]:
        z_status = zebra_statuses.get(p["zebra"]) if p["zebra"] else None
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*<@{p['slack_user_id']}>*\n{_picks_text(p, locked=True, zebra_knocked_out=z_status)}"},
        })
    if len(others) > _EPHEMERAL_PREVIEW:
        blocks.append({"type": "divider"})
        blocks.append({"type": "actions", "elements": [{
            "type": "button",
            "text": {"type": "plain_text", "text": f"See all {len(others)} picks →", "emoji": True},
            "action_id": OPEN_PICKS_MODAL_ACTION,
            "value": "open",
        }]})
    return blocks


def _build_picks_modal_view(all_picks: list, caller_id: str, page: int = 0) -> dict:
    from app.handlers.me import _picks_text
    others = [p for p in all_picks if p["slack_user_id"] != caller_id]
    total = len(others)
    total_pages = max(1, -(-total // PICKS_MODAL_PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    start = page * PICKS_MODAL_PAGE_SIZE
    page_picks = others[start:start + PICKS_MODAL_PAGE_SIZE]

    zebra_teams = {p["zebra"] for p in page_picks if p["zebra"]}
    zebra_statuses = {}
    if zebra_teams:
        with db.db() as conn:
            for team in zebra_teams:
                zebra_statuses[team] = db.team_knocked_out(conn, team)

    blocks = []
    for p in page_picks:
        z_status = zebra_statuses.get(p["zebra"]) if p["zebra"] else None
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*<@{p['slack_user_id']}>*\n{_picks_text(p, locked=True, zebra_knocked_out=z_status)}"},
        })

    nav_elements = []
    if page > 0:
        nav_elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "← Previous", "emoji": True},
            "action_id": PICKS_MODAL_PREV_ACTION,
            "value": str(page - 1),
        })
    if page < total_pages - 1:
        nav_elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "Next →", "emoji": True},
            "action_id": PICKS_MODAL_NEXT_ACTION,
            "value": str(page + 1),
        })
    if nav_elements:
        blocks.append({"type": "divider"})
        blocks.append({"type": "actions", "elements": nav_elements})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"_Page {page + 1} of {total_pages}  ·  {total} players_"}]})

    return {
        "type": "modal",
        "title": {"type": "plain_text", "text": "Everyone's Picks", "emoji": True},
        "close": {"type": "plain_text", "text": "Close"},
        "private_metadata": json.dumps({"caller_id": caller_id}),
        "blocks": blocks,
    }


def handle_open_picks_modal_action(ack, body, client):
    ack()
    caller_id = body["user"]["id"]
    with db.db() as conn:
        all_picks = db.get_all_picks_for_reveal(conn)
    view = _build_picks_modal_view(all_picks, caller_id, page=0)
    client.views_open(trigger_id=body["trigger_id"], view=view)


def handle_picks_modal_nav(ack, body, client):
    ack()
    caller_id = body["user"]["id"]
    page = int(body["actions"][0]["value"])
    with db.db() as conn:
        all_picks = db.get_all_picks_for_reveal(conn)
    view = _build_picks_modal_view(all_picks, caller_id, page=page)
    client.views_update(view_id=body["view"]["id"], view=view)
