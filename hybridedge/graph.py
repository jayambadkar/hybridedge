"""Checkpointed multi-agent workflow.

    user turn ─▶ plan ──Send×N──▶ specialist (subgraph: agent ⇄ tools ▶ finish) ──▶ synthesize ─▶ END

* Every super-step of the parent graph AND of every specialist subgraph is written
  to SQLite on the device (durability="sync"). Parallel specialists that finished
  are stored as pending writes, so a resumed run only re-executes unfinished ones,
  and an unfinished specialist resumes from its own last checkpoint.
* Scale up / down: the plan fans out to up to CLOUD_MAX_AGENTS specialists when a
  cloud is up, EDGE_MAX_AGENTS on-device. Each individual LLM step picks the best
  target *at that moment* (Verda → Modal → edge), so a workflow planned in the
  cloud keeps going on the laptop if the link drops, and speeds back up when a
  cloud returns. The edge model is serialised with a semaphore (one GPU).
"""
from __future__ import annotations

import json
import operator
import re
import time
import uuid
from typing import Annotated, Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .config import settings
from .router import EDGE, router, run_with_failover
from .tools import TOOL_SPECS, run_tool

# --------------------------------------------------------------------- roster
ROLES: dict[str, dict[str, Any]] = {
    "medical": {
        "title": "Medical",
        "brief": "EMS lead: patient assessment, START triage, treatment priorities, transport.",
        "tools": ["lookup_protocol", "start_triage", "log_event"],
    },
    "fire_hazmat": {
        "title": "Fire & Hazmat",
        "brief": "Fire/rescue lead: fire behaviour, hazmat isolation, rescue, firefighter safety.",
        "tools": ["lookup_protocol", "log_event"],
    },
    "scene_safety": {
        "title": "Scene Safety",
        "brief": "Police/scene lead: scene security, cordons, traffic, threats, de-escalation.",
        "tools": ["lookup_protocol", "log_event"],
    },
    "comms": {
        "title": "Command & Comms",
        "brief": "Incident command support: resources, CAN/MIST reports, radio traffic, incident log.",
        "tools": ["lookup_protocol", "log_event", "get_incident_log"],
    },
    "generalist": {
        "title": "Field Assistant",
        "brief": "Answers simple or single-domain questions directly.",
        "tools": ["lookup_protocol", "start_triage", "log_event", "get_incident_log"],
    },
}

POLICE_ROLES: dict[str, dict[str, Any]] = {
    "intel": {
        "title": "Intel Analyst",
        "brief": "Scores the threat with assess_threat, lists what is known vs unknown, and names the intel gaps to close.",
        "tools": ["assess_threat", "log_event"],
    },
    "formation": {
        "title": "Formation Planner",
        "brief": "Builds the officer formation with plan_formation (use the intel level and officer count given) and explains each assignment.",
        "tools": ["plan_formation", "assess_threat", "log_event"],
    },
    "containment": {
        "title": "Containment",
        "brief": "Cordons, evacuation of the public, traffic control and exit routes for the subject.",
        "tools": ["lookup_protocol", "log_event"],
    },
    "command": {
        "title": "Command & Comms",
        "brief": "Command structure, radio plan, specialist resources to request, and 'actions on' contingencies.",
        "tools": ["lookup_protocol", "get_incident_log", "log_event"],
    },
    "medical_support": {
        "title": "Medical Support",
        "brief": "EMS staging, casualty triage and medical contingencies for officers and the public.",
        "tools": ["start_triage", "lookup_protocol"],
    },
}

