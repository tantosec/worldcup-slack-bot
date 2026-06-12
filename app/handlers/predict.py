import json
import logging
from datetime import datetime, timezone

from app import db
from app.flags import flag, home, away, vs
from app.football import is_kickoff_passed, format_kickoff, stage_label



logger = logging.getLogger(__name__)

CALLBACK_ID = "submit_predict"
DATE_ACTION = "pick_date"


def _format_date_option(date_str: str, total: int, predicted: int) -> dict:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    match_word = "match" if total == 1 else "matches"
    if predicted == 0:
        label = f"{dt.strftime('%b %d')} · {total} {match_word}"
    elif predicted == total:
        label = f"{dt.strftime('%b %d')} · {total} {match_word} ✓"
    else:
        label = f"{dt.strftime('%b %d')} · {predicted}/{total} predicted"
    return {
        "text": {"type": "plain_text", "text": label},
        "value": date_str,
    }


def _date_picker_block(options: list[dict], initial: str | None = None) -> dict:
    block = {
        "type": "input",
        "block_id": "block_date",
        "dispatch_action": True,
        "label": {"type": "plain_text", "text": ":calendar: Match day"},
        "element": {
            "type": "static_select",
            "action_id": DATE_ACTION,
            "placeholder": {"type": "plain_text", "text": "Pick a date…"},
            "options": options,
        },
    }
    if initial:
        block["element"]["initial_option"] = next(
            (o for o in options if o["value"] == initial), None
        )
    return block


def _match_blocks(matches: list[dict]) -> list[dict]:
    blocks = []
    for m in matches:
        if is_kickoff_passed(m["kickoff_utc"]):
            continue
        match_id = str(m["id"])
        pred_home = m.get("pred_home")
        pred_away = m.get("pred_away")
        has_pred = pred_home is not None and pred_away is not None

        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{vs(m['home_team'], m['away_team'])}*\n"
                    f":clock3: {format_kickoff(m['kickoff_utc'])}  ·  {stage_label(m['stage'])}"
                    + (f"  ·  _your pick: {pred_home} - {pred_away}_" if has_pred else "")
                ),
            },
        })

        home_el = {
            "type": "plain_text_input",
            "action_id": "score",
            "placeholder": {"type": "plain_text", "text": "0"},
            "max_length": 2,
        }
        away_el = {
            "type": "plain_text_input",
            "action_id": "score",
            "placeholder": {"type": "plain_text", "text": "0"},
            "max_length": 2,
        }
        if has_pred:
            home_el["initial_value"] = str(pred_home)
            away_el["initial_value"] = str(pred_away)

        blocks.append({
            "type": "input",
            "block_id": f"home_{match_id}",
            "optional": True,
            "label": {"type": "plain_text", "text": f"{flag(m['home_team'])} {m['home_team']}", "emoji": True},
            "element": home_el,
        })
        blocks.append({
            "type": "input",
            "block_id": f"away_{match_id}",
            "optional": True,
            "label": {"type": "plain_text", "text": f"{flag(m['away_team'])} {m['away_team']}", "emoji": True},
            "element": away_el,
        })
    return blocks


def open_predict_modal(client, trigger_id: str, slack_user_id: str):
    with db.db() as conn:
        if not db.is_enrolled(conn, slack_user_id):
            client.chat_postEphemeral(
                channel=slack_user_id,
                user=slack_user_id,
                text=":wave: You need to join the league first — use `/register` to sign up!",
            )
            return
        dates = db.get_predict_dates(conn, slack_user_id)

    if not dates:
        client.chat_postEphemeral(
            channel=slack_user_id,
            user=slack_user_id,
            text=":hourglass: No upcoming matches found — check back soon!",
        )
        return

    options = [_format_date_option(d, t, p) for d, t, p in dates]

    client.views_open(
        trigger_id=trigger_id,
        view={
            "type": "modal",
            "callback_id": CALLBACK_ID,
            "title": {"type": "plain_text", "text": ":soccer: Predict", "emoji": True},
            "close": {"type": "plain_text", "text": "Cancel"},
            "private_metadata": json.dumps({"user_id": slack_user_id}),
            "blocks": [_date_picker_block(options)],
        },
    )


