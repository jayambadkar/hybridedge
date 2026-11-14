"""HybridEdge on-device Agents API (/v1).

A small, OpenAI-Responses-style surface any app on the device can use:

  POST /v1/responses                 create a response (runs a checkpointed multi-agent workflow)
       {input, agent?, instructions?, previous_response_id?, session_id?, stream?, metadata?, source?}
  GET  /v1/responses/{id}            retrieve a response
  POST /v1/sessions                  create a session explicitly
  GET  /v1/sessions[/{id}]           list / inspect sessions
  GET  /v1/sessions/{id}/stream      live SSE of everything happening in a session, from any client
  GET  /v1/agents                    available agent teams (profiles)
  GET  /v1/status                    where agents are running right now (cloud provider or device)

Streaming uses Responses-API event names (response.created, response.output_text.delta,
response.completed, response.failed) plus ``hybridedge.*`` events that expose the agent
plan, per-agent steps and tool results, provider failovers, and checkpoints.

Every session is an ordinary HybridEdge incident, so the console shows it live.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import store
from .flow import capability, flow_from_meta
from .graph import ALL_ROLES, PROFILES
from .router import router as model_router
from .runtime import RUNS, Run, RunBusy, attach_flows, cfg, final_message, graph, start_run

api = APIRouter(prefix="/v1", tags=["agents"])
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


# --------------------------------------------------------------------- models
class ResponseCreate(BaseModel):
    input: str | list[dict[str, Any]]
    agent: str | None = Field(None, description="Agent team (see GET /v1/agents). Defaults to 'incident'.")
    model: str | None = Field(None, description="Accepted for compatibility: 'hybridedge' or 'hybridedge/<agent>'.")
    instructions: str | None = None
    previous_response_id: str | None = None
    session_id: str | None = None
    stream: bool = False
    metadata: dict[str, Any] | None = None
    source: str | None = Field(None, description="Name of the calling app, shown in the console.")
    title: str | None = None


class SessionCreate(BaseModel):
    title: str = "New session"
    agent: str = "incident"
    source: str = "api"


# -------------------------------------------------------------------- helpers
def _text_from_input(inp: str | list[dict[str, Any]]) -> str:
    if isinstance(inp, str):
        return inp.strip()
    parts: list[str] = []
    for item in inp:
        if item.get("role", "user") != "user":
            continue
        c = item.get("content")
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            parts += [p.get("text", "") for p in c if p.get("type") in ("input_text", "text")]
    return "\n".join(p for p in parts if p).strip()


def _agent_of(body: ResponseCreate) -> str | None:
    if body.agent:
        return body.agent
    if body.model and "/" in body.model:
        return body.model.split("/", 1)[1]
    if body.model and body.model in PROFILES:
        return body.model
    return None


def _summary(meta: dict) -> dict:
    wf = meta.get("workflow") or {}
    results = wf.get("results", [])
    return {
        "served_by": meta.get("target"),
        "model": meta.get("model"),
        "plan": [{"role": t["role"], "title": t["title"], "task": t["task"]} for t in (wf.get("plan") or {}).get("tasks", [])],
        "agents": [
            {"role": r["role"], "title": r["title"], "targets": r["targets"], "steps": r["steps"], "tools": r["tools"], "output": r["output"]}
            for r in results
        ],
        "failover": bool(meta.get("failover")) or any(r.get("failover") for r in results),
        "on_device": meta.get("target") == "edge" or any("edge" in r["targets"] for r in results),
    }


def _response_obj(row: dict) -> dict:
    text = row.get("output_text")
    rid = row["id"]
    out = []
    if text:
        out.append(
            {
                "type": "message",
                "id": "msg_" + rid.removeprefix("resp_"),
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        )
    return {
        "id": rid,
        "object": "response",
        "created_at": int(row["created_at"]),
        "completed_at": int(row["completed_at"]) if row.get("completed_at") else None,
        "status": row["status"],
        "model": "hybridedge",
        "session_id": row["thread_id"],
        "output": out,
        "output_text": text or "",
        "error": {"message": row["error"]} if row.get("error") else None,
        "metadata": row.get("metadata") or {},
        "hybridedge": _summary(row.get("meta") or {}),
    }


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _finalise(rid: str):
    async def hook(run: Run) -> None:
        if run.ok:
            msg = await final_message(run.thread_id) or {}
            await store.finish_response(rid, "completed", msg.get("content", ""), msg.get("meta") or {}, None)
        else:
            await store.finish_response(rid, "failed", None, {}, run.error or "Run failed")

    return hook


# ------------------------------------------------------------------ responses
@api.post("/responses")
async def create_response(body: ResponseCreate):
    text = _text_from_input(body.input)
    if not text:
        raise HTTPException(400, "input must contain user text")
    if body.instructions:
        text = f"{text}\n\n(Instructions from the calling app: {body.instructions.strip()})"
    source = (body.source or (body.metadata or {}).get("source") or "api")[:40]

    # Resolve the session: explicit > continuation of a previous response > new.
    thread_id = body.session_id
    if not thread_id and body.previous_response_id:
        prev = await store.get_response(body.previous_response_id)
        if not prev:
            raise HTTPException(404, "previous_response_id not found")
        thread_id = prev["thread_id"]
    if thread_id:
        t = await store.get_thread(thread_id)
        if not t:
            raise HTTPException(404, "session not found")
        profile = t.get("profile") or "incident"
    else:
        profile = _agent_of(body) or "incident"
        if profile not in PROFILES:
            raise HTTPException(400, f"unknown agent '{profile}'")
        t = await store.create_thread(body.title or text.splitlines()[0][:60], source=source, profile=profile)
        thread_id = t["id"]
    if t["title"] in ("New incident", "New session"):
        await store.touch_thread(thread_id, title=text.splitlines()[0][:60])

    rid = "resp_" + uuid.uuid4().hex[:24]
    await store.create_response(rid, thread_id, body.metadata)
    user = {"role": "user", "content": text, "meta": {"ts": time.time(), "source": source, "response_id": rid}}
    try:
        run = start_run(thread_id, "response", {"messages": [user], "profile": profile}, cfg(thread_id), source=source, on_finish=await _finalise(rid))
    except RunBusy as e:
        await store.finish_response(rid, "failed", None, {}, str(e))
        raise HTTPException(409, str(e))

    if not body.stream:
        await asyncio.shield(run.task)
        return _response_obj(await store.get_response(rid))

    async def gen():
        row = await store.get_response(rid)
        yield _sse("response.created", {"type": "response.created", "response": _response_obj(row)})
        yield _sse("response.in_progress", {"type": "response.in_progress", "response": _response_obj(row)})
        item_id = "msg_" + rid.removeprefix("resp_")
        text_out = ""
        async for ev in run.subscribe(0):
            t = ev.get("type")
            if t in ("done", "error"):
                break
            yield _sse(f"hybridedge.{t}", {**ev, "type": f"hybridedge.{t}"})
            if t == "token" and ev.get("task_id") == "synth":
                text_out += ev["text"]
                yield _sse("response.output_text.delta", {"type": "response.output_text.delta", "item_id": item_id, "output_index": 0, "content_index": 0, "delta": ev["text"]})
            elif t == "failover" and ev.get("task_id") == "synth" and text_out:
                text_out = ""  # the lead restarts its answer on the next provider
                yield _sse("hybridedge.output_reset", {"type": "hybridedge.output_reset", "item_id": item_id})
            elif t == "final":
                text_out = ev["content"]
                yield _sse("response.output_text.delta", {"type": "response.output_text.delta", "item_id": item_id, "output_index": 0, "content_index": 0, "delta": ev["content"]})
        await asyncio.shield(run.task)  # hooks have written the final record
        row = await store.get_response(rid)
        if row["status"] == "completed":
            yield _sse("response.output_text.done", {"type": "response.output_text.done", "item_id": item_id, "output_index": 0, "content_index": 0, "text": row["output_text"] or ""})
            yield _sse("response.completed", {"type": "response.completed", "response": _response_obj(row)})
        else:
            yield _sse("response.failed", {"type": "response.failed", "response": _response_obj(row)})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@api.get("/responses/{rid}")
async def get_response(rid: str):
    row = await store.get_response(rid)
    if not row:
        raise HTTPException(404, "response not found")
    return _response_obj(row)


# ------------------------------------------------------------------- sessions
def _session_obj(t: dict) -> dict:
    run = RUNS.get(t["id"])
    return {
        "id": t["id"],
        "object": "session",
        "title": t["title"],
        "agent": t.get("profile") or "incident",
        "source": t.get("source") or "console",
        "created_at": int(t["created_at"]),
        "updated_at": int(t["updated_at"]),
        "running": bool(run and not run.done),
    }


@api.post("/sessions")
async def create_session(body: SessionCreate):
    if body.agent not in PROFILES:
        raise HTTPException(400, f"unknown agent '{body.agent}'")
    return _session_obj(await store.create_thread(body.title, source=body.source, profile=body.agent))


@api.get("/sessions")
async def list_sessions():
    return {"object": "list", "data": [_session_obj(t) for t in await store.list_threads()]}


@api.get("/sessions/{sid}")
async def get_session(sid: str):
    t = await store.get_thread(sid)
    if not t:
        raise HTTPException(404, "session not found")
    snap = await graph().aget_state(cfg(sid))
    msgs = [dict(m) for m in ((snap.values or {}).get("messages", []) if snap else [])]
    await attach_flows(sid, msgs)
    return {
        **_session_obj(t),
        "messages": [
            {
                "role": m["role"],
                "content": m.get("content", ""),
                "hybridedge": _summary(m.get("meta") or {}) if m["role"] == "assistant" else None,
                "flow": m.get("flow") if m["role"] == "assistant" else None,
            }
            for m in msgs
            if m["role"] in ("user", "assistant")
        ],
        "incident_log": await store.list_events(sid),
    }


@api.get("/sessions/{sid}/flow")
async def session_flow(sid: str):
    """The flow of the session's current (or most recent) task: stages, agent lanes, loops, tiers."""
    if not await store.get_thread(sid):
        raise HTTPException(404, "session not found")
    run = RUNS.get(sid)
    if run is not None:
        return run.flow.snapshot()
    msg = dict(await final_message(sid) or {})
    await attach_flows(sid, [msg] if msg else [])
    flow = msg.get("flow")
    if flow is None:
        raise HTTPException(404, "no task has run in this session yet")
    return flow


