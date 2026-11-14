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


# ------------------------------------------------------------- tactical tools
THREAT_LABELS = {1: "LOW", 2: "GUARDED", 3: "ELEVATED", 4: "CRITICAL"}
POSTURES = {
    1: "Direct contact",
    2: "Controlled approach",
    3: "Contain and call out",
    4: "Contain and negotiate — no entry",
}
STANDOFF_M = {1: 15, 2: 25, 3: 50, 4: 100}


def assess_threat(
    weapons: str = "unknown",
    subjects: int = 1,
    hostages: bool = False,
    barricaded: bool = False,
    violent_history: bool = False,
    intel_confidence: str = "medium",
) -> dict:
    """Deterministic intel-level scoring (demo model, not an operational standard)."""
    w = {"none": 0, "blunt": 1, "edged": 2, "unknown": 2, "firearm": 4}.get(str(weapons).lower(), 2)
    score = w + (2 if hostages else 0) + (1 if barricaded else 0) + (1 if violent_history else 0) + (1 if subjects > 1 else 0)
    factors = []
    if w >= 4:
        factors.append("Firearm reported")
    elif w >= 2:
        factors.append("Edged or unknown weapon")
    if hostages:
        factors.append("Hostages / vulnerable persons at risk")
    if barricaded:
        factors.append("Subject barricaded")
    if violent_history:
        factors.append("History of violence")
    if subjects > 1:
        factors.append(f"{subjects} subjects")
    level = 1 if score <= 1 else 2 if score <= 3 else 3 if score <= 5 else 4
    conf = str(intel_confidence).lower()
    if conf == "low" and level < 4:
        level += 1
        factors.append("Low intel confidence — level raised one step")
    return {
        "intel_level": level,
        "label": THREAT_LABELS[level],
        "posture": POSTURES[level],
        "standoff_m": STANDOFF_M[level],
        "factors": factors or ["No aggravating factors reported"],
        "score": score,
    }


