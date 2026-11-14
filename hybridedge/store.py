"""Small on-device store for incident threads and the incident event log.

Conversation/agent state itself lives in LangGraph checkpoints (checkpoints.db);
this store only holds the index of incidents and the operator-visible log.
"""
from __future__ import annotations

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
"""


async def init() -> None:
    async with aiosqlite.connect(DB) as db:
        await db.executescript(SCHEMA)
        await db.commit()


async def create_thread(title: str) -> dict:
    now = time.time()
    t = {"id": uuid.uuid4().hex[:12], "title": title.strip()[:80] or "New incident", "created_at": now, "updated_at": now}
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT INTO threads VALUES (?,?,?,?)", (t["id"], t["title"], now, now))
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
