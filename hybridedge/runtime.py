"""Run engine shared by the console API (/api) and the Agents API (/v1).

A ``Run`` executes the checkpointed LangGraph workflow in a background task, fully
decoupled from HTTP connections: any number of clients (the console, a mobile
demo app, another service) can subscribe to the same run's event stream, drop,
and re-attach. Completion hooks let the Responses API finalise its records even
if the client that started the run has gone away.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Awaitable, Callable

from . import store
from .config import settings
from .flow import FlowBuilder
from .router import router


class RunBusy(Exception):
    """A session already has a run in progress."""


class Run:
    def __init__(self, thread_id: str, kind: str, source: str = "console") -> None:
        self.thread_id = thread_id
        self.kind = kind
        self.source = source
        self.started_at = time.time()
        self.events: list[dict] = []
        self.done = False
        self.ok = False
        self.error: str | None = None
        self._cond = asyncio.Condition()
        self.task: asyncio.Task | None = None
        self.on_finish: list[Callable[["Run"], Awaitable[None]]] = []
        self.flow = FlowBuilder("cloud" if router.online else "edge")

    async def push(self, ev: dict) -> None:
        ev.setdefault("ts", time.time())
        async with self._cond:
            self.events.append(ev)
            # Keep an abstract flow model of the task and broadcast it whenever its structure changes.
            if self.flow.apply(ev):
                self.events.append({"type": "flow", "flow": self.flow.snapshot(), "ts": ev["ts"]})
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
_graph: Any = None


def set_graph(g: Any) -> None:
    global _graph
    _graph = g


def graph() -> Any:
    return _graph


def cfg(thread_id: str, **extra: Any) -> dict:
    return {"configurable": {"thread_id": thread_id, **extra}, "recursion_limit": 4 * settings.max_agent_steps + 4}


async def _execute(run: Run, inp: Any, config: dict) -> None:
    await run.push({"type": "run_start", "kind": run.kind, "source": run.source, "active": router.active})
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
        run.ok = True
        await run.push({"type": "done"})
    except asyncio.CancelledError:
        run.error = "Run cancelled. State is safe at the last checkpoint; resume to continue."
        await run.push({"type": "error", "message": run.error})
        raise
    except Exception as e:
        run.error = f"{type(e).__name__}: {e}. State is safe at the last checkpoint."
        await run.push({"type": "error", "message": run.error})
    finally:
        await store.touch_thread(run.thread_id)
        if run.flow.task_id:  # keep the full flow (incl. routing timeline) for this turn
            try:
                await store.save_flow(run.thread_id, run.flow.task_id, run.flow.snapshot())
            except Exception:
                pass
        for hook in run.on_finish:
            try:
                await hook(run)
            except Exception:
                pass
        await run.finish()


def start_run(thread_id: str, kind: str, inp: Any, config: dict, source: str = "console", on_finish=None) -> Run:
    cur = RUNS.get(thread_id)
    if cur and not cur.done:
        raise RunBusy("A run is already in progress for this session")
    run = Run(thread_id, kind, source)
    if on_finish:
        run.on_finish.append(on_finish)
    RUNS[thread_id] = run
    run.task = asyncio.create_task(_execute(run, inp, config))
    return run


async def attach_flows(thread_id: str, messages: list[dict]) -> None:
    """Give each assistant message its turn's flow: the saved live flow if we have it,
    otherwise one rebuilt from the message metadata."""
    from .flow import flow_from_meta

    saved = await store.get_flows(thread_id)
    for m in messages:
        if m.get("role") != "assistant":
            continue
        meta = m.get("meta") or {}
        turn = ((meta.get("workflow") or {}).get("plan") or {}).get("turn_id")
        m["flow"] = saved.get(turn) or flow_from_meta(meta)


async def final_message(thread_id: str) -> dict | None:
    """The latest assistant message in a session's current state."""
    snap = await graph().aget_state(cfg(thread_id))
    msgs = (snap.values or {}).get("messages", []) if snap else []
    return next((m for m in reversed(msgs) if m.get("role") == "assistant"), None)
