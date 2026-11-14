"""FastAPI edge server: serves the web app, runs agent loops, exposes checkpoints."""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel

from . import store
from .config import ROOT, settings
from .graph import build_graph
from .router import router


class Run:
    """A graph run decoupled from any HTTP connection: clients can drop and re-attach."""

    def __init__(self, thread_id: str, kind: str) -> None:
        self.thread_id = thread_id
        self.kind = kind
        self.events: list[dict] = []
        self.done = False
        self._cond = asyncio.Condition()
        self.task: asyncio.Task | None = None

    async def push(self, ev: dict) -> None:
        ev.setdefault("ts", time.time())
        async with self._cond:
            self.events.append(ev)
            self._cond.notify_all()

    async def finish(self) -> None:
        async with self._cond:
            self.done = True
            self._cond.notify_all()

    async def subscribe(self, start: int = 0) -> AsyncIterator[dict]:
        i = start
        while True:
            async with self._cond:
                while i >= len(self.events) and not self.done:
                    await self._cond.wait()
                batch = self.events[i:]
                finished = self.done
            for ev in batch:
                yield ev
            i += len(batch)
            if finished and i >= len(self.events):
                return


RUNS: dict[str, Run] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await store.init()
    router.start()
    async with AsyncSqliteSaver.from_conn_string(str(settings.data_dir / "checkpoints.db")) as saver:
        app.state.graph = build_graph(saver)
        yield
    await router.stop()


app = FastAPI(title="HybridEdgeLLM", lifespan=lifespan)


def graph():
    return app.state.graph


def _cfg(thread_id: str, **extra: Any) -> dict:
    return {"configurable": {"thread_id": thread_id, **extra}, "recursion_limit": 4 * settings.max_agent_steps + 4}


async def _execute(run: Run, inp: Any, config: dict) -> None:
    await run.push({"type": "run_start", "kind": run.kind, "active": router.active})
    try:
        async for ns, mode, chunk in graph().astream(inp, config, stream_mode=["custom", "checkpoints"], durability="sync", subgraphs=True):
            if mode == "custom":
                await run.push(chunk)
            elif mode == "checkpoints":
                vals = chunk["values"]
                task = vals.get("task") or {}
                await run.push(
                    {
                        "type": "checkpoint",
                        "checkpoint_id": chunk["config"]["configurable"]["checkpoint_id"],
                        "ns": "|".join(ns),
                        "step": chunk["metadata"].get("step"),
                        "source": chunk["metadata"].get("source"),
                        "next": list(chunk.get("next", [])),
                        "agent": task.get("role") or "lead",
                        "task_id": task.get("id"),
                    }
                )
        await run.push({"type": "done"})
    except asyncio.CancelledError:
        await run.push({"type": "error", "message": "Run cancelled. State is safe at the last checkpoint; resume to continue."})
        raise
    except Exception as e:
        await run.push({"type": "error", "message": f"{type(e).__name__}: {e}. State is safe at the last checkpoint."})
    finally:
        await store.touch_thread(run.thread_id)
        await run.finish()


def _start(thread_id: str, kind: str, inp: Any, config: dict) -> Run:
    cur = RUNS.get(thread_id)
    if cur and not cur.done:
        raise HTTPException(409, "A run is already in progress for this incident")
    run = Run(thread_id, kind)
    RUNS[thread_id] = run
    run.task = asyncio.create_task(_execute(run, inp, config))
    return run


def _sse(run: Run, start: int = 0) -> StreamingResponse:
    async def gen():
        async for ev in run.subscribe(start):
            yield f"data: {json.dumps(ev)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _require(thread_id: str) -> dict:
    t = await store.get_thread(thread_id)
    if not t:
        raise HTTPException(404, "Unknown incident")
    return t


# ----------------------------------------------------------------------- API
class LinkBody(BaseModel):
    offline: bool | None = None
    provider: str | None = None
    disabled: bool | None = None


class NewThread(BaseModel):
    title: str = "New incident"


class MessageBody(BaseModel):
    text: str


@app.get("/api/status")
async def status():
    return router.status()


@app.post("/api/link")
async def set_link(body: LinkBody):
    """Operator controls to simulate link loss (all clouds) or a single provider outage."""
    if body.provider is not None:
        if body.provider not in router.by_name:
            raise HTTPException(404, "Unknown provider")
        router.set_disabled(body.provider, bool(body.disabled))
    if body.offline is not None:
        router.set_offline(body.offline)
    if not router.offline:
        await router.check()
    return router.status()


@app.get("/api/threads")
async def threads():
    return await store.list_threads()


@app.post("/api/threads")
async def new_thread(body: NewThread):
    return await store.create_thread(body.title)


@app.get("/api/threads/{thread_id}")
async def thread(thread_id: str):
    t = await _require(thread_id)
    cfg = _cfg(thread_id)
    snap = await graph().aget_state(cfg)
    messages = (snap.values or {}).get("messages", []) if snap else []
    checkpoints = []
    async for c in graph().checkpointer.alist(cfg, limit=400):
        md = c.metadata or {}
        checkpoints.append(
            {
                "checkpoint_id": c.config["configurable"]["checkpoint_id"],
                "ns": c.config["configurable"].get("checkpoint_ns", ""),
                "step": md.get("step"),
                "source": md.get("source"),
                "ts": c.checkpoint.get("ts"),
            }
        )
    run = RUNS.get(thread_id)
    return {
        "thread": t,
        "messages": messages,
        "next": list(snap.next) if snap else [],
        "checkpoints": checkpoints,
        "events": await store.list_events(thread_id),
        "running": bool(run and not run.done),
    }


@app.post("/api/threads/{thread_id}/messages")
async def send(thread_id: str, body: MessageBody):
    t = await _require(thread_id)
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Empty message")
    if t["title"] == "New incident":
        await store.touch_thread(thread_id, title=text[:60])
    run = _start(thread_id, "message", {"messages": [{"role": "user", "content": text, "meta": {"ts": time.time()}}]}, _cfg(thread_id))
    return _sse(run)


@app.post("/api/threads/{thread_id}/resume")
async def resume(thread_id: str):
    await _require(thread_id)
    snap = await graph().aget_state(_cfg(thread_id))
    if not snap or not snap.next:
        raise HTTPException(400, "Nothing to resume")
    return _sse(_start(thread_id, "resume", None, _cfg(thread_id)))


@app.post("/api/threads/{thread_id}/rerun")
async def rerun(thread_id: str):
    """Time-travel: fork from the checkpoint taken right after the latest user message and re-run the whole
    workflow now that a cloud is available (full specialist budget instead of the on-device budget)."""
    await _require(thread_id)
    if not router.online:
        raise HTTPException(409, "Cloud link is not available")
    target = None
    async for s in graph().aget_state_history(_cfg(thread_id)):
        msgs = s.values.get("messages", [])
        if tuple(s.next) == ("plan",) and msgs and msgs[-1]["role"] == "user":
            target = s.config["configurable"]["checkpoint_id"]
            break
    if not target:
        raise HTTPException(400, "No turn to re-run")
    return _sse(_start(thread_id, "rerun", None, _cfg(thread_id, checkpoint_id=target)))


@app.get("/api/threads/{thread_id}/attach")
async def attach(thread_id: str, since: int = 0):
    run = RUNS.get(thread_id)
    if not run:
        raise HTTPException(404, "No run")
    return _sse(run, since)


app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