@api.get("/capability")
async def current_capability():
    """What the current inference provider allows (drives the flow graph's shape)."""
    return capability("cloud" if model_router.online else "edge")


@api.get("/sessions/{sid}/stream")
async def stream_session(sid: str):
    """Follow a session live, whoever started its runs (console, Patrol app, another service)."""
    if not await store.get_thread(sid):
        raise HTTPException(404, "session not found")

    async def gen():
        yield _sse("session.attached", {"type": "session.attached", "session_id": sid})
        seen: Run | None = None
        idle = 0.0
        while True:
            run = RUNS.get(sid)
            if run is not None and run is not seen:
                seen = run
                yield _sse("session.run_started", {"type": "session.run_started", "kind": run.kind, "source": run.source})
                async for ev in run.subscribe(0):
                    yield _sse(f"hybridedge.{ev.get('type')}", {**ev, "type": f"hybridedge.{ev.get('type')}"})
                yield _sse("session.run_finished", {"type": "session.run_finished", "ok": run.ok, "error": run.error})
                idle = 0.0
            else:
                await asyncio.sleep(0.4)
                idle += 0.4
                if idle >= 15:
                    idle = 0.0
                    yield ": keep-alive\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


# -------------------------------------------------------------------- catalog
@api.get("/agents")
async def agents():
    return {
        "object": "list",
        "data": [
            {
                "id": pid,
                "label": p["label"],
                "roles": [{"id": rid, "title": r["title"], "brief": r["brief"], "tools": r["tools"]} for rid, r in p["roles"].items()],
            }
            for pid, p in PROFILES.items()
        ],
    }


@api.get("/models")
async def models():
    return {"object": "list", "data": [{"id": "hybridedge", "object": "model"}] + [{"id": f"hybridedge/{p}", "object": "model"} for p in PROFILES]}


@api.get("/status")
async def status():
    s = model_router.status()
    active = s["active"]
    prov = next((p for p in s["providers"] if p["name"] == active), None)
    return {
        "connected": active != "edge",
        "network": s["network"]["up"],
        "running_on": "device" if active == "edge" else active,
        "label": prov["label"] if prov else "This device",
        "model": prov["model"] if prov else s["edge"]["model"],
        "latency_ms": prov["latency_ms"] if prov else None,
        "max_agents": s["budget"]["max_agents"],
    }


ROLE_TITLES = {k: v["title"] for k, v in ALL_ROLES.items()}
