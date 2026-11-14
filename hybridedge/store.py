"""Small on-device store for incident threads and the incident event log.

Conversation/agent state itself lives in LangGraph checkpoints (checkpoints.db);
this store only holds the index of incidents and the operator-visible log.
"""
from __future__ import annotations

import json
import time
import uuid

import aiosqlite

from .config import settings

DB = settings.data_dir / "app.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT NOT NULL, ts REAL NOT NULL,
  priority TEXT NOT NULL, text TEXT NOT NULL, tier TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_thread ON events(thread_id, ts);
CREATE TABLE IF NOT EXISTS contexts (
  thread_id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS flows (
  thread_id TEXT NOT NULL, turn_id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY (thread_id, turn_id)
);
CREATE TABLE IF NOT EXISTS responses (
  id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL,
  completed_at REAL, output_text TEXT, meta TEXT, error TEXT, metadata TEXT
);
"""

MIGRATIONS = [
    "ALTER TABLE threads ADD COLUMN source TEXT NOT NULL DEFAULT 'console'",
    "ALTER TABLE threads ADD COLUMN profile TEXT NOT NULL DEFAULT 'incident'",
]


async def init() -> None:
    async with aiosqlite.connect(DB) as db:
        await db.executescript(SCHEMA)
        for stmt in MIGRATIONS:  # idempotent: ignore "duplicate column"
            try:
                await db.execute(stmt)
            except aiosqlite.OperationalError:
                pass
        await db.commit()


async def create_thread(title: str, source: str = "console", profile: str = "incident") -> dict:
    now = time.time()
    t = {
        "id": uuid.uuid4().hex[:12],
        "title": title.strip()[:80] or "New incident",
        "created_at": now,
        "updated_at": now,
        "source": (source or "console")[:40],
        "profile": profile,
    }
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO threads (id, title, created_at, updated_at, source, profile) VALUES (?,?,?,?,?,?)",
            (t["id"], t["title"], now, now, t["source"], profile),
        )
        await db.commit()
    return t


async def list_threads() -> list[dict]:
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall("SELECT * FROM threads ORDER BY updated_at DESC")
    return [dict(r) for r in rows]


async def get_thread(thread_id: str) -> dict | None:
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall("SELECT * FROM threads WHERE id=?", (thread_id,))
    return dict(rows[0]) if rows else None


async def touch_thread(thread_id: str, title: str | None = None) -> None:
    async with aiosqlite.connect(DB) as db:
        if title:
            await db.execute("UPDATE threads SET updated_at=?, title=? WHERE id=?", (time.time(), title[:80], thread_id))
        else:
            await db.execute("UPDATE threads SET updated_at=? WHERE id=?", (time.time(), thread_id))
        await db.commit()


async def add_event(thread_id: str, text: str, priority: str, tier: str) -> dict:
    ev = {"thread_id": thread_id, "ts": time.time(), "priority": priority, "text": text, "tier": tier}
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "INSERT INTO events (thread_id, ts, priority, text, tier) VALUES (?,?,?,?,?)",
            (thread_id, ev["ts"], priority, text, tier),
        )
        ev["id"] = cur.lastrowid
        await db.commit()
    return ev


async def list_events(thread_id: str) -> list[dict]:
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall("SELECT * FROM events WHERE thread_id=? ORDER BY ts", (thread_id,))
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ responses
async def create_response(rid: str, thread_id: str, metadata: dict | None) -> None:
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO responses (id, thread_id, status, created_at, metadata) VALUES (?,?,?,?,?)",
            (rid, thread_id, "in_progress", time.time(), json.dumps(metadata or {})),
        )
        await db.commit()


async def finish_response(rid: str, status: str, output_text: str | None, meta: dict | None, error: str | None) -> None:
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "UPDATE responses SET status=?, completed_at=?, output_text=?, meta=?, error=? WHERE id=?",
            (status, time.time(), output_text, json.dumps(meta or {}), error, rid),
        )
        await db.commit()


async def get_response(rid: str) -> dict | None:
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall("SELECT * FROM responses WHERE id=?", (rid,))
    if not rows:
        return None
    r = dict(rows[0])
    r["meta"] = json.loads(r["meta"] or "{}")
    r["metadata"] = json.loads(r["metadata"] or "{}")
    return r


# ---------------------------------------------------------------------- flows
async def save_flow(thread_id: str, turn_id: str, flow: dict) -> None:
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT OR REPLACE INTO flows VALUES (?,?,?)", (thread_id, turn_id, json.dumps(flow)))
        await db.commit()


async def get_flows(thread_id: str) -> dict[str, dict]:
    async with aiosqlite.connect(DB) as db:
        rows = await db.execute_fetchall("SELECT turn_id, data FROM flows WHERE thread_id=?", (thread_id,))
    return {r[0]: json.loads(r[1]) for r in rows}


# -------------------------------------------------------------------- context
async def set_context(thread_id: str, data: dict) -> None:
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT OR REPLACE INTO contexts VALUES (?,?,?)", (thread_id, json.dumps(data), time.time()))
        await db.commit()


async def get_context(thread_id: str) -> dict:
    async with aiosqlite.connect(DB) as db:
        rows = await db.execute_fetchall("SELECT data FROM contexts WHERE thread_id=?", (thread_id,))
    return json.loads(rows[0][0]) if rows else {}


async def delete_thread(thread_id: str) -> None:
    """Remove a session's index row and everything stored alongside it (not its checkpoints)."""
    async with aiosqlite.connect(DB) as db:
        for table in ("events", "responses", "flows", "contexts"):
            await db.execute(f"DELETE FROM {table} WHERE thread_id=?", (thread_id,))
        await db.execute("DELETE FROM threads WHERE id=?", (thread_id,))
        await db.commit()
