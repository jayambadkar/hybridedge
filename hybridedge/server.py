"""FastAPI edge server: serves the web app, runs agent loops, exposes checkpoints."""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel

from . import store
from .agents_api import api as agents_api
from .config import ROOT, settings
from .flow import capability, flow_from_meta
from .graph import build_graph
from .router import router
from .runtime import RUNS, Run, RunBusy, cfg as _cfg, graph, set_graph, start_run




@asynccontextmanager
async def lifespan(app: FastAPI):
    await store.init()
    router.start()
    async with AsyncSqliteSaver.from_conn_string(str(settings.data_dir / "checkpoints.db")) as saver:
        set_graph(build_graph(saver))
        yield
    await router.stop()


app = FastAPI(title="HybridEdgeLLM", lifespan=lifespan)
# Other demo apps on this device (any localhost port) may call the Agents API directly.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(agents_api)


@app.middleware("http")
async def no_stale_assets(request, call_next):
    """Always revalidate the web app's files so a reload picks up the newest UI."""
    resp = await call_next(request)
    if not request.url.path.startswith(("/api/", "/v1/")):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


def _start(thread_id: str, kind: str, inp: Any, config: dict) -> Run:
    try:
        return start_run(thread_id, kind, inp, config)
    except RunBusy as e:
        raise HTTPException(409, str(e))


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
    st = router.status()
    st["capability"] = capability("cloud" if router.online else "edge")
    return st


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


@app.get("/api/settings")
async def get_settings():
    """Read-only view of the provider setup and policies. Secrets are never returned."""

    def hint(key: str) -> str | None:
        return f"…{key[-4:]}" if key else None

    return {
        "providers": [
            {
                "name": p.name,
                "label": p.label,
                "priority": i + 1,
                "base_url": p.base_url or None,
                "model": p.model,
                "model_label": settings.cloud_model_label,
                "hardware": p.hardware,
                "standby": p.standby,
                "capacity": p.capacity,
                "api_key": hint(p.api_key),
            }
            for i, p in enumerate(settings.providers)
        ],
        "edge": {
            "base_url": settings.edge_base_url,
            "model": settings.edge_model,
            "label": settings.edge_label,
            "concurrency": settings.edge_concurrency,
            "context_chars": settings.edge_context_chars,
        },
        "scaling": {
            "cloud_max_agents": settings.cloud_max_agents,
            "edge_max_agents": settings.edge_max_agents,
            "max_agent_steps": settings.max_agent_steps,
            "cloud_thinking": settings.cloud_thinking,
        },
        "failover": {
            "health_interval_s": settings.health_interval_s,
            "health_timeout_s": settings.health_timeout_s,
            "fail_threshold": settings.fail_threshold,
            "first_token_timeout_s": settings.first_token_timeout_s,
            "stall_timeout_s": settings.stall_timeout_s,
            "network_watch_s": 0.5,
        },
        "checkpoints": {"path": str(settings.data_dir / "checkpoints.db"), "durability": "sync"},
    }


@app.get("/api/threads")
async def threads():
    out = await store.list_threads()
    for t in out:
        run = RUNS.get(t["id"])
        t["running"] = bool(run and not run.done)
    return out


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
    for m in messages:
        if m.get("role") == "assistant":
            m["flow"] = flow_from_meta(m.get("meta") or {})
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