MTA_ROLES: dict[str, dict[str, Any]] = {
    "intelligence": {
        "title": "Intelligence",
        "brief": "Works out where the attackers are, where they are heading, how many, what weapons, and which crowded places are at risk. Uses analyse_movement.",
        "tools": ["analyse_movement", "log_event"],
    },
    "firearms": {
        "title": "Firearms tactics",
        "brief": "Deploys armed response vehicles (ARVs) to locate, contain and stop the attackers. Uses plan_deployment.",
        "tools": ["plan_deployment", "analyse_movement"],
    },
    "zones": {
        "title": "Zones and cordons",
        "brief": "Sets hot, warm and cold zones, the rendezvous point (RVP), casualty collection point (CCP) and forward command post. Uses set_zones.",
        "tools": ["set_zones"],
    },
    "casualties": {
        "title": "Casualty care",
        "brief": "Plans casualty care: CCP, ambulance staging at the RVP, warm-zone access and triage priorities.",
        "tools": ["set_zones", "start_triage", "lookup_protocol"],
    },
    "public_warning": {
        "title": "Public warning",
        "brief": "Writes the public alert (Run, Hide, Tell) and lists places to evacuate or lock down. Uses analyse_movement.",
        "tools": ["analyse_movement"],
    },
}

ALL_ROLES: dict[str, dict[str, Any]] = {**ROLES, **POLICE_ROLES, **MTA_ROLES}

COMMON = """Current local time: {now}. You support first responders (police, fire, EMS).
Be concise and actionable: most important action first, short numbered steps.
Use tools for facts: lookup_protocol for procedures (never invent protocol details), start_triage for START triage,
log_event for significant facts/decisions. Say exactly what information is missing for safety-critical calls.
Local SOPs, command and medical direction always take precedence."""

EDGE_NOTE = "\nDEGRADED MODE: you run on a small on-device model (cloud link down). Max ~100 words. Rely on tools; flag uncertainty."


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M")


def _clean(m: dict) -> dict:
    out: dict[str, Any] = {"role": m["role"], "content": m.get("content") or ""}
    if m.get("tool_calls"):
        out["tool_calls"] = m["tool_calls"]
    if m["role"] == "tool":
        out["tool_call_id"] = m["tool_call_id"]
    return out


def _fit(msgs: list[dict], target: str) -> list[dict]:
    """Trim history for the small edge context window, keeping valid turn boundaries."""
    if target != EDGE:
        return msgs
    msgs = [dict(m) for m in msgs]
    for m in msgs:
        if m["role"] == "tool" and len(m["content"]) > 1500:
            m["content"] = m["content"][:1500] + "...(truncated)"
    kept, used = [], 0
    for m in reversed(msgs):
        size = len(m["content"]) + sum(len(tc["function"]["arguments"]) for tc in m.get("tool_calls", []))
        if used + size > settings.edge_context_chars and kept:
            break
        kept.append(m)
        used += size
    kept.reverse()
    while kept and kept[0]["role"] not in ("user",):
        kept.pop(0)
    return kept or msgs[-1:]


def _conversation(messages: list[dict], limit: int = 8) -> list[dict]:
    """Conversation context for agents: user turns and final briefs only."""
    conv = [_clean(m) for m in messages if m["role"] in ("user", "assistant") and not m.get("tool_calls")]
    return conv[-limit:]


# ---------------------------------------------------------------------- state
def merge_results(left: list[dict], right: list[dict] | dict) -> list[dict]:
    if isinstance(right, dict) and right.get("__reset__"):
        return []
    return (left or []) + (right or [])


class State(TypedDict, total=False):
    profile: str  # which agent team runs this session ("incident" | "police_tactical")
    messages: Annotated[list[dict], operator.add]
    plan: dict
    results: Annotated[list[dict], merge_results]


class SpecState(TypedDict, total=False):
    task: dict
    context: list[dict]
    messages: Annotated[list[dict], operator.add]
    results: Annotated[list[dict], operator.add]


class SpecOut(TypedDict, total=False):
    results: Annotated[list[dict], operator.add]


def _emitter(ctx: dict):
    emit = get_stream_writer()
    return lambda ev: emit({**ev, **ctx})


# ----------------------------------------------------------------------- plan
PLAN_PROMPT = """You are the {lead}.
Available specialists:
{roster}
Decide which specialists should work on the latest user message, in parallel. Use at most {budget}.
- Simple, conversational or single-domain requests: exactly one specialist (use "generalist" if none fits).
- Complex multi-domain incidents: several specialists, each with a distinct, concrete sub-task.
{extra}
Respond with JSON only: {{"agents": [{{"role": "<role>", "task": "<one-sentence sub-task>"}}]}}"""


