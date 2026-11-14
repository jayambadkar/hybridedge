"""Abstract, provider-agnostic flow model of one task (one user turn).

Every task moves through the same five stages:

    Prompt → Planning → Reasoning (agent ⇄ tool loops) → Checkpointing → Result

The *shape* of the reasoning stage depends only on the class of inference provider:

  cloud  — unconstrained: many agents fan out in parallel, large context, fast model
  edge   — constrained:   fewer agents, one generation at a time, small context

``FlowBuilder`` folds the run's event stream into this model incrementally (used for
live streams); ``flow_from_meta`` rebuilds it from a finished turn's stored metadata.
Clients render the same structure whatever app they are.
"""
from __future__ import annotations

import time
from typing import Any

from .config import settings

STAGES = [("prompt", "Prompt"), ("planning", "Planning"), ("reasoning", "Reasoning"), ("checkpointing", "Checkpointing"), ("result", "Result")]


def capability(mode: str) -> dict:
    """What the current class of provider allows. Drives the graph's shape."""
    if mode == "cloud":
        return {
            "mode": "cloud",
            "label": "Cloud · Unconstrained",
            "model": settings.cloud_model_label,
            "max_agents": settings.cloud_max_agents,
            "parallel": True,
            "context": "128k tokens",
            "summary": f"Up to {settings.cloud_max_agents} agents in parallel · large context · fast multi-step reasoning",
        }
    return {
        "mode": "edge",
        "label": "On-device · Constrained",
        "model": settings.edge_label,
        "max_agents": settings.edge_max_agents,
        "parallel": settings.edge_concurrency > 1,
        "context": "16k tokens",
        "summary": f"Up to {settings.edge_max_agents} agents, one generation at a time · small context · shorter answers",
    }


def _tier(target: str | None) -> str:
    return "edge" if not target or target == "edge" else "cloud"


class FlowBuilder:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.stage_status = {sid: "pending" for sid, _ in STAGES}
        self.stage_status["prompt"] = "done"
        self.stage_status["planning"] = "active"
        self.planning_target: str | None = None
        self.lanes: dict[str, dict] = {}
        self.order: list[str] = []
        self.result_target: str | None = None
        self.checkpoints = 0
        self.last_checkpoint: float | None = None
        self.failovers: list[dict] = []
        self.status = "running"
        self.task_id: str | None = None
        self.started_at = time.time()
        self.routing: list[dict] = []  # when and why work ran where (start + every switch)
        self._agent_target: dict[str, str] = {}

    def _route(self, ev: dict, kind: str, frm: str | None, to: str, reason: str | None) -> None:
        """Record a routing decision; merge simultaneous identical switches (parallel agents)."""
        who = ev.get("agent") or "lead"
        ts = ev.get("ts") or time.time()
        last = self.routing[-1] if self.routing else None
        if last and last["kind"] == kind and last["from"] == frm and last["to"] == to and ts - last["ts"] < 5:
            if who not in last["agents"]:
                last["agents"].append(who)
            return
        self.routing.append({"ts": ts, "kind": kind, "from": frm, "to": to, "reason": reason, "agents": [who]})

    # --------------------------------------------------------------- folding
    def apply(self, ev: dict) -> bool:
        """Fold one run event in. Returns True if the flow's structure changed."""
        t = ev.get("type")
        tid = ev.get("task_id")
        if t == "token" or t == "reasoning":
            return False
        if t == "step_start":
            if tid == "plan" or self.planning_target is None:
                self.mode = _tier(ev.get("target"))  # shape = capability the task was planned under
            who = tid or "lead"
            tgt = ev.get("target")
            prev = self._agent_target.get(who)
            if not self.routing:
                self._route(ev, "start", None, tgt, ev.get("reason"))
            elif prev is not None and prev != tgt:
                self._route(ev, "switch", prev, tgt, ev.get("reason"))
            elif prev is None and tgt != self.routing[-1]["to"]:
                self._route(ev, "switch", self.routing[-1]["to"], tgt, ev.get("reason"))
            self._agent_target[who] = tgt
            if tid == "plan":
                self.planning_target = ev.get("target")
            elif tid == "synth":
                self.result_target = ev.get("target")
                self.stage_status.update(reasoning="done", result="active")
            elif tid in self.lanes:
                lane = self.lanes[tid]
                lane["status"] = "running"
                lane["steps"].append({"kind": "reason", "label": "Reason", "target": ev.get("target"), "tier": _tier(ev.get("target")), "status": "running"})
            return True
        if t == "plan":
            p = ev["plan"]
            self.task_id = p.get("turn_id")
            for task in p["tasks"]:
                self.lanes[task["id"]] = {"id": task["id"], "role": task["role"], "title": task["title"], "task": task["task"], "status": "queued", "steps": []}
                self.order.append(task["id"])
            self.stage_status.update(planning="done", reasoning="active", checkpointing="active")
            return True
        if t == "tool_call" and tid in self.lanes:
            steps = self.lanes[tid]["steps"]
            target = steps[-1]["target"] if steps else None
            steps.append({"kind": "tool", "label": ev.get("name"), "target": target, "tier": "device", "status": "running", "id": ev.get("id")})
            return True
        if t == "tool_result":
            for lane in self.lanes.values():
                for st in lane["steps"]:
                    if st.get("id") == ev.get("id"):
                        st["status"] = "done"
                        return True
            return False
        if t == "step_end" and tid in self.lanes:
            for st in reversed(self.lanes[tid]["steps"]):
                if st["kind"] == "reason" and st["status"] == "running":
                    st["status"] = "done"
                    break
            return True
        if t == "failover":
            self.failovers.append({"from": ev.get("from"), "to": ev.get("to"), "agent": ev.get("agent"), "reason": ev.get("reason")})
            self._route(ev, "failover", ev.get("from"), ev.get("to"), f"{ev.get('reason')} — {ev.get('to_reason') or ''}".strip(" —"))
            self._agent_target[tid or "lead"] = ev.get("to")
            if tid in self.lanes:
                for st in reversed(self.lanes[tid]["steps"]):
                    if st["kind"] == "reason" and st["status"] == "running":
                        st["status"] = "failed_over"
                        break
            return True
        if t == "upgrade":
            self._route(ev, "upgrade", ev.get("from"), ev.get("to"), ev.get("reason"))
            self._agent_target[tid or "lead"] = ev.get("to")
            if tid in self.lanes:
                for st in reversed(self.lanes[tid]["steps"]):
                    if st["kind"] == "reason" and st["status"] == "running":
                        st["status"] = "moved"
                        break
            return True
        if t == "agent_done" and tid in self.lanes:
            self.lanes[tid]["status"] = "done"
            return True
        if t == "checkpoint":
            self.checkpoints += 1
            self.last_checkpoint = ev.get("ts") or time.time()
            return self.checkpoints % 5 == 1  # don't spam snapshots for every save
        if t == "final":
            self.result_target = (ev.get("meta") or {}).get("target")
            self.stage_status.update(reasoning="done", result="done", checkpointing="done")
            return True
        if t == "step_end" and tid == "synth":
            self.stage_status.update(result="done", checkpointing="done")
            return True
        if t == "done":
            self.status = "completed"
            for k in self.stage_status:
                self.stage_status[k] = "done"
            return True
        if t == "error":
            self.status = "failed"
            return True
        return False

    # ------------------------------------------------------------- snapshot
    def snapshot(self) -> dict:
        return _shape(
            mode=self.mode,
            status=self.status,
            stage_status=self.stage_status,
            lanes=[self.lanes[i] for i in self.order],
            planning_target=self.planning_target,
            result_target=self.result_target,
            checkpoints=self.checkpoints,
            last_checkpoint=self.last_checkpoint,
            failovers=self.failovers,
            task_id=self.task_id,
            routing=self.routing,
            started_at=self.started_at,
        )


