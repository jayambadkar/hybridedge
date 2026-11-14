"""Drive one Incident Command MTA report through the local Agents API and time every phase.

    uv run python bench/mta_run.py [--offline] [--reports 3] [--back-after SECONDS]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time

import httpx

BASE = "http://127.0.0.1:8787"
PLACES = [
    {"name": "Narinkka Square", "lat": 60.16967, "lon": 24.93493}, {"name": "Kamppi Centre", "lat": 60.1691, "lon": 24.93315},
    {"name": "Lasipalatsi", "lat": 60.17029, "lon": 24.93689}, {"name": "Kiasma", "lat": 60.17204, "lon": 24.93674},
    {"name": "Oodi Library", "lat": 60.17394, "lon": 24.93798}, {"name": "Railway Square", "lat": 60.17038, "lon": 24.93985},
    {"name": "Helsinki Central Station", "lat": 60.17155, "lon": 24.94059}, {"name": "Ateneum", "lat": 60.17002, "lon": 24.94407},
    {"name": "Stockmann", "lat": 60.1683, "lon": 24.94208}, {"name": "Kaisaniemi Park", "lat": 60.175, "lon": 24.94609},
    {"name": "Senate Square", "lat": 60.16951, "lon": 24.95227},
]
SCRIPT = [
    {"min": 0, "type": "Attack", "lat": 60.16962, "lon": 24.93455, "attackers": 2, "weapon": "knife", "casualties": 4, "text": "Two attackers with knives attacking people at Narinkka Square"},
    {"min": 3, "type": "Sighting", "lat": 60.17012, "lon": 24.93722, "attackers": 2, "weapon": "firearm", "casualties": 2, "text": "Attackers running east along Mannerheimintie past Lasipalatsi, one seen with a handgun"},
    {"min": 6, "type": "Shots fired", "lat": 60.17058, "lon": 24.93928, "attackers": 2, "weapon": "firearm", "casualties": 3, "text": "Shots fired on Railway Square, crowd fleeing"},
    {"min": 8, "type": "Sighting", "lat": 60.17128, "lon": 24.94062, "attackers": 2, "weapon": "firearm", "casualties": 0, "text": "Both attackers entered the Central Station main hall"},
]


async def run(n_reports: int = 3, offline: bool = False, back_after: float | None = None, quiet: bool = False, cut_after: float | None = None) -> dict:
    async with httpx.AsyncClient(base_url=BASE, timeout=300) as c:
        await c.post("/api/link", json={"offline": offline})
        sid = (await c.post("/v1/sessions", json={"title": "Benchmark: MTA Helsinki (exercise)", "agent": "mta", "source": "bench"})).json()["id"]
        t0 = time.time() - 600
        reports = [{"id": f"r{i+1}", **r, "t": t0 + r["min"] * 60} for i, r in enumerate(SCRIPT[:n_reports])]
        await c.put(f"/v1/sessions/{sid}/context", json={"reports": reports, "places": PLACES, "resources": {"arv": 4, "ambulance": 3}, "area": "Helsinki city centre"})
        text = " ".join(f"Report {i+1}: {r['type']} — {r['text']}. Attackers {r['attackers']}, weapon {r['weapon']}, casualties {r['casualties']}." for i, r in enumerate(reports))
        inp = f"Marauding terrorist attack declared in Helsinki city centre. {text} ARVs available: 4. Ambulances: 3. Produce the commander's brief."
        start = time.time()
        marks: dict[str, float] = {}
        out = {"session": sid, "offline": offline, "agents": {}, "deferred": [], "review": None, "targets": set(), "events": 0}
        back_task = None
        if back_after is not None:
            async def back():
                await asyncio.sleep(back_after)
                await c.post("/api/link", json={"offline": False})
                marks.setdefault("cloud_back", time.time() - start)
            back_task = asyncio.ensure_future(back())
        if cut_after is not None:
            async def cut():
                await asyncio.sleep(cut_after)
                await c.post("/api/link", json={"offline": True})
                marks.setdefault("cut", time.time() - start)
            back_task = asyncio.ensure_future(cut())
        async with c.stream("POST", "/v1/responses", json={"session_id": sid, "input": inp, "stream": True, "source": "bench"}) as r:
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                ev = json.loads(line[5:])
                out["events"] += 1
                t = ev.get("type", "")
                el = time.time() - start
                if t == "hybridedge.step_start":
                    out["targets"].add(ev.get("target"))
                    marks.setdefault(f"start:{ev.get('task_id')}", el)
                elif t == "hybridedge.plan":
                    marks["plan"] = el
                    out["plan"] = [x["role"] for x in ev["plan"]["tasks"]]
                    out["deferred"] = [x["role"] for x in ev["plan"].get("deferred") or []]
                elif t == "hybridedge.agent_done":
                    out["agents"][ev["result"]["role"]] = {"t": round(el, 1), "targets": ev["result"]["targets"], "chars": len(ev["result"]["output"]), "deferred": bool(ev["result"].get("deferred"))}
                elif t == "hybridedge.review":
                    marks["review"] = el
                    out["review"] = {k: ev["review"].get(k) for k in ("verdict", "device")} | {"issues": len(ev["review"].get("issues", []))}
                elif t == "hybridedge.checkpoint":
                    out["checkpoints"] = out.get("checkpoints", 0) + 1
                elif t in ("hybridedge.failover", "hybridedge.upgrade"):
                    out.setdefault("switches", []).append({"t": round(el, 1), "kind": t.split(".")[1], "to": ev.get("to"), "agent": ev.get("agent")})
                elif t == "response.output_text.delta":
                    marks.setdefault("first_brief_token", el)
                elif t == "response.completed":
                    marks["done"] = el
                    out["brief_chars"] = len(ev["response"].get("output_text") or "")
                elif t == "response.failed":
                    marks["failed"] = el
                    out["error"] = ev["response"].get("error")
                if not quiet and t in ("hybridedge.plan", "hybridedge.agent_done", "hybridedge.review", "hybridedge.revise", "response.completed", "hybridedge.failover", "hybridedge.upgrade"):
                    print(f"{el:6.1f}s {t}")
        if back_task:
            back_task.cancel()
        await c.post("/api/link", json={"offline": False})
        await c.delete(f"/v1/sessions/{sid}")
        out["marks"] = {k: round(v, 1) for k, v in marks.items()}
        out["targets"] = sorted(x for x in out["targets"] if x)
        return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--reports", type=int, default=3)
    ap.add_argument("--back-after", type=float)
    ap.add_argument("--cut-after", type=float)
    a = ap.parse_args()
    print(json.dumps(asyncio.run(run(a.reports, a.offline, a.back_after, cut_after=a.cut_after)), indent=1, default=list))
