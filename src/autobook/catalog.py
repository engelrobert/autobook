"""SQLite catalog: one row per photo plus analysis, AI results and the cost ledger.

Every pipeline step reads and writes here, so re-running a step is cheap and
AI results are never paid for twice.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterable
from pathlib import Path

from autobook.discover.metadata import PhotoMeta

SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    size INTEGER, mtime REAL,
    width INTEGER, height INTEGER,
    taken TEXT, tz TEXT, ts_source TEXT,
    lat REAL, lon REAL,
    camera TEXT, orientation INTEGER, format TEXT,
    active INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS photos_path ON photos(path);
CREATE TABLE IF NOT EXISTS analysis (
    id TEXT PRIMARY KEY,
    sharpness REAL, brightness REAL, clip_low REAL, clip_high REAL,
    colorfulness REAL, phash TEXT, screenshot INTEGER,
    local_score REAL, dup_group TEXT, event_id TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY, idx INTEGER, start TEXT, end TEXT, n INTEGER,
    lat REAL, lon REAL, place TEXT, country TEXT, data TEXT
);
CREATE TABLE IF NOT EXISTS ai_results (
    key TEXT PRIMARY KEY, photo_id TEXT, kind TEXT, json TEXT, created REAL
);
CREATE INDEX IF NOT EXISTS ai_photo ON ai_results(photo_id, kind);
CREATE TABLE IF NOT EXISTS cost_ledger (
    ts REAL, model TEXT, kind TEXT, input_tokens INTEGER, output_tokens INTEGER,
    cache_read INTEGER, usd REAL, batch INTEGER
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""


class Catalog:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()  # AI calls record costs from worker threads
        self.db.executescript(SCHEMA)
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(cost_ledger)")}
        for col, decl in (("reasoning_tokens", "INTEGER DEFAULT 0"), ("seconds", "REAL")):
            if col not in cols:  # added after the first release; older work dirs lack them
                self.db.execute(f"ALTER TABLE cost_ledger ADD COLUMN {col} {decl}")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # photos -----------------------------------------------------------
    def lookup_unchanged(self, path: str, size: int, mtime: float) -> str | None:
        row = self.db.execute("SELECT id FROM photos WHERE path=? AND size=? AND mtime=?",
                              (path, size, mtime)).fetchone()
        return row["id"] if row else None

    def upsert_photo(self, m: PhotoMeta) -> None:
        r = m.to_row()
        cols = ",".join(r)
        q = ",".join("?" * len(r))
        upd = ",".join(f"{k}=excluded.{k}" for k in r if k != "id")
        self.db.execute(f"INSERT INTO photos({cols},active) VALUES({q},1) "
                        f"ON CONFLICT(id) DO UPDATE SET {upd}, active=1", list(r.values()))

    def deactivate_all(self) -> None:
        self.db.execute("UPDATE photos SET active=0")

    def photos(self, active_only: bool = True) -> list[sqlite3.Row]:
        q = "SELECT * FROM photos" + (" WHERE active=1" if active_only else "") + " ORDER BY taken, path"
        return self.db.execute(q).fetchall()

    def photo(self, pid: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM photos WHERE id=?", (pid,)).fetchone()

    # analysis ---------------------------------------------------------
    def upsert_analysis(self, pid: str, **fields) -> None:
        cols = ["id", *fields]
        q = ",".join("?" * len(cols))
        upd = ",".join(f"{k}=excluded.{k}" for k in fields)
        self.db.execute(f"INSERT INTO analysis({','.join(cols)}) VALUES({q}) "
                        f"ON CONFLICT(id) DO UPDATE SET {upd}", [pid, *fields.values()])

    def analysis(self) -> dict[str, sqlite3.Row]:
        return {r["id"]: r for r in self.db.execute("SELECT * FROM analysis")}

    def joined(self) -> list[dict]:
        """Active photos joined with their analysis, in time order."""
        rows = self.db.execute(
            "SELECT p.*, a.sharpness, a.brightness, a.clip_low, a.clip_high, a.colorfulness, "
            "a.phash, a.screenshot, a.local_score, a.dup_group, a.event_id "
            "FROM photos p LEFT JOIN analysis a ON a.id=p.id WHERE p.active=1 ORDER BY p.taken, p.path"
        ).fetchall()
        return [dict(r) for r in rows]

    # events -----------------------------------------------------------
    def replace_events(self, events: Iterable[dict]) -> None:
        self.db.execute("DELETE FROM events")
        for e in events:
            self.db.execute(
                "INSERT INTO events(id,idx,start,end,n,lat,lon,place,country,data) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (e["id"], e["idx"], e["start"], e["end"], e["n"], e.get("lat"), e.get("lon"),
                 e.get("place"), e.get("country"), json.dumps(e.get("data", {}))))

    def events(self) -> list[dict]:
        out = []
        for r in self.db.execute("SELECT * FROM events ORDER BY idx"):
            d = dict(r)
            d["data"] = json.loads(d["data"] or "{}")
            out.append(d)
        return out

    # AI cache ---------------------------------------------------------
    def ai_get(self, key: str) -> dict | None:
        row = self.db.execute("SELECT json FROM ai_results WHERE key=?", (key,)).fetchone()
        return json.loads(row["json"]) if row else None

    def ai_put(self, key: str, photo_id: str | None, kind: str, value: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO ai_results(key,photo_id,kind,json,created) VALUES(?,?,?,?,?)",
                        (key, photo_id, kind, json.dumps(value, ensure_ascii=False), time.time()))

    def ai_latest(self, kind: str) -> dict[str, dict]:
        """Most recent AI result of a kind per photo."""
        out: dict[str, dict] = {}
        for r in self.db.execute("SELECT photo_id, json FROM ai_results WHERE kind=? ORDER BY created", (kind,)):
            out[r["photo_id"]] = json.loads(r["json"])
        return out

    # cost ledger --------------------------------------------------------
    def add_cost(self, model: str, kind: str, input_tokens: int, output_tokens: int,
                 cache_read: int, usd: float, batch: bool, reasoning: int = 0,
                 seconds: float | None = None) -> None:
        """output_tokens includes reasoning; cache_read = cached input tokens; seconds = call duration
        (None for batch items)."""
        with self.lock:
            self.db.execute("INSERT INTO cost_ledger(ts, model, kind, input_tokens, output_tokens, cache_read, "
                            "usd, batch, reasoning_tokens, seconds) VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (time.time(), model, kind, input_tokens, output_tokens, cache_read, usd, int(batch),
                             reasoning, seconds))
            self.db.commit()

    def total_cost(self) -> float:
        with self.lock:
            return float(self.db.execute("SELECT COALESCE(SUM(usd),0) FROM cost_ledger").fetchone()[0])

    def cost_summary(self) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT model, kind, COUNT(*) calls, SUM(input_tokens) input_tokens, SUM(output_tokens) output_tokens, "
            "COALESCE(SUM(reasoning_tokens),0) reasoning_tokens, ROUND(SUM(usd),4) usd, "
            "ROUND(AVG(seconds),1) avg_seconds, ROUND(MAX(seconds),1) max_seconds "
            "FROM cost_ledger GROUP BY model, kind ORDER BY usd DESC")]

    # kv ---------------------------------------------------------------
    def kv_get(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def kv_set(self, key: str, value) -> None:
        self.db.execute("INSERT OR REPLACE INTO kv(key,value) VALUES(?,?)", (key, json.dumps(value)))

    def commit(self) -> None:
        self.db.commit()
