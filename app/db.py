import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "worldcup.db")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS matches (
                id              INTEGER PRIMARY KEY,
                external_id     INTEGER UNIQUE NOT NULL,
                home_team       TEXT NOT NULL,
                away_team       TEXT NOT NULL,
                kickoff_utc     TEXT NOT NULL,
                stage           TEXT NOT NULL DEFAULT 'GROUP_STAGE',
                matchday        INTEGER,
                status          TEXT NOT NULL DEFAULT 'SCHEDULED',
                home_score      INTEGER,
                away_score      INTEGER,
                scored          INTEGER NOT NULL DEFAULT 0,
                reminder_sent   INTEGER NOT NULL DEFAULT 0,
                kickoff_announced   INTEGER NOT NULL DEFAULT 0,
                notified_home_score INTEGER,
                notified_away_score INTEGER,
                winner          TEXT,
                duration        TEXT NOT NULL DEFAULT 'REGULAR',
                et_home         INTEGER,
                et_away         INTEGER,
                penalties_home  INTEGER,
                penalties_away  INTEGER,
                home_odds       REAL,
                draw_odds       REAL,
                away_odds       REAL
            );

            CREATE TABLE IF NOT EXISTS odds_sync (
                id            INTEGER PRIMARY KEY CHECK (id = 1),
                last_synced_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS daily_wrap_sent (
                match_date TEXT PRIMARY KEY
            );

            CREATE TABLE IF NOT EXISTS phase_wrap_sent (
                stage TEXT PRIMARY KEY
            );

            CREATE TABLE IF NOT EXISTS picks_reveal_sent (
                id      INTEGER PRIMARY KEY CHECK (id = 1),
                sent_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS predictions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                slack_user_id TEXT NOT NULL,
                match_id      INTEGER NOT NULL REFERENCES matches(id),
                home_score    INTEGER NOT NULL,
                away_score    INTEGER NOT NULL,
                points        INTEGER,
                created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(slack_user_id, match_id)
            );

            CREATE TABLE IF NOT EXISTS users (
                slack_user_id TEXT PRIMARY KEY,
                enrolled_at   TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS tournament_picks (
                slack_user_id      TEXT PRIMARY KEY,
                winner             TEXT,
                top_scorer         TEXT,
                zebra              TEXT,
                zebra_tier         TEXT,
                semi1              TEXT,
                semi2              TEXT,
                semi3              TEXT,
                semi4              TEXT,
                group_goals_guess  INTEGER,
                winner_points      INTEGER,
                scorer_points      INTEGER,
                zebra_points       INTEGER,
                semi_points        INTEGER,
                group_goals_points INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_predictions_match ON predictions(match_id);
            CREATE INDEX IF NOT EXISTS idx_predictions_user  ON predictions(slack_user_id);
        """)
        # Migrations for new columns — safe to run on existing DB
        for col, definition in [
            ("halftime_notified",      "INTEGER NOT NULL DEFAULT 0"),
            ("second_half_notified",   "INTEGER NOT NULL DEFAULT 0"),
            ("extra_time_notified",    "INTEGER NOT NULL DEFAULT 0"),
            ("shootout_notified",      "INTEGER NOT NULL DEFAULT 0"),
            ("venue_name",             "TEXT"),
            ("venue_city",             "TEXT"),
            ("home_score_90",          "INTEGER"),
            ("away_score_90",          "INTEGER"),
        ]:
            try:
                conn.execute(f"ALTER TABLE matches ADD COLUMN {col} {definition}")
            except Exception:
                pass  # Column already exists


# ── User / enrolment queries ───────────────────────────────────────────────────

def enroll_user(conn: sqlite3.Connection, slack_user_id: str) -> bool:
    """Insert user. Returns True if newly enrolled, False if already enrolled."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO users (slack_user_id) VALUES (?)", (slack_user_id,)
    )
    return cur.rowcount == 1


def is_enrolled(conn: sqlite3.Connection, slack_user_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM users WHERE slack_user_id = ?", (slack_user_id,)
    ).fetchone() is not None


def get_enrolled_users(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM users ORDER BY enrolled_at ASC"
    ).fetchall()


# ── Match queries ──────────────────────────────────────────────────────────────

def upsert_match_espn(conn: sqlite3.Connection, m: dict):
    """Upsert a match from ESPN.

    Priority order:
    1. Match by external_id — updates ALL fields including team names so ESPN
       placeholder names (e.g. 'Group J 2nd Place') get replaced with real ones.
    2. Match by (home_team, away_team, kickoff_date) — sets external_id on rows
       that were inserted before ESPN assigned an id.
    3. Insert as new row.
    """
    kickoff_date = m["kickoff_utc"][:10]

    # Step 1: update by external_id — covers team name changes
    rows = conn.execute("""
        UPDATE matches SET
            home_team      = :home_team,
            away_team      = :away_team,
            kickoff_utc    = :kickoff_utc,
            stage          = :stage,
            status         = :status,
            home_score     = :home_score,
            away_score     = :away_score,
            winner         = :winner,
            duration       = :duration,
            matchday       = COALESCE(:matchday, matchday),
            venue_name     = COALESCE(:venue_name, venue_name),
            venue_city     = COALESCE(:venue_city, venue_city)
        WHERE external_id = :external_id
    """, m)

    if rows.rowcount > 0:
        return

    # Step 2: update by team names — sets external_id on pre-existing rows
    rows = conn.execute("""
        UPDATE matches SET
            external_id    = :external_id,
            status         = :status,
            home_score     = :home_score,
            away_score     = :away_score,
            winner         = :winner,
            duration       = :duration,
            matchday       = COALESCE(:matchday, matchday),
            venue_name     = COALESCE(:venue_name, venue_name),
            venue_city     = COALESCE(:venue_city, venue_city)
        WHERE home_team = :home_team AND away_team = :away_team
          AND substr(kickoff_utc, 1, 10) = :kickoff_date
    """, {**m, "kickoff_date": kickoff_date})

    if rows.rowcount > 0:
        return

    # Step 3: truly new match
    conn.execute("""
        INSERT OR IGNORE INTO matches (
            external_id, home_team, away_team, kickoff_utc, stage, matchday,
            status, home_score, away_score, winner, duration,
            et_home, et_away, penalties_home, penalties_away,
            venue_name, venue_city
        ) VALUES (
            :external_id, :home_team, :away_team, :kickoff_utc, :stage, :matchday,
            :status, :home_score, :away_score, :winner, :duration,
            :et_home, :et_away, :penalties_home, :penalties_away,
            :venue_name, :venue_city
        )
    """, m)


def upsert_match(conn: sqlite3.Connection, m: dict):
    conn.execute("""
        INSERT INTO matches (
            external_id, home_team, away_team, kickoff_utc, stage, matchday,
            status, home_score, away_score, winner, duration,
            et_home, et_away, penalties_home, penalties_away
        )
        VALUES (
            :external_id, :home_team, :away_team, :kickoff_utc, :stage, :matchday,
            :status, :home_score, :away_score, :winner, :duration,
            :et_home, :et_away, :penalties_home, :penalties_away
        )
        ON CONFLICT(external_id) DO UPDATE SET
            status         = excluded.status,
            home_score     = excluded.home_score,
            away_score     = excluded.away_score,
            kickoff_utc    = excluded.kickoff_utc,
            stage          = excluded.stage,
            matchday       = excluded.matchday,
            winner         = excluded.winner,
            duration       = excluded.duration,
            et_home        = excluded.et_home,
            et_away        = excluded.et_away,
            penalties_home = excluded.penalties_home,
            penalties_away = excluded.penalties_away
    """, m)


def get_upcoming_matches(conn: sqlite3.Connection, limit: int = 10) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT * FROM matches
        WHERE status IN ('SCHEDULED', 'TIMED')
        ORDER BY kickoff_utc ASC
        LIMIT ?
    """, (limit,)).fetchall()


def get_all_upcoming_matches(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT * FROM matches
        WHERE status IN ('SCHEDULED', 'TIMED')
        ORDER BY kickoff_utc ASC
    """).fetchall()


def get_all_finished_matches(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT * FROM matches
        WHERE status = 'FINISHED'
        ORDER BY kickoff_utc DESC
    """).fetchall()


def get_live_matches(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT * FROM matches
        WHERE status IN ('IN_PLAY', 'PAUSED', 'HALFTIME')
        ORDER BY kickoff_utc ASC
    """).fetchall()


def get_match_predictions_all_users(conn: sqlite3.Connection, match_id: int) -> list[sqlite3.Row]:
    """Return all enrolled users with their prediction for a match (NULL scores if no prediction)."""
    return conn.execute("""
        SELECT u.slack_user_id, p.home_score, p.away_score
        FROM users u
        LEFT JOIN predictions p ON p.match_id = ? AND p.slack_user_id = u.slack_user_id
        ORDER BY u.enrolled_at ASC
    """, (match_id,)).fetchall()


def get_matches_with_score_change(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """IN_PLAY matches whose score has changed since we last posted a goal notification."""
    return conn.execute("""
        SELECT * FROM matches
        WHERE status IN ('IN_PLAY', 'PAUSED', 'HALFTIME')
          AND home_score IS NOT NULL AND away_score IS NOT NULL
          AND (
            notified_home_score IS NULL OR notified_away_score IS NULL
            OR home_score != notified_home_score
            OR away_score != notified_away_score
          )
        ORDER BY kickoff_utc ASC
    """).fetchall()


def mark_score_notified(conn: sqlite3.Connection, match_id: int, home_score: int, away_score: int):
    conn.execute(
        "UPDATE matches SET notified_home_score = ?, notified_away_score = ? WHERE id = ?",
        (home_score, away_score, match_id),
    )


def get_finished_unscored_matches(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT * FROM matches
        WHERE status = 'FINISHED' AND scored = 0
          AND home_score IS NOT NULL AND away_score IS NOT NULL
    """).fetchall()


def mark_match_scored(conn: sqlite3.Connection, match_id: int):
    conn.execute("UPDATE matches SET scored = 1 WHERE id = ?", (match_id,))


def get_recent_matches(conn: sqlite3.Connection, limit: int = 5) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT * FROM matches
        WHERE status = 'FINISHED'
        ORDER BY kickoff_utc DESC
        LIMIT ?
    """, (limit,)).fetchall()


def get_match_by_external(conn: sqlite3.Connection, external_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM matches WHERE external_id = ?", (external_id,)
    ).fetchone()


# ── Prediction queries ─────────────────────────────────────────────────────────

def insert_prediction(conn: sqlite3.Connection, slack_user_id: str, match_id: int,
                      home_score: int, away_score: int) -> bool:
    """Insert a prediction. Returns True if inserted, False if one already exists."""
    cur = conn.execute("""
        INSERT OR IGNORE INTO predictions (slack_user_id, match_id, home_score, away_score)
        VALUES (?, ?, ?, ?)
    """, (slack_user_id, match_id, home_score, away_score))
    return cur.rowcount == 1


def upsert_prediction(conn: sqlite3.Connection, slack_user_id: str, match_id: int,
                      home_score: int, away_score: int):
    """Insert or update a prediction. Never overwrites points (set by scoring job)."""
    conn.execute("""
        INSERT INTO predictions (slack_user_id, match_id, home_score, away_score)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(slack_user_id, match_id) DO UPDATE SET
          home_score = excluded.home_score,
          away_score = excluded.away_score
    """, (slack_user_id, match_id, home_score, away_score))


def get_predictions_for_match(conn: sqlite3.Connection, match_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM predictions WHERE match_id = ?", (match_id,)
    ).fetchall()


def update_prediction_points(conn: sqlite3.Connection, prediction_id: int, points: int):
    conn.execute("UPDATE predictions SET points = ? WHERE id = ?", (points, prediction_id))


def get_user_prediction(conn: sqlite3.Connection, slack_user_id: str,
                        match_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM predictions WHERE slack_user_id = ? AND match_id = ?",
        (slack_user_id, match_id),
    ).fetchone()


def get_leaderboard(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT
            u.slack_user_id,
            COUNT(p.id)                                          AS total_predictions,
            SUM(CASE WHEN p.points IS NOT NULL THEN 1 ELSE 0 END) AS scored_predictions,
            COALESCE(SUM(p.points), 0)
                + COALESCE(tp.winner_points, 0)
                + COALESCE(tp.scorer_points, 0)
                + COALESCE(tp.zebra_points, 0)
                + COALESCE(tp.semi_points, 0)
                + COALESCE(tp.group_goals_points, 0)            AS total_points,
            SUM(CASE WHEN p.points >= 9  THEN 1 ELSE 0 END)   AS exact_scores,
            SUM(CASE WHEN p.points IN (5, 8, 10, 11, 12, 15, 16, 22, 28, 33) THEN 1 ELSE 0 END) AS upsets_called,
            COALESCE(tp.winner_points, 0)      AS winner_points,
            COALESCE(tp.scorer_points, 0)      AS scorer_points,
            tp.zebra_points                    AS zebra_points,
            COALESCE(tp.semi_points, 0)        AS semi_points,
            COALESCE(tp.group_goals_points, 0) AS group_goals_points,
            tp.winner   AS picked_winner,
            tp.zebra    AS picked_zebra,
            tp.semi1, tp.semi2, tp.semi3, tp.semi4
        FROM users u
        LEFT JOIN predictions p      ON p.slack_user_id = u.slack_user_id
        LEFT JOIN tournament_picks tp ON tp.slack_user_id = u.slack_user_id
        GROUP BY u.slack_user_id
        ORDER BY total_points DESC, exact_scores DESC
    """).fetchall()


def get_leaderboard_with_breakdown(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return get_leaderboard(conn)


# ── Tournament pick queries ────────────────────────────────────────────────────

def upsert_tournament_pick(
    conn: sqlite3.Connection,
    slack_user_id: str,
    winner: str,
    top_scorer: str,
    zebra: str,
    zebra_tier: str,
    semi1: str | None = None,
    semi2: str | None = None,
    semi3: str | None = None,
    semi4: str | None = None,
    group_goals_guess: int | None = None,
):
    conn.execute("""
        INSERT INTO tournament_picks
            (slack_user_id, winner, top_scorer, zebra, zebra_tier,
             semi1, semi2, semi3, semi4, group_goals_guess)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(slack_user_id) DO UPDATE SET
            winner            = excluded.winner,
            top_scorer        = excluded.top_scorer,
            zebra             = excluded.zebra,
            zebra_tier        = excluded.zebra_tier,
            semi1             = excluded.semi1,
            semi2             = excluded.semi2,
            semi3             = excluded.semi3,
            semi4             = excluded.semi4,
            group_goals_guess = excluded.group_goals_guess
    """, (slack_user_id, winner, top_scorer, zebra, zebra_tier,
          semi1, semi2, semi3, semi4, group_goals_guess))


def get_tournament_pick(conn: sqlite3.Connection,
                        slack_user_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM tournament_picks WHERE slack_user_id = ?", (slack_user_id,)
    ).fetchone()


def get_all_tournament_picks(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM tournament_picks").fetchall()


def update_winner_points(conn: sqlite3.Connection, slack_user_id: str, points: int):
    conn.execute(
        "UPDATE tournament_picks SET winner_points = ? WHERE slack_user_id = ?",
        (points, slack_user_id),
    )


def update_scorer_points(conn: sqlite3.Connection, slack_user_id: str, points: int):
    conn.execute(
        "UPDATE tournament_picks SET scorer_points = ? WHERE slack_user_id = ?",
        (points, slack_user_id),
    )


def update_zebra_points(conn: sqlite3.Connection, slack_user_id: str, points: int):
    conn.execute(
        "UPDATE tournament_picks SET zebra_points = ? WHERE slack_user_id = ?",
        (points, slack_user_id),
    )


def update_semi_points(conn: sqlite3.Connection, slack_user_id: str, points: int):
    conn.execute(
        "UPDATE tournament_picks SET semi_points = ? WHERE slack_user_id = ?",
        (points, slack_user_id),
    )


def update_group_goals_points(conn: sqlite3.Connection, slack_user_id: str, points: int):
    conn.execute(
        "UPDATE tournament_picks SET group_goals_points = ? WHERE slack_user_id = ?",
        (points, slack_user_id),
    )


def count_finished_group_matches(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM matches WHERE stage = 'GROUP_STAGE' AND status = 'FINISHED'"
    ).fetchone()[0]


def sum_group_goals(conn: sqlite3.Connection) -> int:
    row = conn.execute("""
        SELECT COALESCE(SUM(
            home_score + away_score
            + COALESCE(et_home, 0) + COALESCE(et_away, 0)
        ), 0)
        FROM matches
        WHERE stage = 'GROUP_STAGE' AND status = 'FINISHED'
          AND home_score IS NOT NULL AND away_score IS NOT NULL
    """).fetchone()
    return row[0]


def get_confirmed_semi_teams(conn: sqlite3.Connection) -> list[str]:
    """Return the home/away teams from SEMI_FINALS fixtures once confirmed."""
    rows = conn.execute("""
        SELECT home_team, away_team FROM matches WHERE stage = 'SEMI_FINALS'
    """).fetchall()
    teams = []
    for r in rows:
        teams.append(r["home_team"])
        teams.append(r["away_team"])
    return teams


def winner_picks_already_scored(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM tournament_picks WHERE winner_points IS NOT NULL"
    ).fetchone()
    return row[0] > 0


def scorer_picks_already_scored(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM tournament_picks WHERE scorer_points IS NOT NULL"
    ).fetchone()
    return row[0] > 0


def get_tournament_winner(conn: sqlite3.Connection) -> str | None:
    """Return the winning team from the FINAL match once played."""
    row = conn.execute("""
        SELECT home_team, away_team, winner FROM matches
        WHERE stage = 'FINAL' AND status = 'FINISHED' AND winner IS NOT NULL
        LIMIT 1
    """).fetchone()
    if not row:
        return None
    if row["winner"] == "HOME_TEAM":
        return row["home_team"]
    if row["winner"] == "AWAY_TEAM":
        return row["away_team"]
    return None


def get_team_knockout_stages(conn: sqlite3.Connection, team_name: str) -> list[sqlite3.Row]:
    """All finished knockout matches a team appeared in."""
    return conn.execute("""
        SELECT stage, home_team, away_team, winner FROM matches
        WHERE (home_team = ? OR away_team = ?)
          AND stage NOT IN ('GROUP_STAGE')
          AND status = 'FINISHED'
    """, (team_name, team_name)).fetchall()


def team_has_last32_fixture(conn: sqlite3.Connection, team_name: str) -> bool:
    """True if the team has a confirmed LAST_32 fixture (any status)."""
    row = conn.execute(
        "SELECT COUNT(*) FROM matches WHERE stage = 'LAST_32' AND (home_team = ? OR away_team = ?)",
        (team_name, team_name),
    ).fetchone()
    return row[0] > 0


def team_knocked_out(conn: sqlite3.Connection, team_name: str) -> bool:
    """True if team has a finished knockout match where they didn't win (i.e. eliminated)."""
    row = conn.execute(
        """SELECT COUNT(*) FROM matches
           WHERE (home_team = ? OR away_team = ?)
             AND stage != 'GROUP_STAGE'
             AND status = 'FINISHED'
             AND winner != ?""",
        (team_name, team_name, team_name),
    ).fetchone()
    return row[0] > 0


def semi_picks_already_scored(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM tournament_picks WHERE semi_points IS NOT NULL"
    ).fetchone()
    return row[0] > 0


def group_goals_already_scored(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM tournament_picks WHERE group_goals_points IS NOT NULL"
    ).fetchone()
    return row[0] > 0


def get_first_match_kickoff(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT kickoff_utc FROM matches ORDER BY kickoff_utc ASC LIMIT 1"
    ).fetchone()
    return row["kickoff_utc"] if row else None


def get_first_knockout_kickoff(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("""
        SELECT kickoff_utc FROM matches
        WHERE stage != 'GROUP_STAGE'
        ORDER BY kickoff_utc ASC LIMIT 1
    """).fetchone()
    return row["kickoff_utc"] if row else None


def get_first_matchday2_kickoff(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("""
        SELECT kickoff_utc FROM matches
        WHERE stage = 'GROUP_STAGE' AND matchday = 2
        ORDER BY kickoff_utc ASC LIMIT 1
    """).fetchone()
    return row["kickoff_utc"] if row else None


# ── Kickoff announcement queries ──────────────────────────────────────────────

def get_matches_needing_kickoff_announcement(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Matches that are now live but haven't had their predictions revealed yet."""
    return conn.execute("""
        SELECT * FROM matches
        WHERE status IN ('IN_PLAY', 'PAUSED', 'HALFTIME')
          AND kickoff_announced = 0
        ORDER BY kickoff_utc ASC
    """).fetchall()


def mark_kickoff_announced(conn: sqlite3.Connection, match_id: int):
    conn.execute("UPDATE matches SET kickoff_announced = 1 WHERE id = ?", (match_id,))


def get_matches_needing_halftime_notification(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT * FROM matches
        WHERE status = 'HALFTIME' AND halftime_notified = 0
        ORDER BY kickoff_utc ASC
    """).fetchall()


def mark_halftime_notified(conn: sqlite3.Connection, match_id: int):
    conn.execute("UPDATE matches SET halftime_notified = 1 WHERE id = ?", (match_id,))


def get_matches_needing_second_half_notification(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT * FROM matches
        WHERE status = 'IN_PLAY' AND halftime_notified = 1 AND second_half_notified = 0
        ORDER BY kickoff_utc ASC
    """).fetchall()


def mark_second_half_notified(conn: sqlite3.Connection, match_id: int):
    conn.execute("UPDATE matches SET second_half_notified = 1 WHERE id = ?", (match_id,))


def get_matches_needing_extra_time_notification(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT * FROM matches
        WHERE status = 'IN_PLAY' AND duration = 'EXTRA_TIME' AND extra_time_notified = 0
        ORDER BY kickoff_utc ASC
    """).fetchall()


def mark_extra_time_notified(conn: sqlite3.Connection, match_id: int):
    conn.execute("UPDATE matches SET extra_time_notified = 1 WHERE id = ?", (match_id,))


def get_matches_needing_shootout_notification(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT * FROM matches
        WHERE status = 'IN_PLAY' AND duration = 'PENALTY_SHOOTOUT' AND shootout_notified = 0
        ORDER BY kickoff_utc ASC
    """).fetchall()


def mark_shootout_notified(conn: sqlite3.Connection, match_id: int):
    conn.execute("UPDATE matches SET shootout_notified = 1 WHERE id = ?", (match_id,))


# ── Kickoff reminder queries ───────────────────────────────────────────────────

def get_matches_needing_reminder(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Matches kicking off in 50–70 min that haven't had a reminder sent yet."""
    return conn.execute("""
        SELECT * FROM matches
        WHERE status IN ('SCHEDULED', 'TIMED')
          AND reminder_sent = 0
          AND datetime(kickoff_utc) BETWEEN datetime('now', '+50 minutes')
                                      AND datetime('now', '+70 minutes')
        ORDER BY kickoff_utc ASC
    """).fetchall()


def mark_reminder_sent(conn: sqlite3.Connection, match_id: int):
    conn.execute("UPDATE matches SET reminder_sent = 1 WHERE id = ?", (match_id,))


def get_unpredicted_enrolled_users(conn: sqlite3.Connection, match_id: int) -> list[str]:
    """Enrolled users who have not submitted a prediction for this match."""
    rows = conn.execute("""
        SELECT u.slack_user_id FROM users u
        WHERE NOT EXISTS (
            SELECT 1 FROM predictions p
            WHERE p.slack_user_id = u.slack_user_id AND p.match_id = ?
        )
    """, (match_id,)).fetchall()
    return [r["slack_user_id"] for r in rows]


# ── Matchday wrap queries ──────────────────────────────────────────────────────

def get_dates_needing_wrap(conn: sqlite3.Connection) -> list[str]:
    """Dates where every match has been scored but no wrap has been posted yet."""
    rows = conn.execute("""
        SELECT date(kickoff_utc) AS match_date
        FROM matches
        GROUP BY date(kickoff_utc)
        HAVING COUNT(*) = SUM(scored)
           AND MAX(datetime(kickoff_utc)) < datetime('now')
           AND date(kickoff_utc) NOT IN (SELECT match_date FROM daily_wrap_sent)
        ORDER BY match_date ASC
    """).fetchall()
    return [r["match_date"] for r in rows]


def mark_wrap_sent(conn: sqlite3.Connection, match_date: str):
    conn.execute(
        "INSERT OR IGNORE INTO daily_wrap_sent (match_date) VALUES (?)", (match_date,)
    )


def get_matches_on_date(conn: sqlite3.Connection, match_date: str) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT * FROM matches
        WHERE date(kickoff_utc) = ?
        ORDER BY kickoff_utc ASC
    """, (match_date,)).fetchall()


def get_day_top_earners(conn: sqlite3.Connection, match_date: str, limit: int = 3) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT p.slack_user_id, SUM(p.points) AS day_pts
        FROM predictions p
        JOIN matches m ON m.id = p.match_id
        WHERE date(m.kickoff_utc) = ? AND p.points IS NOT NULL
        GROUP BY p.slack_user_id
        ORDER BY day_pts DESC
        LIMIT ?
    """, (match_date, limit)).fetchall()


# ── Phase wrap queries ─────────────────────────────────────────────────────────

def get_stages_needing_phase_wrap(conn: sqlite3.Connection) -> list[str]:
    """Stages where every match is scored but no phase wrap has been posted."""
    rows = conn.execute("""
        SELECT stage
        FROM matches
        GROUP BY stage
        HAVING COUNT(*) = SUM(scored)
           AND MAX(datetime(kickoff_utc)) < datetime('now')
           AND stage NOT IN (SELECT stage FROM phase_wrap_sent)
        ORDER BY MIN(kickoff_utc) ASC
    """).fetchall()
    return [r["stage"] for r in rows]


def mark_phase_wrap_sent(conn: sqlite3.Connection, stage: str):
    conn.execute(
        "INSERT OR IGNORE INTO phase_wrap_sent (stage) VALUES (?)", (stage,)
    )


def get_matches_by_stage(conn: sqlite3.Connection, stage: str) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT * FROM matches WHERE stage = ?
        ORDER BY kickoff_utc ASC
    """, (stage,)).fetchall()


def get_stage_stats(conn: sqlite3.Connection, stage: str) -> dict:
    row = conn.execute("""
        SELECT
            COUNT(*)                                    AS match_count,
            SUM(home_score + away_score)                AS total_goals,
            MAX(home_score + away_score)                AS max_goals_match,
            SUM(CASE WHEN home_score = away_score AND home_score IS NOT NULL THEN 1 ELSE 0 END) AS draws
        FROM matches WHERE stage = ?
    """, (stage,)).fetchone()
    return dict(row)


def get_upcoming_stages(conn: sqlite3.Connection) -> list[str]:
    """Stages that have at least one SCHEDULED/TIMED match."""
    rows = conn.execute("""
        SELECT stage FROM matches
        WHERE status IN ('SCHEDULED', 'TIMED')
        GROUP BY stage
        ORDER BY MIN(kickoff_utc) ASC
    """).fetchall()
    return [r["stage"] for r in rows]


# ── Leaderboard rank helper ────────────────────────────────────────────────────

def get_user_rank_and_total(conn: sqlite3.Connection, slack_user_id: str) -> tuple[int, int]:
    """Returns (rank, total_points) for a user. Rank is 1-based."""
    rows = conn.execute("""
        SELECT u.slack_user_id,
               COALESCE(SUM(p.points), 0)
                   + COALESCE(tp.winner_points, 0)
                   + COALESCE(tp.scorer_points, 0)
                   + COALESCE(tp.zebra_points, 0)
                   + COALESCE(tp.semi_points, 0)
                   + COALESCE(tp.group_goals_points, 0) AS total_points
        FROM users u
        LEFT JOIN predictions p       ON p.slack_user_id = u.slack_user_id
        LEFT JOIN tournament_picks tp ON tp.slack_user_id = u.slack_user_id
        GROUP BY u.slack_user_id
        ORDER BY total_points DESC
    """).fetchall()
    for rank, row in enumerate(rows, start=1):
        if row["slack_user_id"] == slack_user_id:
            return rank, row["total_points"]
    return 0, 0


# ── /me personal stats ────────────────────────────────────────────────────────

def get_user_match_stats(conn: sqlite3.Connection, slack_user_id: str) -> sqlite3.Row:
    return conn.execute("""
        SELECT
            COUNT(p.id)                                                    AS total_predictions,
            SUM(CASE WHEN p.points IS NOT NULL THEN 1 ELSE 0 END)          AS scored,
            SUM(CASE WHEN p.home_score = m.home_score
                      AND p.away_score = m.away_score
                      AND m.home_score IS NOT NULL THEN 1 ELSE 0 END)      AS exact_scores,
            SUM(CASE WHEN p.points > 0
                      AND NOT (p.home_score = m.home_score AND p.away_score = m.away_score)
                      THEN 1 ELSE 0 END)                                   AS correct_results,
            SUM(CASE WHEN p.points = 0 AND p.points IS NOT NULL THEN 1 ELSE 0 END) AS wrong,
            COALESCE(SUM(CASE WHEN p.points IS NOT NULL THEN p.points ELSE 0 END), 0) AS match_points
        FROM predictions p
        JOIN matches m ON m.id = p.match_id
        WHERE p.slack_user_id = ?
    """, (slack_user_id,)).fetchone()


# ── Picks reveal ───────────────────────────────────────────────────────────────

def picks_reveal_already_sent(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT COUNT(*) FROM picks_reveal_sent").fetchone()[0] > 0


def mark_picks_reveal_sent(conn: sqlite3.Connection):
    conn.execute("INSERT OR IGNORE INTO picks_reveal_sent (id) VALUES (1)")


def get_all_picks_for_reveal(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT tp.* FROM tournament_picks tp
        JOIN users u ON u.slack_user_id = tp.slack_user_id
        ORDER BY u.enrolled_at ASC
    """).fetchall()


def get_predict_dates(conn: sqlite3.Connection, slack_user_id: str) -> list[tuple[str, int, int]]:
    """Return (date_str, total_count, predicted_count) for dates with upcoming matches."""
    rows = conn.execute("""
        SELECT
            substr(kickoff_utc, 1, 10) AS match_date,
            count(*) AS total,
            count(p.id) AS predicted
        FROM matches m
        LEFT JOIN predictions p ON p.match_id = m.id AND p.slack_user_id = ?
        WHERE m.status IN ('SCHEDULED', 'TIMED')
        GROUP BY match_date
        ORDER BY match_date ASC
    """, (slack_user_id,)).fetchall()
    return [(r["match_date"], r["total"], r["predicted"]) for r in rows]


def get_matches_for_date(conn: sqlite3.Connection, slack_user_id: str, date_str: str) -> list[dict]:
    """Return all upcoming matches on a given UTC date with any existing prediction scores."""
    rows = conn.execute("""
        SELECT m.*, p.home_score AS pred_home, p.away_score AS pred_away
        FROM matches m
        LEFT JOIN predictions p ON p.match_id = m.id AND p.slack_user_id = ?
        WHERE m.status IN ('SCHEDULED', 'TIMED')
          AND substr(m.kickoff_utc, 1, 10) = ?
        ORDER BY m.kickoff_utc ASC
    """, (slack_user_id, date_str)).fetchall()
    return [dict(r) for r in rows]


def get_user_predictions_with_matches(conn: sqlite3.Connection,
                                      slack_user_id: str) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT p.*, m.home_team, m.away_team, m.kickoff_utc, m.status,
               m.home_score AS match_home, m.away_score AS match_away
        FROM predictions p
        JOIN matches m ON m.id = p.match_id
        WHERE p.slack_user_id = ?
        ORDER BY m.kickoff_utc ASC
    """, (slack_user_id,)).fetchall()


def get_user_finished_predictions(conn: sqlite3.Connection, slack_user_id: str) -> list[sqlite3.Row]:
    """Return all finished matches where the user made a prediction, ordered by kickoff."""
    return conn.execute("""
        SELECT m.home_team, m.away_team, m.kickoff_utc, m.stage,
               m.home_score, m.away_score,
               COALESCE(m.home_score_90, m.home_score) AS act_home,
               COALESCE(m.away_score_90, m.away_score) AS act_away,
               m.duration, m.penalties_home, m.penalties_away,
               m.et_home, m.et_away,
               m.home_odds, m.draw_odds, m.away_odds,
               p.home_score AS pred_home, p.away_score AS pred_away, p.points
        FROM matches m
        JOIN predictions p ON p.match_id = m.id AND p.slack_user_id = ?
        WHERE m.status = 'FINISHED'
        ORDER BY m.kickoff_utc ASC
    """, (slack_user_id,)).fetchall()


def get_user_upcoming_predictions(conn: sqlite3.Connection, slack_user_id: str) -> list[sqlite3.Row]:
    """Return upcoming matches where the user has already submitted a prediction."""
    return conn.execute("""
        SELECT m.home_team, m.away_team, m.kickoff_utc, m.stage,
               m.home_odds, m.draw_odds, m.away_odds,
               m.venue_name, m.venue_city,
               p.home_score AS pred_home, p.away_score AS pred_away
        FROM matches m
        JOIN predictions p ON p.match_id = m.id AND p.slack_user_id = ?
        WHERE m.status IN ('SCHEDULED', 'TIMED')
        ORDER BY m.kickoff_utc ASC
    """, (slack_user_id,)).fetchall()


def get_user_prediction_gaps(conn: sqlite3.Connection, slack_user_id: str) -> tuple[int, int]:
    """Return (missed, still_to_predict) for a user.

    missed = finished matches with no prediction submitted.
    still_to_predict = upcoming scheduled matches with no prediction submitted.
    """
    row = conn.execute("""
        SELECT
            SUM(CASE WHEN m.status = 'FINISHED'
                      AND p.id IS NULL THEN 1 ELSE 0 END) AS missed,
            SUM(CASE WHEN m.status IN ('SCHEDULED', 'TIMED')
                      AND p.id IS NULL THEN 1 ELSE 0 END) AS still_to_predict
        FROM matches m
        LEFT JOIN predictions p ON p.match_id = m.id AND p.slack_user_id = ?
    """, (slack_user_id,)).fetchone()
    return (row["missed"] or 0, row["still_to_predict"] or 0)


def get_last32_fixture_count(conn: sqlite3.Connection) -> int:
    """Count confirmed LAST_32 fixtures in DB (0–16). Used to distinguish 'pending' from 'eliminated'."""
    return conn.execute(
        "SELECT COUNT(*) FROM matches WHERE stage = 'LAST_32'"
    ).fetchone()[0]


def update_match_penalties(conn: sqlite3.Connection, match_id: int, pen_home: int, pen_away: int):
    conn.execute(
        "UPDATE matches SET penalties_home = ?, penalties_away = ? WHERE id = ?",
        (pen_home, pen_away, match_id),
    )


def update_match_90min_scores(conn: sqlite3.Connection, match_id: int, home_90: int, away_90: int):
    conn.execute(
        "UPDATE matches SET home_score_90 = ?, away_score_90 = ? WHERE id = ?",
        (home_90, away_90, match_id),
    )


def get_last_odds_sync(conn) -> str | None:
    row = conn.execute("SELECT last_synced_at FROM odds_sync WHERE id = 1").fetchone()
    return row["last_synced_at"] if row else None


def set_last_odds_sync(conn) -> None:
    conn.execute("""
        INSERT INTO odds_sync (id, last_synced_at) VALUES (1, datetime('now'))
        ON CONFLICT(id) DO UPDATE SET last_synced_at = datetime('now')
    """)