async def plan(state: State, config: RunnableConfig) -> dict:
    emit = _emitter({"agent": "lead", "task_id": "plan"})
    prof = PROFILES[state.get("profile") or "incident"]
    roles = prof["roles"]
    roster = "\n".join(f'- "{k}": {v["brief"]}' for k, v in roles.items())
    conv = _conversation(state["messages"])

    async def call(target: str, step_id: str) -> dict:
        budget = settings.cloud_max_agents if target != EDGE else settings.edge_max_agents
        prompt = PLAN_PROMPT.format(lead=prof["lead"], roster=roster, budget=budget, extra=prof["plan_extra"])
        msgs = [{"role": "system", "content": prompt}] + _fit(conv, target)
        msg = await router.stream_step(target, msgs, None, emit, step_id, json_mode=True, max_tokens=400, silent=True)
        msg["meta"]["budget"] = budget
        return msg

    msg = await run_with_failover(call, emit, {"agent": "lead", "task_id": "plan", "phase": "plan"})
    budget = msg["meta"]["budget"]
    tasks = []
    try:
        raw = msg["content"].strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
        data = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])
        seen = set()
        for a in data.get("agents", []):
            role = str(a.get("role", "")).strip()
            if role in roles and role not in seen and str(a.get("task", "")).strip():
                seen.add(role)
                tasks.append({"role": role, "task": str(a["task"]).strip()[:300]})
    except Exception:
        tasks = []
    if not tasks:
        tasks = [dict(t) for t in prof["fallback"]] if prof["fallback"] else [{"role": "generalist", "task": conv[-1]["content"][:300] if conv else "Assist the responder."}]
    tasks = tasks[:budget]
    for t in tasks:
        t["id"] = uuid.uuid4().hex[:8]
        t["title"] = ALL_ROLES[t["role"]]["title"]
    p = {"turn_id": uuid.uuid4().hex[:8], "tasks": tasks, "planned_on": msg["meta"]["target"], "budget": budget}
    emit({"type": "plan", "plan": p})
    return {"plan": p, "results": {"__reset__": True}}


def fan_out(state: State) -> list[Send]:
    ctx = _conversation(state["messages"])
    return [Send("specialist", {"task": t, "context": ctx, "messages": [], "results": []}) for t in state["plan"]["tasks"]]


POLICE_SYNTH = """You are the duty inspector. Merge the specialist reports into TACTICAL ORDERS for the officers on scene.
Use exactly these short sections:
**Situation** — one line.
**Intel level** — level and label, then the key factors.
**Posture** — one line, with the stand-off distance.
**Assignments** — numbered: position → number of officers → task. Use the formation tool results exactly.
**Actions on** — what to do if the subject exits, if a weapon is used, if a hostage is seen.
**Requests** — specialist resources to call.
Use the exact stand-off/outer distances, positions and officer counts from the formation tool output; never invent other distances.
Be brief and decisive. Do not add facts that are not in the reports. Current local time: {now}."""


# ---------------------------------------------------------- specialist subgraph
def _spec_tools(role: str) -> list[dict]:
    allowed = set(ALL_ROLES[role]["tools"])
    return [t for t in TOOL_SPECS if t["function"]["name"] in allowed]


def _steps(state: SpecState) -> int:
    return sum(1 for m in state.get("messages", []) if m["role"] == "assistant")


