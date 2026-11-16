"""Database (SQLite, one file: <data>/isg.db): events, tasks, measurement slices.

All times are local time in ISO format ("2026-11-11T09:30:00"). Databases of v0.1.0 (Turkish schema) are converted by
migration.py before the first connection.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading

import paths
from i18n import N_, T

_LOCK = threading.RLock()

# Statements are separated by ";" (migration.py runs them one by one): no ";" inside comments.
SCHEMA = """
CREATE TABLE IF NOT EXISTS event (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  uid TEXT,                       -- id given by the engine (the same when the event starts and closes)
  ongoing INTEGER NOT NULL DEFAULT 0,  -- is the event ongoing now
  time TEXT NOT NULL,             -- moment the event started
  camera TEXT NOT NULL,           -- camera code
  camera_name TEXT NOT NULL,
  rule TEXT NOT NULL,             -- rule id
  rule_name TEXT NOT NULL,
  category TEXT NOT NULL,
  zone TEXT,                      -- zone name (if any)
  severity INTEGER NOT NULL,      -- 1-5
  likelihood INTEGER NOT NULL,    -- 1-5
  score INTEGER NOT NULL,         -- severity x likelihood
  serious INTEGER NOT NULL,       -- serious injury / fatality potential
  duration_s REAL NOT NULL,
  people INTEGER NOT NULL,        -- number of people involved
  x REAL, y REAL,                 -- position in the image (0-1)
  clip TEXT, snapshot TEXT,       -- relative paths under output/ (faces masked)
  description TEXT,
  sample INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'open',  -- open | assigned | resolved | false_alarm
  assignee TEXT, notes TEXT, status_time TEXT,
  measurement_id INTEGER          -- measurement.id
);
CREATE INDEX IF NOT EXISTS event_time ON event(time);
CREATE INDEX IF NOT EXISTS event_camera ON event(camera, time);
CREATE INDEX IF NOT EXISTS event_uid ON event(uid);