def handle_date_selected(ack, body, client):
    ack()
    view = body["view"]
    slack_user_id = json.loads(view["private_metadata"])["user_id"]
    date_str = body["actions"][0]["selected_option"]["value"]

    with db.db() as conn:
        dates = db.get_predict_dates(conn, slack_user_id)
        matches = db.get_matches_for_date(conn, slack_user_id, date_str)

    options = [_format_date_option(d, t, p) for d, t, p in dates]
    match_blocks = _match_blocks(matches)

    if not match_blocks:
        # All matches on this date have kicked off since the modal opened
        blocks = [
            _date_picker_block(options, initial=date_str),
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": ":lock: All matches on this date have already kicked off."},
            },
        ]
        client.views_update(
            view_id=view["id"],
            hash=view["hash"],
            view={
                "type": "modal",
                "callback_id": CALLBACK_ID,
                "title": {"type": "plain_text", "text": ":soccer: Predict", "emoji": True},
                "close": {"type": "plain_text", "text": "Cancel"},
                "private_metadata": view["private_metadata"],
                "blocks": blocks,
            },
        )
        return

    client.views_update(
        view_id=view["id"],
        hash=view["hash"],
        view={
            "type": "modal",
            "callback_id": CALLBACK_ID,
            "title": {"type": "plain_text", "text": ":soccer: Predict", "emoji": True},
            "submit": {"type": "plain_text", "text": "Lock In"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "private_metadata": view["private_metadata"],
            "blocks": [
                _date_picker_block(options, initial=date_str),
                *match_blocks,
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": ":lock: You can update predictions any time before kickoff. Leave a match blank to skip it."}],
                },
            ],
        },
    )


def handle_predict_submit(ack, body, client):
    slack_user_id = body["user"]["id"]
    values = body["view"]["state"]["values"]

    match_ids = set()
    for block_id in values:
        if block_id.startswith("home_") or block_id.startswith("away_"):
            suffix = block_id.split("_", 1)[1]
            if suffix.isdigit():
                match_ids.add(suffix)

    saved = []
    errors = {}

    for match_id in sorted(match_ids, key=int):
        home_val = (values.get(f"home_{match_id}", {}).get("score", {}).get("value") or "").strip()
        away_val = (values.get(f"away_{match_id}", {}).get("score", {}).get("value") or "").strip()

        if not home_val and not away_val:
            continue

        if bool(home_val) != bool(away_val):
            block = f"home_{match_id}" if not home_val else f"away_{match_id}"
            errors[block] = "Fill in both scores or leave both blank."
            continue

        if not home_val.isdigit():
            errors[f"home_{match_id}"] = "Enter a whole number."
            continue
        if not away_val.isdigit():
            errors[f"away_{match_id}"] = "Enter a whole number."
            continue

        home_int, away_int = int(home_val), int(away_val)
        if home_int > 30 or away_int > 30:
            block = f"home_{match_id}" if home_int > 30 else f"away_{match_id}"
            errors[block] = "Score looks too high — max 30."
            continue

        saved.append((int(match_id), home_int, away_int))

    if errors:
        ack(response_action="errors", errors=errors)
        return

    ack()

    if not saved:
        client.chat_postEphemeral(
            channel=slack_user_id,
            user=slack_user_id,
            text=":shrug: No predictions submitted — all matches were left blank.",
        )
        return

    confirmed = []
    with db.db() as conn:
        for match_id, home_score, away_score in saved:
            match = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
            if not match or is_kickoff_passed(match["kickoff_utc"]):
                continue
            db.upsert_prediction(conn, slack_user_id, match_id, home_score, away_score)
            confirmed.append((dict(match), home_score, away_score))

    if not confirmed:
        client.chat_postEphemeral(
            channel=slack_user_id,
            user=slack_user_id,
            text=":lock: None of those predictions could be saved — matches may have already kicked off.",
        )
        return

    lines = [f":white_check_mark: *{len(confirmed)} prediction{'s' if len(confirmed) != 1 else ''} saved:*\n"]
    for match, home_score, away_score in confirmed:
        lines.append(
            f"  {home(match['home_team'])} *{home_score} - {away_score}* {away(match['away_team'])}"
            f"  ·  _{format_kickoff(match['kickoff_utc'])}_"
        )

    client.chat_postEphemeral(
        channel=slack_user_id,
        user=slack_user_id,
        text="\n".join(lines),
    )