async def s_agent(state: SpecState, config: RunnableConfig) -> dict:
    task = state["task"]
    ctx = {"agent": task["role"], "task_id": task["id"]}
    emit = _emitter(ctx)
    allow_tools = _steps(state) < settings.max_agent_steps - 1

    async def call(target: str, step_id: str) -> dict:
        role = ALL_ROLES[task["role"]]
        system = f"You are the {role['title']} specialist. {role['brief']}\n" + COMMON.format(now=_now())
        if target == EDGE:
            system += EDGE_NOTE
        history = state.get("context", []) + [{"role": "user", "content": f"Your assigned sub-task: {task['task']}"}]
        history += [_clean(m) for m in state.get("messages", [])]
        msgs = [{"role": "system", "content": system}] + _fit(history, target)
        return await router.stream_step(target, msgs, _spec_tools(task["role"]) if allow_tools else None, emit, step_id)

    msg = await run_with_failover(call, emit, ctx)
    for tc in msg.get("tool_calls", []):
        emit({"type": "tool_call", "step_id": msg["meta"]["step_id"], "id": tc["id"], "name": tc["function"]["name"], "args": tc["function"]["arguments"]})
    return {"messages": [msg]}


async def s_tools(state: SpecState, config: RunnableConfig) -> dict:
    task = state["task"]
    emit = _emitter({"agent": task["role"], "task_id": task["id"]})
    last = state["messages"][-1]
    out = []
    for tc in last.get("tool_calls", []):
        name = tc["function"]["name"]
        content, result = await run_tool(name, tc["function"]["arguments"], config["configurable"]["thread_id"], last["meta"]["tier"])
        emit({"type": "tool_result", "id": tc["id"], "name": name, "result": result})
        out.append({"role": "tool", "tool_call_id": tc["id"], "content": content, "meta": {"name": name}})
    return {"messages": out}


async def s_finish(state: SpecState) -> dict:
    task = state["task"]
    msgs = state.get("messages", [])
    assistants = [m for m in msgs if m["role"] == "assistant"]
    final = next((m for m in reversed(assistants) if m.get("content")), assistants[-1] if assistants else {"content": ""})
    targets = [m["meta"]["target"] for m in assistants]
    result = {
        "task_id": task["id"],
        "role": task["role"],
        "title": task["title"],
        "task": task["task"],
        "output": final.get("content") or "(no output)",
        "targets": targets,
        "steps": len(assistants),
        "tools": [tc["function"]["name"] for m in assistants for tc in m.get("tool_calls", [])],
        "failover": any(m["meta"].get("failover") for m in assistants),
    }
    get_stream_writer()({"type": "agent_done", "agent": task["role"], "task_id": task["id"], "result": result})
    return {"results": [result]}


def _s_route(state: SpecState) -> str:
    last = state["messages"][-1]
    return "s_tools" if last.get("tool_calls") else "s_finish"


def build_specialist():
    g = StateGraph(SpecState, output_schema=SpecOut)
    g.add_node("s_agent", s_agent)
    g.add_node("s_tools", s_tools)
    g.add_node("s_finish", s_finish)
    g.add_edge(START, "s_agent")
    g.add_conditional_edges("s_agent", _s_route, {"s_tools": "s_tools", "s_finish": "s_finish"})
    g.add_edge("s_tools", "s_agent")
    g.add_edge("s_finish", END)
    return g.compile()


# ----------------------------------------------------------------- synthesize
SYNTH_PROMPT = """You are the incident lead. Merge the specialist reports below into ONE operational brief for the responder.
Format: a one-line situation summary, then "Priorities" as numbered actions (most urgent first, tagged with the owning discipline),
then "Needs / unknowns" if any. Do not repeat yourself; do not add facts that are not in the reports.
Current local time: {now}."""


MTA_SYNTH = """You are the incident commander for a marauding terrorist attack. Merge the specialist reports into a short COMMANDER'S BRIEF.
Use exactly these sections, each brief:
**Situation** — one line: what, where, when.
**Threat** — attackers, weapons, last seen, direction, places at risk with ETA.
**Zones** — hot, warm and cold zone sizes; RVP, CCP and forward command post locations.
**Armed response** — each ARV and its task.
**Casualty care** — CCP, ambulances, warm-zone access.
**Public warning** — the alert text to send (Run, Hide, Tell) and places to evacuate or lock down.
**METHANE** — one line each: Major incident, Exact location, Type, Hazards, Access, Number of casualties, Emergency services.
Use the tool results exactly (names, distances, ETAs). Do not invent facts. Current local time: {now}."""

