"""Field tools. All run on-device so they keep working with no connectivity."""
from __future__ import annotations

import json
import re
import time
from typing import Any

from . import store

# Demo reference cards. Generic summaries only -- local SOPs and medical direction always take precedence.
PROTOCOLS: dict[str, dict[str, Any]] = {
    "start_triage": {
        "title": "START triage (mass-casualty, adults)",
        "tags": "triage mci mass casualty start sort tag walking wounded",
        "steps": [
            "Able to walk -> MINOR (green). Direct to a collection point.",
            "Not breathing -> open/reposition airway. Still not breathing -> EXPECTANT/DECEASED (black). Breathing after repositioning -> IMMEDIATE (red).",
            "Respiratory rate > 30/min -> IMMEDIATE (red).",
            "No radial pulse OR capillary refill > 2 s -> IMMEDIATE (red). Control major bleeding.",
            "Cannot follow simple commands -> IMMEDIATE (red). Otherwise -> DELAYED (yellow).",
            "Spend < 60 s per patient; only interventions: open airway, control bleeding, position.",
        ],
    },
    "cpr_adult": {
        "title": "Adult CPR / BLS",
        "tags": "cpr cardiac arrest bls compressions aed unresponsive not breathing resuscitation",
        "steps": [
            "Scene safe; check responsiveness and breathing (<= 10 s). Call for help and an AED.",
            "Compressions: center of chest, 100-120/min, depth at least 5 cm (2 in), full recoil, minimise pauses.",
            "30 compressions : 2 breaths without an advanced airway.",
            "Attach AED as soon as available; follow prompts; resume compressions immediately after shock.",
            "Rotate compressors about every 2 minutes.",
        ],
    },
    "bleeding_control": {
        "title": "Severe bleeding control",
        "tags": "bleeding hemorrhage haemorrhage tourniquet wound packing trauma stop the bleed",
        "steps": [
            "Apply firm direct pressure.",
            "Limb bleeding not controlled -> tourniquet 5-7 cm (2-3 in) above the wound, not over a joint; tighten until bleeding stops.",
            "Record tourniquet application time; do not remove in the field.",
            "Junctional/torso wounds -> pack wound and hold pressure.",
        ],
    },
    "burns_rule_of_nines": {
        "title": "Burns: rule of nines (adult)",
        "tags": "burn burns tbsa rule of nines fire injury",
        "steps": [
            "Head & neck 9%; each arm 9%; front trunk 18%; back trunk 18%; each leg 18%; perineum 1%.",
            "Patient's palm (incl. fingers) ~ 1% TBSA for scattered burns.",
            "Cool with running water, remove jewellery, cover with clean dressing, prevent hypothermia.",
            "Consider airway burns: singed nasal hair, soot, hoarse voice -> early escalation.",
        ],
    },
    "hazmat_initial": {
        "title": "Hazmat: initial actions",
        "tags": "hazmat chemical spill leak gas fumes placard erg isolation",
        "steps": [
            "Approach uphill and upwind; do not walk into or touch spilled material.",
            "Identify the product from placards / UN number / shipping papers at a safe distance.",
            "Look up initial isolation and protective-action distances in the current Emergency Response Guidebook (ERG).",
            "Isolate the area, deny entry, request hazmat team, report via CAN.",
        ],
    },
    "scene_sizeup": {
        "title": "Scene size-up",
        "tags": "scene size up arrival safety hazards resources",
        "steps": [
            "Scene safety and hazards (traffic, fire, violence, utilities).",
            "Number of patients / people involved; mechanism of injury.",
            "Resources needed now; establish command and staging.",
            "Report: unit, location, what you see, what you are doing, what you need.",
        ],
    },
    "can_report": {
        "title": "CAN radio report",
        "tags": "radio report can conditions actions needs comms update",
        "steps": ["Conditions: what is happening now.", "Actions: what you are doing.", "Needs: what resources you require."],
    },
    "mist_handover": {
        "title": "MIST / ATMIST handover",
        "tags": "handover hospital mist atmist report patient transfer",
        "steps": [
            "Age, Time of incident.",
            "Mechanism of injury.",
            "Injuries found / suspected.",
            "Signs: vital signs and trends.",
            "Treatment given and response.",
        ],
    },
    "mayday_lunar": {
        "title": "Firefighter MAYDAY (LUNAR)",
        "tags": "mayday firefighter trapped lost down lunar rit emergency",
        "steps": [
            "Transmit 'MAYDAY MAYDAY MAYDAY'.",
            "Location, Unit, Name, Assignment/Air, Resources needed.",
            "Activate PASS; conserve air; stay low; attempt self-rescue if possible.",
        ],
    },
    "deescalation": {
        "title": "Verbal de-escalation",
        "tags": "police de-escalation crisis mental health agitated subject behavioural",
        "steps": [
            "Create distance and time; position with cover and an exit.",
            "One communicator; calm, low, slow voice; use the person's name.",
            "Listen, acknowledge feelings, avoid arguing; offer simple choices.",
            "Request specialist / crisis team resources early.",
        ],
    },
}