def plan_formation(officers: int, intel_level: int, incident_type: str = "incident", structure: bool = True) -> dict:
    """Allocate available officers to positions around the subject location."""
    officers = max(1, min(int(officers), 40))
    lvl = max(1, min(int(intel_level), 4))
    positions: list[dict] = []
    left = officers
    requests: list[str] = []

    def add(pid: str, label: str, ring: str, bearing: int, n: int, note: str = "") -> None:
        nonlocal left
        n = max(0, min(n, left))
        positions.append({"id": pid, "label": label, "ring": ring, "bearing": bearing, "officers": n, "note": note})
        left -= n

    add("cp", "Command post", "outer", 200, 1 if officers >= 4 else 0, "Incident commander + radio")
    if lvl >= 3:
        sides = [("north", "Inner cordon N", 0), ("east", "Inner cordon E", 90), ("south", "Inner cordon S", 180), ("west", "Inner cordon W", 270)]
        if left < 6:
            sides = [sides[0], sides[2]]
        for sid, label, b in sides:
            add(f"inner_{sid}", label, "inner", b, 1, "Hold position, eyes on exits")
        if officers >= 6:
            add("overwatch", "Overwatch", "overwatch", 135, 2 if (lvl == 4 and left >= 6) else 1, "Elevated observation post")
        if lvl == 3:
            add("arrest", "Arrest team (standby)", "outer", 160, 2, "Ready for controlled surrender")
    else:
        add("contact", "Contact officer", "inner", 180, 1, "Lead communication")
        add("cover", "Cover officer", "inner", 210, 1, "Cover the contact officer")
        if structure:
            add("rear", "Rear containment", "inner", 0, 1, "Cover rear exit")
    ring_slots = [("outer_a", "Outer cordon / traffic A", 45), ("outer_b", "Outer cordon / traffic B", 315), ("outer_c", "Outer cordon / public C", 250)]
    i = 0
    while left > 0 and i < 12:
        pid, label, b = ring_slots[i % len(ring_slots)]
        existing = next((p for p in positions if p["id"] == pid), None)
        if existing:
            existing["officers"] += 1
            left -= 1
        else:
            add(pid, label, "outer", b, 1, "Keep public and traffic out")
        i += 1
    positions.append({"id": "ems", "label": "EMS staging", "ring": "staging", "bearing": 225, "officers": 0, "note": "Ambulance outside the outer cordon"})

    if lvl >= 3:
        requests.append("Armed / tactical support unit")
    if lvl == 4:
        requests += ["Trained negotiator", "Air support or drone overwatch"]
    requests.append("EMS to staging point")
    needed = {1: 3, 2: 4, 3: 8, 4: 12}[lvl]
    shortfall = max(0, needed - officers)
    if shortfall:
        requests.insert(0, f"{shortfall} more officers for a safe level-{lvl} formation")
    return {
        "incident_type": incident_type,
        "intel_level": lvl,
        "posture": POSTURES[lvl],
        "standoff_m": STANDOFF_M[lvl],
        "outer_m": STANDOFF_M[lvl] * 3,
        "officers_available": officers,
        "officers_assigned": sum(p["officers"] for p in positions),
        "positions": positions,
        "requests": requests,
        "note": "Demo allocation model. The incident commander makes the final call.",
    }


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
            "name": "assess_threat",
            "description": "Score the intel/threat level (1 LOW – 4 CRITICAL) from known facts. Returns level, posture, stand-off distance and factors.",
            "parameters": {
                "type": "object",
                "properties": {
                    "weapons": {"type": "string", "enum": ["none", "blunt", "edged", "firearm", "unknown"]},
                    "subjects": {"type": "integer"},
                    "hostages": {"type": "boolean"},
                    "barricaded": {"type": "boolean"},
                    "violent_history": {"type": "boolean"},
                    "intel_confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": ["weapons"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_formation",
            "description": "Assign the available officers to positions (cordons, overwatch, contact team, command post, EMS staging) for a given intel level.",
            "parameters": {
                "type": "object",
                "properties": {
                    "officers": {"type": "integer", "description": "Officers available on scene or en route"},
                    "intel_level": {"type": "integer", "minimum": 1, "maximum": 4},
                    "incident_type": {"type": "string"},
                    "structure": {"type": "boolean", "description": "Subject is inside a building"},
                },
                "required": ["officers", "intel_level"],
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


from . import mta_tools  # noqa: E402

TOOL_SPECS += mta_tools.TOOL_SPECS


async def run_tool(name: str, args_json: str, thread_id: str, tier: str) -> tuple[str, Any]:
    """Execute a tool call. Returns (content_for_model, structured_result)."""
    try:
        args = json.loads(args_json or "{}")
        if not isinstance(args, dict):
            raise ValueError("arguments must be an object")
        if name == "lookup_protocol":
            result = lookup_protocol(str(args.get("query", "")))
        elif name == "start_triage":
            spec = next(t for t in TOOL_SPECS if t["function"]["name"] == "start_triage")
            allowed = set(spec["function"]["parameters"]["properties"])
            result = start_triage(**{k: v for k, v in args.items() if k in allowed and v is not None})
        elif name == "log_event":
            pr = args.get("priority", "info")
            ev = await store.add_event(thread_id, str(args.get("text", ""))[:500], pr if pr in ("info", "warning", "critical") else "info", tier)
            result = {"logged": True, "id": ev["id"], "time": time.strftime("%H:%M:%S", time.localtime(ev["ts"]))}
        elif name == "assess_threat":
            keys = {"weapons", "subjects", "hostages", "barricaded", "violent_history", "intel_confidence"}
            result = assess_threat(**{k: v for k, v in args.items() if k in keys and v is not None})
        elif name == "plan_formation":
            keys = {"officers", "intel_level", "incident_type", "structure"}
            result = plan_formation(**{k: v for k, v in args.items() if k in keys and v is not None})
        elif name in mta_tools.FUNCS:
            result = mta_tools.FUNCS[name](await store.get_context(thread_id))
        elif name == "get_incident_log":
            evs = await store.list_events(thread_id)
            result = {"entries": [{"time": time.strftime("%H:%M:%S", time.localtime(e["ts"])), "priority": e["priority"], "text": e["text"]} for e in evs]}
        else:
            result = {"error": f"unknown tool {name}"}
    except Exception as e:
        result = {"error": f"{type(e).__name__}: {e}"}
    return json.dumps(result), result