PROFILES_EXTRA = {
    "mta": {
        "label": "Marauding terrorist attack response",
        "lead": "incident commander coordinating specialist advisers during a marauding terrorist attack",
        "roles": MTA_ROLES,
        "plan_extra": '- ALWAYS include "intelligence" and "zones". Add "firearms", "casualties" and "public_warning" when the budget allows.\n'
        "- Each update is a new report: re-run the specialists whose advice changes.",
        "fallback": [
            {"role": "intelligence", "task": "Analyse the latest reports: location, direction, places at risk."},
            {"role": "zones", "task": "Set zones, RVP, CCP and command post from the latest reports."},
        ],
        "synth": MTA_SYNTH,
    }
}


PROFILES: dict[str, dict[str, Any]] = {
    "incident": {
        "label": "Multi-agency incident team",
        "lead": "incident lead coordinating specialist agents for first responders",
        "roles": ROLES,
        "plan_extra": "",
        "fallback": None,
        "synth": SYNTH_PROMPT,
    },
    "police_tactical": {
        "label": "Police tactical response team",
        "lead": "duty inspector coordinating a police tactical planning cell",
        "roles": POLICE_ROLES,
        "plan_extra": '- For a new incident, ALWAYS include "intel" and "formation" first; add others if useful.\n'
        "- For an intel update, re-run the specialists whose output changes.",
        "fallback": [
            {"role": "intel", "task": "Score the threat and list intel gaps."},
            {"role": "formation", "task": "Assign the available officers to positions."},
        ],
        "synth": POLICE_SYNTH,
    },
    **PROFILES_EXTRA,
}


async def synthesize(state: State, config: RunnableConfig) -> dict:
    p = state["plan"]
    order = {t["id"]: i for i, t in enumerate(p["tasks"])}
    results = sorted([r for r in state.get("results", []) if r["task_id"] in order], key=lambda r: order[r["task_id"]])
    workflow = {"plan": p, "results": results}
    ctx = {"agent": "lead", "task_id": "synth", "phase": "synthesize"}
    emit = _emitter(ctx)
    prof = PROFILES[state.get("profile") or "incident"]
    if len(results) == 1:
        r = results[0]
        meta = {"target": r["targets"][-1] if r["targets"] else EDGE, "single": True}
        meta["tier"] = "edge" if meta["target"] == EDGE else "cloud"
        meta["model"] = router.label(meta["target"])
        meta["workflow"] = workflow
        emit({"type": "final", "content": r["output"], "meta": meta})
        return {"messages": [{"role": "assistant", "content": r["output"], "meta": meta}]}

    reports = "\n\n".join(f"## {r['title']} (task: {r['task']})\n{r['output']}" for r in results)
    conv = _conversation(state["messages"])
    user_req = conv[-1]["content"] if conv else ""

    async def call(target: str, step_id: str) -> dict:
        system = prof["synth"].format(now=_now()) + (EDGE_NOTE if target == EDGE else "")
        content = f"Responder request: {user_req}\n\nSpecialist reports:\n{reports}"
        if target == EDGE and len(content) > settings.edge_context_chars:
            content = content[: settings.edge_context_chars] + "\n...(truncated)"
        return await router.stream_step(target, [{"role": "system", "content": system}, {"role": "user", "content": content}], None, emit, step_id)

    msg = await run_with_failover(call, emit, ctx)
    msg["meta"]["workflow"] = workflow
    return {"messages": [msg]}


def build_graph(checkpointer):
    g = StateGraph(State)
    g.add_node("plan", plan)
    g.add_node("specialist", build_specialist())
    g.add_node("synthesize", synthesize)
    g.add_edge(START, "plan")
    g.add_conditional_edges("plan", fan_out, ["specialist"])
    g.add_edge("specialist", "synthesize")
    g.add_edge("synthesize", END)
    return g.compile(checkpointer=checkpointer)