CREATE TABLE IF NOT EXISTS measurement (      -- every analyzed recording slice
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  camera TEXT NOT NULL, camera_name TEXT NOT NULL,
  start TEXT NOT NULL, duration_s REAL NOT NULL,
  person_s REAL NOT NULL,         -- total person-seconds in the image
  people_tracked INTEGER NOT NULL,  -- number of people tracked (approximate)
  peak_people INTEGER NOT NULL,
  vehicles_tracked INTEGER NOT NULL,
  ppe_compliant_s REAL NOT NULL, ppe_noncompliant_s REAL NOT NULL,
  violation_s REAL NOT NULL,      -- violation person-seconds weighted by severity
  grid TEXT NOT NULL,             -- JSON: {"cols": 32, "rows": 18, "person": [...], "vehicle": [...]}
  tracks TEXT NOT NULL,           -- JSON: [{"s": "person"|"vehicle", "p": [[x, y], ...]}]
  last_frame TEXT,
  source TEXT,                    -- signature of the analyzed file (if the same file is analyzed again, the old one is deleted)
  sample INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS measurement_camera ON measurement(camera, start);

CREATE TABLE IF NOT EXISTS task (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL, description TEXT,
  owner TEXT, due_date TEXT,
  status TEXT NOT NULL DEFAULT 'open',  -- open | in_progress | closed
  camera TEXT, rule TEXT, zone TEXT,
  created TEXT NOT NULL, closed TEXT
);
CREATE TABLE IF NOT EXISTS task_event (task INTEGER NOT NULL, event INTEGER NOT NULL, PRIMARY KEY (task, event));
"""


def connect() -> sqlite3.Connection:
    import migration

    migration.migrate()  # a v0.1.0 database must be converted before the new tables are created next to it
    paths.DATA.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(paths.DB), timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)
    return c


_CONN: sqlite3.Connection | None = None


def conn() -> sqlite3.Connection:
    global _CONN
    with _LOCK:
        if _CONN is None:
            _CONN = connect()
        return _CONN


def close() -> None:
    global _CONN
    with _LOCK:
        if _CONN is not None:
            _CONN.close()
            _CONN = None


def now() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


def query(sql: str, *p) -> list[dict]:
    with _LOCK:
        return [dict(r) for r in conn().execute(sql, p).fetchall()]


def execute(sql: str, *p) -> int:
    with _LOCK:
        c = conn().execute(sql, p)
        conn().commit()
        return c.lastrowid


def insert(table: str, record: dict) -> int:
    fields = list(record)
    return execute(f"INSERT INTO {table} ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
                   *[json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for v in record.values()])


def delete_camera(code: str, samples_only: bool = False) -> None:
    extra = " AND sample = 1" if samples_only else ""
    with _LOCK:
        conn().execute(f"DELETE FROM task_event WHERE event IN (SELECT id FROM event WHERE camera = ?{extra})", (code,))
        conn().execute(f"DELETE FROM event WHERE camera = ?{extra}", (code,))
        conn().execute(f"DELETE FROM measurement WHERE camera = ?{extra}", (code,))
        conn().commit()


def delete_source(camera: str, source: str) -> int:
    """Deletes earlier analysis results of the same file (events and task links included). Returns the number of slices deleted."""
    with _LOCK:
        slices = [r["id"] for r in conn().execute("SELECT id FROM measurement WHERE camera = ? AND source = ?", (camera, source)).fetchall()]
        for s in slices:
            conn().execute("DELETE FROM task_event WHERE event IN (SELECT id FROM event WHERE measurement_id = ?)", (s,))
            conn().execute("DELETE FROM event WHERE measurement_id = ?", (s,))
            conn().execute("DELETE FROM measurement WHERE id = ?", (s,))
        conn().commit()
        return len(slices)


# ---------------------------------------------------------------- general settings

# Names are N_-marked texts, translated in settings() (defaults that were never saved follow the panel language).
DEFAULT_SETTINGS = {
    "site": N_("Site"),
    "shifts": [{"name": N_("Day"), "start": "08:00", "end": "16:00"}, {"name": N_("Evening"), "start": "16:00", "end": "00:00"},
               {"name": N_("Night"), "start": "00:00", "end": "08:00"}],
    "outputs": [],   # [{"name": "...", "type": "webhook", "url": "https://...", "min_score": 9}]
}


def _default_settings() -> dict:
    v = DEFAULT_SETTINGS
    return {**v, "site": T(v["site"]), "shifts": [{**s, "name": T(s["name"])} for s in v["shifts"]], "outputs": []}


def settings() -> dict:
    """settings.json over the defaults. Keys of v0.1.0 (genel.json) in a hand-edited file are still understood."""
    try:
        g = json.loads(paths.SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        g = {}
    if not isinstance(g, dict):
        g = {}
    else:
        import legacy

        if any(k in g for k in legacy.SETTINGS_KEYS):
            import migration

            g = migration.convert_settings(g)
    return {**_default_settings(), **g}


def save_settings(g: dict, with_defaults: bool = True) -> None:
    """Writes settings.json. with_defaults=False writes only what is stored already plus `g` (defaults stay unsaved)."""
    paths.DATA.mkdir(parents=True, exist_ok=True)
    if with_defaults:
        base = settings()
    else:
        try:
            base = json.loads(paths.SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            base = {}
        if not isinstance(base, dict):
            base = {}
    data = json.dumps({**base, **g}, ensure_ascii=False, indent=1)
    tmp = paths.SETTINGS_FILE.with_name(paths.SETTINGS_FILE.name + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(paths.SETTINGS_FILE)


def shift_of(time: str, shifts: list[dict] | None = None) -> str:
    """Name of the shift the given moment falls in."""
    shifts = shifts or settings()["shifts"]
    minute = int(time[11:13]) * 60 + int(time[14:16])
    for s in shifts:
        a = int(s["start"][:2]) * 60 + int(s["start"][3:5])
        b = int(s["end"][:2]) * 60 + int(s["end"][3:5])
        if (a <= minute < b) if a < b else (minute >= a or minute < b):
            return s["name"]
    return "–"
