"""System benchmarks for the hybrid concept (run with the server up on :8787, nothing else using the device model).

  failover   – cut the link mid-answer; time until the device carries on writing the same answer
  checkpoint – cost of a durable SQLite checkpoint per LangGraph super-step
  scenarios  – the full MTA workflow under four network conditions

    uv run python bench/system.py [failover] [checkpoint] [scenarios]
"""
from __future__ import annotations

import asyncio
import json
import operator
import sys
import tempfile
import time
from pathlib import Path
from typing import Annotated, TypedDict

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench.models import merge  # noqa: E402
from bench.mta_run import BASE, run as mta  # noqa: E402

PROMPT = "Structure fire on the 2nd floor of a residential block, one occupant possibly trapped. Give a detailed step-by-step plan for the first crew."


async def failover_once(c: httpx.AsyncClient, cut_after_tokens: int = 40) -> dict:
    await c.post("/api/link", json={"offline": False})
    sid = (await c.post("/v1/sessions", json={"title": "Benchmark: failover", "agent": "incident", "source": "bench"})).json()["id"]
    n = 0
    t_cut = t_fail = t_resume = t_out = None
    start = time.time()
    async with c.stream("POST", "/v1/responses", json={"session_id": sid, "input": PROMPT, "stream": True, "source": "bench"}) as r:
        async for line in r.aiter_lines():
            if not line.startswith("data:"):
                continue
            ev = json.loads(line[5:])
            t = ev.get("type")
            # Cut while a specialist is visibly writing its answer; resume = its next token from the device.
            if t == "hybridedge.token" and ev.get("task_id") not in (None, "plan", "synth", "review"):
                n += 1
                if t_cut is None and n >= cut_after_tokens:
                    t_cut = time.time()
                    asyncio.ensure_future(c.post("/api/link", json={"offline": True}))
                elif t_fail is not None and t_resume is None:
                    t_resume = time.time()
            if t_fail is not None and t_out is None and t in ("hybridedge.token", "hybridedge.tool_call", "hybridedge.reasoning") and ev.get("task_id") not in (None, "plan", "synth", "review"):
                t_out = time.time()
            if t == "hybridedge.failover" and t_cut and t_fail is None:
                t_fail = time.time()
            if t in ("response.completed", "response.failed"):
                ok = t == "response.completed"
                break
    await c.post("/api/link", json={"offline": False})
    await c.delete(f"/v1/sessions/{sid}")
    return {"detect_ms": round(1000 * (t_fail - t_cut)) if t_fail and t_cut else None,
            "resume_ms": round(1000 * (t_resume - t_cut)) if t_resume and t_cut else None,
            "output_ms": round(1000 * (t_out - t_cut)) if t_out and t_cut else None,
            "completed": ok, "total_s": round(time.time() - start, 1)}


async def failover(n: int = 5) -> dict:
    async with httpx.AsyncClient(base_url=BASE, timeout=300) as c:
        runs = []
        for i in range(n):
            r = await failover_once(c)
            print("failover", i, r, flush=True)
            runs.append(r)
            await asyncio.sleep(3)
    ok = [r for r in runs if r["resume_ms"] is not None]
    med = lambda k: sorted(r[k] for r in ok)[len(ok) // 2] if ok else None  # noqa: E731
    return {"runs": runs, "n": n, "completed": sum(r["completed"] for r in runs), "detect_ms_p50": med("detect_ms"), "resume_ms_p50": med("resume_ms"), "output_ms_p50": med("output_ms"),
            "resume_ms_max": max((r["resume_ms"] for r in ok), default=None)}


async def checkpoint(steps: int = 200) -> dict:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph.graph import END, START, StateGraph

    class S(TypedDict):
        n: int
        messages: Annotated[list[dict], operator.add]

    def node(s: S) -> dict:
        # ~2 KB of new state per step, like an agent turn with a tool result
        return {"n": s["n"] + 1, "messages": [{"role": "assistant", "content": "x" * 2000}]}

    def build(cp=None):
        g = StateGraph(S)
        g.add_node("step", node)
        g.add_edge(START, "step")
        g.add_conditional_edges("step", lambda s: END if s["n"] >= steps else "step")
        return g.compile(checkpointer=cp)

    cfg = {"recursion_limit": steps + 10}
    plain = build()

    async def timed(graph, conf, **kw) -> float:
        t = time.perf_counter()
        await graph.ainvoke({"n": 0, "messages": []}, conf, **kw)
        return time.perf_counter() - t

    await timed(plain, cfg)  # warm-up
    base = min([await timed(plain, cfg) for _ in range(3)])
    with tempfile.TemporaryDirectory() as d:
        async with AsyncSqliteSaver.from_conn_string(f"{d}/cp.sqlite") as cp:
            g = build(cp)
            await timed(g, {**cfg, "configurable": {"thread_id": "w"}}, durability="sync")  # warm-up
            durable = min([await timed(g, {**cfg, "configurable": {"thread_id": f"b{i}"}}, durability="sync") for i in range(3)])
    per = (durable - base) / steps * 1000
    return {"steps": steps, "ms_per_checkpoint": round(per, 2), "state_kb_final": round(steps * 2.0), "durability": "sync (written before the next step starts)"}


SCENARIOS = [
    ("cloud", "Cloud up", {}),
    ("offline", "No network", {"offline": True}),
    ("cut", "Link lost at 12 s", {"cut_after": 12}),
    ("back", "Offline, cloud back at 9 s", {"offline": True, "back_after": 9}),
]


async def scenarios() -> dict:
    out = {}
    for key, label, kw in SCENARIOS:
        r = await mta(3, quiet=True, **kw)
        waiting = [k for k, a in r["agents"].items() if a.get("deferred")]
        agents = {k: a for k, a in r["agents"].items() if not a.get("deferred")}
        out[key] = {
            "label": label, "completed": "done" in r["marks"], "total_s": r["marks"].get("done"), "first_brief_s": r["marks"].get("first_brief_token"),
            "plan_s": r["marks"].get("plan"), "advisers": len(agents), "advisers_cloud": sum(1 for a in agents.values() if a["targets"] and a["targets"][-1] != "edge"),
            "advisers_device": sum(1 for a in agents.values() if a["targets"] and a["targets"][-1] == "edge"), "deferred": sorted(set(r["deferred"]) | set(waiting)),
            "review": r["review"], "brief_chars": r.get("brief_chars", 0), "checkpoints": r.get("checkpoints", 0), "switches": r.get("switches", []),
            "targets": r["targets"],
        }
        print("scenario", key, out[key], flush=True)
        await asyncio.sleep(3)
    return out


async def main(which: list[str]):
    if "checkpoint" in which:
        r = await checkpoint()
        print("checkpoint", r)
        merge(["system", "checkpoint"], r)
    if "failover" in which:
        merge(["system", "failover"], await failover())
    if "scenarios" in which:
        merge(["system", "scenarios"], await scenarios())


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or ["checkpoint", "failover", "scenarios"]))