def lookup_protocol(query: str) -> dict:
    words = set(re.findall(r"[a-z0-9]+", query.lower()))
    scored = []
    for key, p in PROTOCOLS.items():
        hay = set(re.findall(r"[a-z0-9]+", (p["title"] + " " + p["tags"]).lower()))
        score = len(words & hay)
        if score:
            scored.append((score, key))
    scored.sort(reverse=True)
    if not scored:
        return {"found": False, "available": [p["title"] for p in PROTOCOLS.values()]}
    best = [PROTOCOLS[k] | {"id": k} for _, k in scored[:2]]
    return {
        "found": True,
        "results": [{"id": b["id"], "title": b["title"], "steps": b["steps"]} for b in best],
        "note": "Demo reference card. Local SOPs and medical direction take precedence.",
    }


def start_triage(
    can_walk: bool,
    breathing: bool,
    breathing_after_airway_repositioning: bool | None = None,
    respiratory_rate: int | None = None,
    radial_pulse_present: bool | None = None,
    capillary_refill_seconds: float | None = None,
    follows_commands: bool | None = None,
) -> dict:
    def out(cat: str, why: str) -> dict:
        colors = {"MINOR": "green", "DELAYED": "yellow", "IMMEDIATE": "red", "EXPECTANT": "black"}
        return {"category": cat, "tag_color": colors[cat], "reason": why, "algorithm": "START (adult)"}

    if can_walk:
        return out("MINOR", "Ambulatory")
    if not breathing:
        if breathing_after_airway_repositioning:
            return out("IMMEDIATE", "Breathing only after airway repositioning")
        if breathing_after_airway_repositioning is None:
            return {"category": "INCOMPLETE", "reason": "Reposition airway, then re-run with breathing_after_airway_repositioning."}
        return out("EXPECTANT", "Apnoeic after airway repositioning")
    if respiratory_rate is not None and respiratory_rate > 30:
        return out("IMMEDIATE", f"Respiratory rate {respiratory_rate} > 30")
    if radial_pulse_present is False or (capillary_refill_seconds is not None and capillary_refill_seconds > 2):
        return out("IMMEDIATE", "Poor perfusion (no radial pulse or cap refill > 2 s)")
    if follows_commands is False:
        return out("IMMEDIATE", "Cannot follow simple commands")
    missing = [n for n, v in (("respiratory_rate", respiratory_rate), ("follows_commands", follows_commands)) if v is None]
    if radial_pulse_present is None and capillary_refill_seconds is None:
        missing.append("radial_pulse_present or capillary_refill_seconds")
    if missing:
        return {"category": "INCOMPLETE", "reason": "Need: " + ", ".join(missing)}
    return out("DELAYED", "Breathing <= 30, perfusion adequate, follows commands")


TOOL_SPECS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "lookup_protocol",
            "description": "Search the on-device field protocol library (triage, CPR, bleeding, burns, hazmat, scene size-up, radio reports, handover, mayday, de-escalation).",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Keywords, e.g. 'tourniquet bleeding'"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_triage",
            "description": "Deterministic START mass-casualty triage for one adult patient. Returns MINOR/DELAYED/IMMEDIATE/EXPECTANT.",
            "parameters": {
                "type": "object",
                "properties": {
                    "can_walk": {"type": "boolean"},
                    "breathing": {"type": "boolean"},
                    "breathing_after_airway_repositioning": {"type": "boolean"},
                    "respiratory_rate": {"type": "integer", "description": "Breaths per minute"},
                    "radial_pulse_present": {"type": "boolean"},
                    "capillary_refill_seconds": {"type": "number"},
                    "follows_commands": {"type": "boolean"},
                },
                "required": ["can_walk", "breathing"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_event",
            "description": "Append a timestamped entry to this incident's log (patient tagged, unit arrived, hazard found, decision made).",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "priority": {"type": "string", "enum": ["info", "warning", "critical"]},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_incident_log",
            "description": "Read back this incident's timestamped log.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


async def run_tool(name: str, args_json: str, thread_id: str, tier: str) -> tuple[str, Any]:
    """Execute a tool call. Returns (content_for_model, structured_result)."""
    try:
        args = json.loads(args_json or "{}")
        if not isinstance(args, dict):
            raise ValueError("arguments must be an object")
        if name == "lookup_protocol":
            result = lookup_protocol(str(args.get("query", "")))
        elif name == "start_triage":
            allowed = {k for k in TOOL_SPECS[1]["function"]["parameters"]["properties"]}
            result = start_triage(**{k: v for k, v in args.items() if k in allowed and v is not None})
        elif name == "log_event":
            pr = args.get("priority", "info")
            ev = await store.add_event(thread_id, str(args.get("text", ""))[:500], pr if pr in ("info", "warning", "critical") else "info", tier)
            result = {"logged": True, "id": ev["id"], "time": time.strftime("%H:%M:%S", time.localtime(ev["ts"]))}
        elif name == "get_incident_log":
            evs = await store.list_events(thread_id)
            result = {"entries": [{"time": time.strftime("%H:%M:%S", time.localtime(e["ts"])), "priority": e["priority"], "text": e["text"]} for e in evs]}
        else:
            result = {"error": f"unknown tool {name}"}
    except Exception as e:
        result = {"error": f"{type(e).__name__}: {e}"}
    return json.dumps(result), result
