import logging
import os
from dotenv import load_dotenv

load_dotenv()

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from app.db import init_db
from app.scheduler import start_scheduler, sync_fixtures
from app.handlers.predict import (
    open_predict_modal, handle_predict_submit, handle_date_selected,
    CALLBACK_ID, DATE_ACTION,
)
from app.handlers.enroll import handle_enroll
from app.handlers.picks import open_picks_modal, handle_picks_submit, handle_picks_page_action, CALLBACK_ID as PICKS_CALLBACK_ID, SCORER_ACTION, PICKS_PAGE_ACTION
from app.players import search as search_players
from app.handlers.leaderboard import handle_leaderboard
from app.handlers.fixtures import handle_fixtures, handle_fixtures_page, FIXTURES_PAGE_ACTION
from app.handlers.results import handle_results, handle_results_page, RESULTS_PAGE_ACTION
from app.handlers.scoring import handle_scoring
from app.handlers.me import handle_me, handle_mystats_upcoming_page, MYSTATS_UPCOMING_PAGE_ACTION

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_REQUIRED_ENV = ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "RESULTS_CHANNEL"]

def _validate_env():
    missing = [k for k in _REQUIRED_ENV if not os.getenv(k)]
    if missing:
        raise SystemExit(f"Missing required environment variables: {', '.join(missing)}")
    tz = os.getenv("DISPLAY_TIMEZONE", "Australia/Sydney")
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(tz)
    except Exception:
        raise SystemExit(f"Invalid DISPLAY_TIMEZONE: '{tz}' — use a valid tz name e.g. 'Australia/Sydney'")

_validate_env()

app = App(token=os.environ["SLACK_BOT_TOKEN"])

_HELP_TEXT = (
    "*:soccer: World Cup 2026 Bot — commands*\n\n"
    "  `/register`     — join the prediction league\n"
    "  `/picks`        — set tournament picks (winner, golden boot, semi-finalists, zebra, group goals)\n"
    "  `/predict`      — predict scores for a matchday (pick a date, fill in scores)\n"
    "  `/leaderboard`  — current standings\n"
    "  `/fixtures`     — upcoming fixtures\n"
    "  `/results`      — recent match results\n"
    "  `/scoring`      — how points are calculated\n"
    "  `/mystats`      — your personal stats and picks (or `/mystats @user` to view someone else)\n"
    "  `/help`         — show this message\n"
)


# ── Slash command: /register ──────────────────────────────────────────────────
@app.command("/register")
def cmd_enroll(ack, respond, body):
    ack()
    handle_enroll(respond, body)


# ── Slash command: /picks ──────────────────────────────────────────────────────
@app.command("/picks")
def cmd_picks(ack, body, client):
    ack()
    open_picks_modal(client, body["trigger_id"], body["user_id"])


@app.view(PICKS_CALLBACK_ID)
def view_picks(ack, body, client):
    handle_picks_submit(ack, body, client)


@app.action(PICKS_PAGE_ACTION)
def action_picks_page(ack, body, respond):
    handle_picks_page_action(ack, body, respond)


# ── External select: golden boot player search ────────────────────────────────
@app.options(SCORER_ACTION)
def options_scorer(ack, payload):
    query = payload.get("value", "")
    players = search_players(query, limit=20)
    options = [
        {
            "text": {"type": "plain_text", "text": f"{p['name']} ({p['team']} · {p['position']})"},
            "value": p["name"],
        }
        for p in players
    ]
    ack(options=options)


# ── Slash command: /predict ────────────────────────────────────────────────────
@app.command("/predict")
def cmd_predict(ack, body, client):
    ack()
    open_predict_modal(client, body["trigger_id"], body["user_id"])


@app.action(DATE_ACTION)
def action_date_selected(ack, body, client):
    handle_date_selected(ack, body, client)


@app.view(CALLBACK_ID)
def view_predict(ack, body, client):
    handle_predict_submit(ack, body, client)


# ── Slash command: /leaderboard ───────────────────────────────────────────────
@app.command("/leaderboard")
def cmd_leaderboard(ack, respond, client, body):
    ack()
    handle_leaderboard(respond, client, body)


# ── Slash command: /fixtures ──────────────────────────────────────────────────
@app.command("/fixtures")
def cmd_fixtures(ack, respond, body):
    ack()
    handle_fixtures(respond, body)


@app.action(FIXTURES_PAGE_ACTION)
def action_fixtures_page(ack, respond, body):
    handle_fixtures_page(ack, respond, body)


# ── Slash command: /results ───────────────────────────────────────────────────
@app.command("/results")
def cmd_results(ack, respond, body):
    ack()
    handle_results(respond, body)


@app.action(RESULTS_PAGE_ACTION)
def action_results_page(ack, respond, body):
    handle_results_page(ack, respond, body)


# ── Slash command: /scoring ───────────────────────────────────────────────────
@app.command("/scoring")
def cmd_scoring(ack, respond, body):
    ack()
    handle_scoring(respond, body)


# ── Slash command: /mystats ───────────────────────────────────────────────────
@app.command("/mystats")
def cmd_me(ack, respond, body, client):
    ack()
    handle_me(respond, body, client)


@app.action(MYSTATS_UPCOMING_PAGE_ACTION)
def action_mystats_upcoming_page(ack, respond, body, client):
    handle_mystats_upcoming_page(ack, respond, body, client)


# ── Slash command: /help ──────────────────────────────────────────────────────
@app.command("/help")
def cmd_help(ack, respond):
    ack()
    respond(response_type="ephemeral", text=_HELP_TEXT)


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    logger.info("Database initialised")

    logger.info("Fetching initial fixtures…")
    sync_fixtures()

    slack_client = app.client
    start_scheduler(slack_client)

    logger.info("Starting bot in Socket Mode…")
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()
