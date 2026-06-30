# World Cup Slack Prediction Bot

![WC 2026 Bot](worldcup.png)

A Slack bot for running an internal FIFA World Cup prediction league. Players predict match scores, make tournament picks, and earn points throughout the tournament. Everything is scored and announced automatically.

Competition identity (name, dates, phases, scoring constants) is driven by `app/data/config.json` — no code changes required to adapt the bot for a new edition.

## Features

- **Match predictions** — predict scores before each kickoff, editable any time until kickoff
- **Tournament picks** — winner, golden boot, semi-finalists, zebra underdog, group stage goals total
- **Auto scoring** — matches scored within one poll cycle of finishing
- **Kickoff reminders** — channel reminder ~1 hour before each match, tagging unpredicted players
- **Kickoff announcements** — all predictions revealed the moment a match kicks off, with venue and city
- **Goal notifications** — live alert within ~10 seconds of every goal, with scorer name, minute, and current standings
- **Halftime notifications** — halftime summary with scorers, possession/shots stats, and prediction standings
- **Win probabilities** — live betting odds shown in every match message (predict modal, reminders, kickoff, goals, results)
- **Underdog detection** — automatically identifies the underdog using betting odds (favourite must be ≥1.25× more likely to win) with FIFA rankings as fallback
- **Live fixtures** — `/fixtures` shows in-progress matches with current score and everyone's predictions
- **Result summaries** — full-time result posted to channel with goalscorer recap, possession and shots stats, everyone's predictions, points, and top 10 leaderboard
- **Personal DMs** — each player gets a DM with their points and rank after every match
- **Matchday wraps** — end-of-day summary with all results and top earners
- **Phase wraps** — rich announcement after each round completes (group stage, R32, R16, QF, SF, Final) with full leaderboard
- **Picks reveal** — all tournament picks posted publicly when picks lock
- **Leaderboard** — live standings available any time
- **Player stats** — full prediction history and picks visible per player via `/mystats @user`
- **Auto-picks** — LLM-generated predictions for players who forget, applied at kickoff. Match auto-picks earn 75% of points; tournament auto-picks count for full points. Uses Pollinations AI by default (no account required); Groq and Google Gemini supported as drop-in replacements. Auto-picks are labelled 🤖 everywhere they appear.

## Scoring

### Match Predictions
| Result | Points |
|--------|--------|
| Exact score | 9 pts |
| Correct result (W/D/L) | 3 pts |
| Upset bonus (predicted the underdog wins — and they did) | +2 pts |

**Knockout multipliers:** ×1.5 (R32/R16) · ×2 (QF) · ×2.5 (SF) · ×3 (3rd Place / Final)

**How is the underdog determined?**
Live betting odds are used — the favourite must be at least 1.25× more likely to win (probability ratio ≥ 1.25) for a team to be considered the underdog. When odds aren't available yet, FIFA rankings are used as a fallback (the higher-ranked team's rank number must be at least 1.25× the lower-ranked team's). A draw does **not** trigger the upset bonus — you need to predict the underdog wins outright.

### Tournament Picks _(lock time set via `picks_lock_time` in `config.json`)_
| Pick | Points |
|------|--------|
| World Cup Winner | 30 pts |
| Golden Boot (top scorer) | 30 pts |
| Semi-finalists (×4) | 15 pts each |
| Group Stage Total Goals | 25 pts (closest guess) / 10 pts (2nd closest guess) |
| Zebra Pick — Bold tier | 10–80 pts depending on how far they go |
| Zebra Pick — Wildcard tier | ×2 all zebra points |

### Auto-picks 🤖
Players who forget to predict a match or miss the tournament picks deadline are automatically covered by the LLM auto-pick system.

- **Match predictions** — generated once per match at the ~1h kickoff reminder and cached. Applied to all missing players at kickoff so the full predictions board is always complete. Each player gets a DM explaining the pick and the reasoning.
- **Tournament picks** — generated once at the ~1h tournament picks lock reminder. Applied to all players who haven't submitted when picks lock. Each player gets a DM with their full auto-generated picks.
- **Fairness** — all players who missed the same match get the identical LLM-generated pick (one LLM call per match, not per player). Nobody gets a different result by accident.
- **Points penalty** — auto-picked match predictions earn **75% of the points** a correct prediction would score (floor division, e.g. 9 pts → 6 pts). Configurable via `AUTO_PICK_POINTS_MULTIPLIER` (0.0–1.0 decimal, default `0.75`). Tournament picks auto-filled by the bot count for full points.
- **Display** — auto-picks are labelled 🤖 in the kickoff message, `/mystats`, and `/picks`.
- **Fallback** — if the LLM fails all retries, an odds-based pick is used instead (favourite wins 1–0, or 0–0 for a draw). The 🤖 label still applies.