def _name(target: str | None) -> str:
    if not target or target == "edge":
        return "This device"
    p = next((p for p in settings.providers if p.name == target), None)
    return p.label if p else target


def _shape(*, mode, status, stage_status, lanes, planning_target, result_target, checkpoints, last_checkpoint, failovers, task_id, routing=None, started_at=None) -> dict:
    cap = capability(mode)
    lanes = [dict(lane, loops=sum(1 for s in lane["steps"] if s["kind"] == "reason")) for lane in lanes]
    return {
        "object": "flow",
        "task_id": task_id,
        "status": status,
        "mode": mode,
        "capability": cap,
        "stages": [{"id": sid, "label": label, "status": stage_status[sid]} for sid, label in STAGES],
        "planning": {"target": planning_target, "tier": _tier(planning_target) if planning_target else None},
        "lanes": lanes,
        "result": {"target": result_target, "tier": _tier(result_target) if result_target else None},
        "checkpoints": {"count": checkpoints, "last_at": last_checkpoint},
        "failovers": failovers,
        "layout": "parallel" if cap["parallel"] and mode == "cloud" else "serial",
        "started_at": started_at,
        "routing": [dict(r, from_name=_name(r["from"]) if r["from"] else None, to_name=_name(r["to"])) for r in (routing or [])],
        "providers": {"edge": "This device", **{p.name: p.label for p in settings.providers}},
    }


def flow_from_meta(meta: dict) -> dict | None:
    """Rebuild a finished turn's flow from the assistant message metadata."""
    wf = (meta or {}).get("workflow")
    if not wf:
        return None
    plan = wf.get("plan") or {}
    results = {r["task_id"]: r for r in wf.get("results", [])}
    lanes = []
    for task in plan.get("tasks", []):
        r = results.get(task["id"], {})
        steps, tools = [], list(r.get("tools", []))
        for target in r.get("targets", []):
            steps.append({"kind": "reason", "label": "Reason", "target": target, "tier": _tier(target), "status": "done"})
            if tools and len(steps) < 2 * len(r.get("targets", [])) - 1:
                steps.append({"kind": "tool", "label": tools.pop(0), "target": target, "tier": "device", "status": "done"})
        lanes.append({"id": task["id"], "role": task["role"], "title": task["title"], "task": task["task"], "status": "done", "steps": steps})
    planned = plan.get("planned_on")
    result_target = meta.get("target")
    return _shape(
        mode=_tier(planned),
        status="completed",
        stage_status={sid: "done" for sid, _ in STAGES},
        lanes=lanes,
        planning_target=planned,
        result_target=result_target,
        checkpoints=0,
        last_checkpoint=None,
        failovers=[{"agent": r["role"]} for r in results.values() if r.get("failover")],
        task_id=plan.get("turn_id"),
    )
