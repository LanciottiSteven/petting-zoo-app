"""SQLite persistence for simulation runs, draft state and manual overrides."""
from __future__ import annotations
import json, sqlite3, time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "pettingzoo.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at REAL NOT NULL,
  kind TEXT NOT NULL,           -- 'draft' | 'season' | 'recommend'
  label TEXT,
  params TEXT NOT NULL,
  result TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS draft_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  updated_at REAL NOT NULL,
  payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS overrides (
  name TEXT PRIMARY KEY,
  games_missed INTEGER NOT NULL DEFAULT 0,
  note TEXT,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def save_run(kind: str, params: dict, result: dict, label: str | None = None) -> int:
    with connect() as con:
        cur = con.execute(
            "INSERT INTO runs (created_at, kind, label, params, result) VALUES (?,?,?,?,?)",
            (time.time(), kind, label, json.dumps(params), json.dumps(result)))
        return cur.lastrowid


def list_runs(kind: str | None = None, limit: int = 50) -> list[dict]:
    q = "SELECT id, created_at, kind, label, params FROM runs"
    args: tuple = ()
    if kind:
        q += " WHERE kind = ?"; args = (kind,)
    q += " ORDER BY id DESC LIMIT ?"
    args += (limit,)
    with connect() as con:
        return [{**dict(r), "params": json.loads(r["params"])}
                for r in con.execute(q, args)]


def get_run(run_id: int) -> dict | None:
    with connect() as con:
        r = con.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not r:
            return None
        return {**dict(r), "params": json.loads(r["params"]),
                "result": json.loads(r["result"])}


def delete_run(run_id: int) -> None:
    with connect() as con:
        con.execute("DELETE FROM runs WHERE id = ?", (run_id,))


def save_draft_state(payload: dict) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO draft_state (id, updated_at, payload) VALUES (1,?,?) "
            "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at, "
            "payload=excluded.payload", (time.time(), json.dumps(payload)))


def load_draft_state() -> dict:
    with connect() as con:
        r = con.execute("SELECT payload FROM draft_state WHERE id = 1").fetchone()
        return json.loads(r["payload"]) if r else {"taken": [], "my_roster": [], "pick_no": 1}


def set_override(name: str, games_missed: int, note: str = "") -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO overrides (name, games_missed, note, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET games_missed=excluded.games_missed, "
            "note=excluded.note, updated_at=excluded.updated_at",
            (name, games_missed, note, time.time()))


def clear_override(name: str) -> None:
    with connect() as con:
        con.execute("DELETE FROM overrides WHERE name = ?", (name,))


def get_overrides() -> dict[str, tuple[int, str]]:
    with connect() as con:
        return {r["name"]: (r["games_missed"], r["note"] or "")
                for r in con.execute("SELECT * FROM overrides")}


def set_setting(key: str, value) -> None:
    with connect() as con:
        con.execute("INSERT INTO settings (key,value) VALUES (?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, json.dumps(value)))


def get_setting(key: str, default=None):
    with connect() as con:
        r = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default