## Commands

| Command | Description |
|---------|-------------|
| `/register` | Join the prediction league |
| `/picks` | Set tournament picks (locks at time set in `config.json`, defaults to first match kickoff) |
| `/predict` | Predict match scores — pick a date, fill in scores |
| `/leaderboard` | Current standings |
| `/fixtures` | Upcoming fixtures + live matches with all predictions during games |
| `/results` | Recent results with your points |
| `/scoring` | Full scoring rules |
| `/mystats` | Your personal stats and picks — `/mystats @user` to view someone else |
| `/help` | List all commands |

## Stack

- **Python 3.12** with [slack-bolt](https://github.com/slackapi/bolt-python) in Socket Mode
- **SQLite** with WAL mode for persistence
- **APScheduler** for background jobs (scoring, reminders, wraps, odds sync)
- **ESPN unofficial API** — no key required, near-real-time scores, goal scorers, match stats, venue info
- **The Odds API** free tier for live betting odds and win probabilities (500 req/month — synced every 6 hours)
- **Docker** + Docker Compose for deployment

## Setup

### 1. Create the Slack App

Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From a manifest** → paste `manifest.yaml`.

Then:
- **OAuth & Permissions** → **Install to Workspace** → copy the `xoxb-` bot token
- **Basic Information** → **App-Level Tokens** → create token with `connections:write` scope → copy the `xapp-` token

### 2. Set the bot icon _(optional)_

Go to [api.slack.com/apps](https://api.slack.com/apps) → your app → **Basic Information** → **Display Information** → upload `worldcup.png` as the App Icon.

### 3. Get an Odds API key

Go to [the-odds-api.com](https://the-odds-api.com) → sign up for a free account → copy your API key.

The free tier gives 500 requests/month. The bot syncs odds every 6 hours (~120 requests for the full tournament).

### 4. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
ODDS_API_KEY=your-key-here            # free at the-odds-api.com
RESULTS_CHANNEL=C0XXXXXXXXX           # Slack channel ID for announcements
DISPLAY_TIMEZONE=Australia/Sydney     # timezone for kickoff times
# Optional — defaults shown
LIVE_POLL_INTERVAL=10                 # seconds between live score syncs (default: 10)
POLL_INTERVAL=60                      # seconds between other job cycles (default: 60)
AUTO_PICK_ENABLED=true                # set to false to disable auto-picks entirely
AUTO_PICK_POINTS_MULTIPLIER=0.75      # fraction of points auto-picks earn (default: 0.75)
LLM_PROVIDER=pollinations             # pollinations (default) | groq | google
# GROQ_API_KEY=                       # required if LLM_PROVIDER=groq
# GOOGLE_AI_API_KEY=                  # required if LLM_PROVIDER=google
```

> **Picks lock time** is set via `picks_lock_time` in `app/data/config.json` (interpreted in `DISPLAY_TIMEZONE`). It defaults to the first match kickoff if omitted from config.

### 5. Generate static data files

The bot needs two static data files baked into the Docker image before the first build.

**Squad players** (used for golden boot autocomplete):
```bash
pip install requests
python scripts/build_players.py
# Options:
#   --year 2026            default: 2026
#   --league fifa.wwc      default: fifa.world (men's WC)
#   --output path/file.json
```

**FIFA rankings** (used as underdog fallback when betting odds aren't available yet):
```bash
python scripts/build_rankings.py
# Options:
#   --gender women         default: men
#   --output path/file.json
```

Both scripts write directly to `app/data/` and only need to be re-run if you're setting up for a new competition.

### 6. Invite the bot to the channel

In Slack, run `/invite @<your-bot-name>` in your results channel.

### 7. Deploy

```bash
docker compose up -d
docker compose logs -f
```

The bot initialises the database, imports all fixtures from ESPN, and starts listening on first run. The `./data/` directory is created automatically by Docker Compose on first run — this is where `worldcup.db` is persisted on the host.

## Project Structure

```
app/
├── main.py              # Slack app, command registration, entry point
├── db.py                # SQLite schema and all queries
├── scheduler.py         # APScheduler jobs (scoring, reminders, wraps, odds sync)
├── espn.py              # ESPN API client — fixtures, live scores, goal scorers, stats
├── football.py          # Score and time formatting utilities
├── odds.py              # The Odds API client, win probability calculation, underdog detection
├── scoring.py           # Points calculation logic
├── autopick.py          # LLM auto-pick logic — generates, caches, and applies picks
├── players.py           # Player search for golden boot autocomplete
├── flags.py             # Country flag emoji map
├── fifa_rankings.py     # FIFA rankings loader (underdog fallback)
├── config.py            # Competition config loader — reads app/data/config.json
├── data/
│   ├── config.json      # Competition identity, phases, scoring constants
│   ├── players.json     # Squad players (generated by scripts/build_players.py)
│   └── fifa_rankings.json  # FIFA rankings (generated by scripts/build_rankings.py)
├── llm/
│   ├── __init__.py      # get_provider() factory + startup validation
│   ├── base.py          # LLMProvider protocol
│   ├── pollinations.py  # Pollinations AI (default, no key required)
│   ├── groq.py          # Groq (set LLM_PROVIDER=groq + GROQ_API_KEY)
│   ├── google.py        # Google Gemini (set LLM_PROVIDER=google + GOOGLE_AI_API_KEY)
│   └── fallback.py      # Odds-based fallback when all LLM attempts fail
└── handlers/
    ├── predict.py       # /predict — dynamic date picker modal
    ├── picks.py         # /picks — tournament picks modal
    ├── enroll.py        # /register
    ├── leaderboard.py   # /leaderboard
    ├── fixtures.py      # /fixtures
    ├── results.py       # /results
    ├── scoring.py       # /scoring
    └── me.py            # /mystats

scripts/
├── build_players.py     # Fetch squad players from ESPN API → app/data/players.json
└── build_rankings.py    # Fetch FIFA rankings from official API → app/data/fifa_rankings.json
```

## Adapting for a New Competition

1. Edit `app/data/config.json` — update competition identity, dates, phases, and scoring constants (see **config.json Reference** below)
2. Run `python scripts/build_players.py` to regenerate the squad player list
3. Run `python scripts/build_rankings.py` to regenerate FIFA rankings
4. Rebuild the Docker image: `docker compose build --no-cache && docker compose up -d`

### 32-team vs 48-team format

The tournament bracket is fully driven by the `phases` array in `config.json`. The key difference between editions is whether there is a **Round of 32** (R32) before the Round of 16.

**48-team format (WC 2026)** — includes R32 as the first knockout round:
```json
"phases": [
  { "key": "GROUP_STAGE", ..., "next_label": "Round of 32" },
  { "key": "LAST_32", "label": "Round of 32", "multiplier": 1.5, ... },
  { "key": "LAST_16", "label": "Round of 16", "multiplier": 1.5, ... },
  ...
]
```

**32-team format (WC 2022 and earlier)** — R16 is the first knockout round. Remove the `LAST_32` phase entirely and update `GROUP_STAGE`'s `next_label`:
```json
"phases": [
  { "key": "GROUP_STAGE", ..., "next_label": "Round of 16" },
  { "key": "LAST_16", "label": "Round of 16", "multiplier": 1.5, ... },
  ...
]
```

Also remove `"LAST_32"` from `zebra_points` and `stage_display`, and set `group_stage_match_count: 48` (8 groups × 6 matches).

### Women's World Cup

Change `espn_slug` to `fifa.wwc` and use `--league fifa.wwc` when running `build_players.py`, and `--gender women` for `build_rankings.py`. The bracket (32-team or 48-team) is configured the same way as above.

## config.json Reference

`app/data/config.json` is the single place to configure everything competition-specific.

### `competition`

| Field | Description |
|-------|-------------|
| `name` | Full competition name shown in messages and modals (e.g. `"FIFA World Cup 2026"`) |
| `short_name` | Abbreviated name used in tighter contexts (e.g. `"WC 2026"`) |
| `espn_slug` | ESPN league identifier — `fifa.world` for men's WC, `fifa.wwc` for women's WC |
| `group_stage_match_count` | Total number of group stage matches — used for the group goals prediction (48 for 32-team, 72 for 48-team) |
| `tournament_start` | First match date `YYYY-MM-DD` — used to bound the fixture import range |
| `tournament_end` | Last match date `YYYY-MM-DD` |
| `picks_lock_time` | When tournament picks lock, in `DISPLAY_TIMEZONE`, format `"YYYY-MM-DD HH:MM:SS"`. Omit to lock at the first match kickoff. |

### `phases`

Each phase drives fixture/result pagination, phase wrap announcements, and the `/picks` modal date groupings. Fields per phase:

| Field | Description |
|-------|-------------|
| `key` | Internal identifier — must match one of: `GROUP_STAGE`, `LAST_32`, `LAST_16`, `QUARTER_FINALS`, `SEMI_FINALS`, `FINALS` |
| `label` | Human-readable phase name |
| `button_text` | Label on the date-picker button in `/predict` and `/fixtures` |
| `modal_title` | Modal title when viewing this phase |
| `stages` | ESPN stage codes this phase covers — usually one, but `FINALS` covers `["THIRD_PLACE", "FINAL"]` |
| `multiplier` | Points multiplier for match predictions in this phase |
| `next_label` | Name of the next phase — shown in the phase wrap announcement ("Next up: Round of 16") |
| `stage_labels` | _(optional)_ Per-stage display overrides within a multi-stage phase (used in `FINALS` to label 3rd place vs the Final) |

### `stage_display`

Maps internal stage codes to display strings used in result messages and `/fixtures`. Only knockout stages need entries (group stage has no separate display name).

### `scoring`

| Field | Description |
|-------|-------------|
| `tournament_pick_points` | Points for correctly picking the winner or golden boot |
| `semi_pick_points` | Points per correct semi-finalist pick |
| `group_goals_win_points` | Points for the closest group stage total goals guess |
| `group_goals_near_points` | Points for the second closest guess |
| `zebra_points` | Points map by stage for how far the zebra pick advances — add/remove stages to match the bracket |
| `zebra_wildcard_multiplier` | Multiplier applied to all zebra points for wildcard-tier picks |
| `underdog_ratio` | Minimum win-probability ratio for a team to be considered the favourite (default 1.25 — the other team becomes the underdog) |
| `zebra_bold` | List of ESPN team names eligible for the Bold zebra tier |
| `zebra_wildcard` | List of ESPN team names eligible for the Wildcard zebra tier |

## Deployment Notes

- Runs over Socket Mode — no public IP or open ports required, any machine with internet access works
- SQLite database is persisted via Docker volume at `./data/worldcup.db` on the host; the `./data/` directory is created automatically by Docker Compose on first run
- Static data files (`config.json`, `players.json`, `fifa_rankings.json`) are baked into the Docker image under `app/data/` — they are not in the volume mount and cannot be overwritten by the running container
- Live score sync runs every `LIVE_POLL_INTERVAL` seconds (default 10s) — ESPN has no rate limits
- Other jobs (scoring, kickoff announcements, reminders) run every `POLL_INTERVAL` seconds (default 60s)
- Odds sync runs every 6 hours regardless of `POLL_INTERVAL` to conserve API credits
- Odds are frozen at kickoff — only `SCHEDULED`/`TIMED` matches are updated
- Knockout matches with TBD teams are skipped during sync and added automatically once teams are confirmed
- Times displayed in `DISPLAY_TIMEZONE` (default: `Australia/Sydney`)
- DB schema changes are applied directly via `ALTER TABLE` on the production database — no migration logic in code
- Auto-pick LLM calls happen at the ~1h kickoff reminder (not at kickoff) — by the time the match starts the result is cached, so kickoff messages are never delayed
- With `LLM_PROVIDER=pollinations` (default) no API key is needed — the provider allows 1 queued request per IP with ~5–20s response time, which is well within the 1-hour window
- Switching LLM providers requires only changing `LLM_PROVIDER` and adding the corresponding key — no code changes needed
- Set `picks_lock_time` in `config.json` to the intended lock datetime (interpreted in `DISPLAY_TIMEZONE`); omit it to lock at the first match kickoff
