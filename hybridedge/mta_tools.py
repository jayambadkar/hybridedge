"""Deterministic tools for a marauding terrorist attack (MTA) response.

They read the session context supplied by the calling app (via PUT /v1/sessions/{id}/context):

    {
      "reports":   [{"id", "type", "lat", "lon", "t", "text", "attackers"?, "weapon"?, "casualties"?}],
      "places":    [{"name", "lat", "lon"}],          # crowded places nearby
      "resources": {"arv": 4, "ambulance": 3}          # available units
    }

and return plain structured results the app can draw on a map. All geometry is simple
equirectangular maths — accurate to a few metres at city scale. Demo logic only.
"""
from __future__ import annotations

import math
from typing import Any

R_EARTH = 6_371_000.0


def _dist(a: dict, b: dict) -> float:
    lat = math.radians((a["lat"] + b["lat"]) / 2)
    dx = math.radians(b["lon"] - a["lon"]) * math.cos(lat) * R_EARTH
    dy = math.radians(b["lat"] - a["lat"]) * R_EARTH
    return math.hypot(dx, dy)


def _bearing(a: dict, b: dict) -> float:
    lat = math.radians((a["lat"] + b["lat"]) / 2)
    dx = math.radians(b["lon"] - a["lon"]) * math.cos(lat)
    dy = math.radians(b["lat"] - a["lat"])
    return (math.degrees(math.atan2(dx, dy)) + 360) % 360


def _move(p: dict, bearing: float, metres: float) -> dict:
    b = math.radians(bearing)
    dlat = metres * math.cos(b) / R_EARTH
    dlon = metres * math.sin(b) / (R_EARTH * math.cos(math.radians(p["lat"])))
    return {"lat": round(p["lat"] + math.degrees(dlat), 6), "lon": round(p["lon"] + math.degrees(dlon), 6)}


def _compass(b: float | None) -> str:
    if b is None:
        return "unknown"
    return ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"][int((b + 22.5) // 45) % 8]


def _nearest(p: dict, places: list[dict]) -> str:
    if not places:
        return ""
    best = min(places, key=lambda q: _dist(p, q))
    d = _dist(p, best)
    return best["name"] if d < 60 else f"{round(d / 10) * 10} m from {best['name']}"


def _sightings(ctx: dict) -> list[dict]:
    reps = [r for r in ctx.get("reports", []) if isinstance(r.get("lat"), (int, float)) and isinstance(r.get("lon"), (int, float))]
    return reps  # in the order they were filed (report clocks can disagree)


# --------------------------------------------------------------------- tools
def analyse_movement(ctx: dict) -> dict:
    reps = _sightings(ctx)
    places = ctx.get("places", [])
    if not reps:
        return {"reports": 0, "summary": "No located reports yet."}
    last = reps[-1]
    heading = speed = None
    if len(reps) >= 2:
        prev = reps[-2]
        d = _dist(prev, last)
        dt = max(60.0, float(last.get("t", 0)) - float(prev.get("t", 0)))
        if d > 25:
            heading = _bearing(prev, last)
            speed = max(0.6, min(3.0, d / dt))
    eff_speed = speed or 1.4
    predicted = []
    if heading is not None:
        for m in (2, 5):
            p = _move(last, heading, eff_speed * 60 * m)
            predicted.append({**p, "minutes": m, "near": _nearest(p, places)})
    at_risk = []
    for pl in places:
        d = _dist(last, pl)
        if d < 40:
            continue
        ahead = heading is not None and abs(((_bearing(last, pl) - heading) + 180) % 360 - 180) <= 50
        if d <= 250 or (ahead and d <= eff_speed * 600 + 150):
            at_risk.append({"name": pl["name"], "lat": pl["lat"], "lon": pl["lon"], "distance_m": round(d), "eta_min": round(d / eff_speed / 60, 1), "in_path": ahead})
    at_risk.sort(key=lambda x: (not x["in_path"], x["eta_min"]))
    attackers = max([int(r.get("attackers") or 0) for r in reps] or [0]) or None
    weapons = sorted({str(r["weapon"]).lower() for r in reps if r.get("weapon")})
    casualties = sum(int(r.get("casualties") or 0) for r in reps)
    conf = "low" if len(reps) == 1 else "medium" if len(reps) == 2 else "high"
    return {
        "reports": len(reps),
        "last_seen": {"lat": last["lat"], "lon": last["lon"], "near": _nearest(last, places), "t": last.get("t"), "text": last.get("text", "")},
        "heading": _compass(heading),
        "heading_deg": round(heading) if heading is not None else None,
        "speed_mps": round(speed, 1) if speed else None,
        "predicted": predicted,
        "places_at_risk": at_risk[:5],
        "attackers": attackers,
        "weapons": weapons,
        "casualties_reported": casualties,
        "confidence": conf,
        "summary": (
            f"{attackers or 'Unknown number of'} attacker(s), last seen {_nearest(last, places)}, "
            + (f"moving {_compass(heading)} at about {round(speed * 3.6, 1)} km/h" if heading is not None else "direction unknown")
            + (f"; next likely: {at_risk[0]['name']} in ~{at_risk[0]['eta_min']} min" if at_risk else "")
        ),
    }


def set_zones(ctx: dict) -> dict:
    mv = analyse_movement(ctx)
    if not mv.get("reports"):
        return {"summary": "No located reports yet — zones not set."}
    last = mv["last_seen"]
    heading = mv["heading_deg"]
    places = ctx.get("places", [])
    centre = last
    if mv["predicted"]:
        p = mv["predicted"][0]
        centre = {"lat": round((last["lat"] + p["lat"]) / 2, 6), "lon": round((last["lon"] + p["lon"]) / 2, 6)}
    hot_r = 250 + (60 if mv["predicted"] else 0)
    warm_r = hot_r + 350
    cold_r = warm_r + 400
    away = (heading + 180) % 360 if heading is not None else 180
    rvp = _move(centre, away, cold_r + 120)
    ccp = _move(centre, (away + 35) % 360, warm_r + 40)
    fcp = _move(centre, (away - 50) % 360, warm_r + 220)
    return {
        "hot": {"lat": centre["lat"], "lon": centre["lon"], "radius_m": hot_r},
        "warm": {"lat": centre["lat"], "lon": centre["lon"], "radius_m": warm_r},
        "cold": {"lat": centre["lat"], "lon": centre["lon"], "radius_m": cold_r},
        "rvp": {**rvp, "near": _nearest(rvp, places)},
        "ccp": {**ccp, "near": _nearest(ccp, places)},
        "fcp": {**fcp, "near": _nearest(fcp, places)},
        "summary": f"Hot zone {hot_r} m, warm zone {warm_r} m, cold zone {cold_r} m around {last['near']}. "
        f"RVP {_nearest(rvp, places)}; CCP {_nearest(ccp, places)}; forward command post {_nearest(fcp, places)}.",
    }


def plan_deployment(ctx: dict) -> dict:
    mv = analyse_movement(ctx)
    zones = set_zones(ctx)
    if not mv.get("reports"):
        return {"units": [], "summary": "No located reports yet."}
    res = ctx.get("resources") or {}
    n_arv = max(0, int(res.get("arv", 4)))
    n_amb = max(0, int(res.get("ambulance", 3)))
    places = ctx.get("places", [])
    last = mv["last_seen"]
    units: list[dict[str, Any]] = []
    targets = [{"lat": p["lat"], "lon": p["lon"], "why": f"Intercept at predicted position (+{p['minutes']} min, {p['near']})"} for p in mv["predicted"]]
    targets += [{"lat": r["lat"], "lon": r["lon"], "why": f"Protect {r['name']} (ETA {r['eta_min']} min)"} for r in mv["places_at_risk"] if r["in_path"]]
    if not targets:
        targets = [{"lat": last["lat"], "lon": last["lon"], "why": f"Locate and confront at last sighting ({last['near']})"}]
    for i in range(n_arv):
        if i < len(targets):
            t = targets[i]
            units.append({"id": f"ARV {i + 1}", "type": "arv", "lat": t["lat"], "lon": t["lon"], "task": t["why"]})
        else:
            b = (mv["heading_deg"] or 0) + 90 * (i - len(targets) + 1)
            p = _move({"lat": zones["hot"]["lat"], "lon": zones["hot"]["lon"]}, b, zones["hot"]["radius_m"])
            units.append({"id": f"ARV {i + 1}", "type": "arv", **p, "task": f"Contain hot zone edge ({_nearest(p, places)})"})
    for i in range(n_amb):
        spot = zones["ccp"] if i == 0 else zones["rvp"]
        units.append({"id": f"Ambulance {i + 1}", "type": "ambulance", "lat": spot["lat"], "lon": spot["lon"],
                      "task": "Casualty collection point" if i == 0 else "Wait at RVP for warm-zone access"})
    units.append({"id": "Command", "type": "command", "lat": zones["fcp"]["lat"], "lon": zones["fcp"]["lon"], "task": "Forward command post"})
    return {
        "units": units,
        "summary": f"{n_arv} ARVs: " + "; ".join(f"{u['id']} – {u['task']}" for u in units if u["type"] == "arv")
        + f". {n_amb} ambulances to CCP/RVP. Command at {zones['fcp']['near']}.",
    }


TOOL_SPECS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "analyse_movement",
            "description": "Analyse the located reports for this incident: last sighting, direction and speed of travel, predicted positions, crowded places at risk, attackers, weapons and casualties.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_zones",
            "description": "Set hot, warm and cold zones, the rendezvous point (RVP), casualty collection point (CCP) and forward command post from the latest reports.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_deployment",
            "description": "Assign the available armed response vehicles (ARVs) and ambulances to positions based on the latest reports and zones.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

FUNCS = {"analyse_movement": analyse_movement, "set_zones": set_zones, "plan_deployment": plan_deployment}


# ------------------------------------------------------------ casualty care
# Real Helsinki-area hospitals. Capacities are demo assumptions, not real figures.
HOSPITALS = [
    {"name": "Töölö Hospital (HUS trauma centre)", "lat": 60.1832, "lon": 24.9238, "p1": 6, "p2": 8},
    {"name": "Meilahti Tower Hospital (HUS)", "lat": 60.18822, "lon": 24.90641, "p1": 4, "p2": 8},
    {"name": "Jorvi Hospital (Espoo)", "lat": 60.22174, "lon": 24.68912, "p1": 2, "p2": 6},
    {"name": "Peijas Hospital (Vantaa)", "lat": 60.33153, "lon": 25.05934, "p1": 2, "p2": 6},
]
# Rail and metro stations near the city centre (metro positions approximate).
STATIONS = [
    {"name": "Kamppi metro station", "lat": 60.1687, "lon": 24.9312, "kind": "metro"},
    {"name": "Rautatientori metro station", "lat": 60.1706, "lon": 24.9415, "kind": "metro"},
    {"name": "Helsingin yliopisto metro station", "lat": 60.17276, "lon": 24.9486, "kind": "metro"},
    {"name": "Helsinki Central railway station", "lat": 60.17155, "lon": 24.94059, "kind": "rail"},
    {"name": "Kamppi bus terminal", "lat": 60.16965, "lon": 24.9333, "kind": "bus"},
]


def casualty_plan(ctx: dict) -> dict:
    reps = _sightings(ctx)
    n = sum(int(r.get("casualties") or 0) for r in reps)
    zones = set_zones(ctx)
    res = ctx.get("resources") or {}
    amb = max(0, int(res.get("ambulance", 3)))
    p1 = math.ceil(n * 0.2)
    p2 = math.ceil(n * 0.3)
    p3 = max(0, n - p1 - p2)
    needed = p1 + math.ceil(p2 / 2)
    ccp = zones.get("ccp") or (reps[-1] if reps else None)
    rows, left1, left2 = [], p1, p2
    for h in HOSPITALS:
        if not (left1 or left2):
            break
        a1, a2 = min(left1, h["p1"]), min(left2, h["p2"])
        left1 -= a1
        left2 -= a2
        if a1 or a2:
            d = _dist(ccp, h) * 1.4 if ccp else 0
            rows.append({"hospital": h["name"], "lat": h["lat"], "lon": h["lon"], "p1": a1, "p2": a2, "road_km": round(d / 1000, 1), "eta_min": round(d / 1000 / 40 * 60)})
    return {
        "casualties": n,
        "triage": {"P1_immediate": p1, "P2_urgent": p2, "P3_minor": p3},
        "ambulances_needed": needed,
        "ambulances_available": amb,
        "shortfall": max(0, needed - amb),
        "hospitals": rows,
        "unallocated": {"P1": left1, "P2": left2},
        "ccp": ccp and {"lat": ccp["lat"], "lon": ccp["lon"], "near": zones.get("ccp", {}).get("near", "")},
        "note": "Triage split and hospital capacities are planning assumptions.",
        "summary": f"{n} casualties → P1 {p1}, P2 {p2}, P3 {p3}. Ambulances needed {needed}, available {amb}"
        + (f" (short by {needed - amb})" if needed > amb else "")
        + ". " + "; ".join(f"{r['hospital'].split(' (')[0]}: P1 {r['p1']}, P2 {r['p2']}" for r in rows),
    }


def public_measures(ctx: dict) -> dict:
    mv = analyse_movement(ctx)
    zones = set_zones(ctx)
    if not mv.get("reports"):
        return {"summary": "No located reports yet."}
    c = zones["hot"]
    lock, evac = [], []
    for pl in ctx.get("places", []):
        d = _dist(c, pl)
        if d <= zones["hot"]["radius_m"]:
            evac.append({"name": pl["name"], "distance_m": round(d), "action": "Shelter in place now; evacuate only when police say it is safe"})
        elif d <= zones["warm"]["radius_m"]:
            lock.append({"name": pl["name"], "distance_m": round(d), "action": "Lock doors, keep people inside, away from windows"})
    for r in mv.get("places_at_risk", []):
        if r["in_path"] and r["name"] not in [x["name"] for x in evac + lock]:
            lock.append({"name": r["name"], "distance_m": r["distance_m"], "action": f"In the attackers' path (ETA {r['eta_min']} min): lock down immediately"})
    transport = []
    for s in STATIONS:
        d = _dist(c, s)
        if d <= zones["warm"]["radius_m"]:
            transport.append({"name": s["name"], "kind": s["kind"], "action": "Close; trains/buses do not stop", "distance_m": round(d)})
        elif d <= zones["cold"]["radius_m"]:
            transport.append({"name": s["name"], "kind": s["kind"], "action": "Keep open for outbound only", "distance_m": round(d)})
    area = mv["last_seen"]["near"]
    away = _compass((mv["heading_deg"] + 180) % 360) if mv["heading_deg"] is not None else "away from the area"
    alert_en = (f"EMERGENCY: A violent attack is in progress in Helsinki city centre near {area}. Avoid the area. "
                f"If you are nearby: RUN to a safe place ({away} if you can), HIDE if you cannot get away, "
                f"TELL the emergency number 112 when it is safe.")
    alert_fi = (f"VAARATIEDOTE: Helsingin keskustassa on käynnissä vakava väkivaltatilanne alueella {area}. Vältä aluetta. "
                f"Jos olet lähellä: JUOKSE turvaan, PIILOUDU jos et pääse pois, ILMOITA hätänumeroon 112 kun se on turvallista.")
    return {
        "alert_en": alert_en,
        "alert_fi": alert_fi,
        "evacuate": evac,
        "lockdown": lock,
        "transport": transport,
        "summary": f"Alert ready (EN/FI). {len(lock)} places to lock down, {len(evac)} to shelter, {len(transport)} stations affected.",
    }


def check_plan(ctx: dict) -> dict:
    """Consistency checks across the advisers' outputs (all recomputed from the reports)."""
    mv = analyse_movement(ctx)
    if not mv.get("reports"):
        return {"issues": [], "summary": "Nothing to check yet."}
    dep = plan_deployment(ctx)
    cas = casualty_plan(ctx)
    res = ctx.get("resources") or {}
    issues = []
    arvs = [u for u in dep["units"] if u["type"] == "arv"]
    for r in mv["places_at_risk"]:
        if r["in_path"] and r["eta_min"] <= 5 and not any(_dist(u, r) <= 250 for u in arvs):
            issues.append({"severity": "high", "owner": "firearms", "issue": f"{r['name']} is in the path (ETA {r['eta_min']} min) with no ARV within 250 m."})
    if "firearm" in mv.get("weapons", []) and int(res.get("arv", 4)) < 3:
        issues.append({"severity": "high", "owner": "firearms", "issue": "Firearm reported but fewer than 3 ARVs available: request more armed units."})
    if cas["shortfall"] > 0:
        issues.append({"severity": "high", "owner": "casualties", "issue": f"Ambulances needed {cas['ambulances_needed']}, available {cas['ambulances_available']} (short by {cas['shortfall']})."})
    if cas["unallocated"]["P1"] or cas["unallocated"]["P2"]:
        issues.append({"severity": "high", "owner": "casualties", "issue": f"Hospital capacity exceeded: {cas['unallocated']['P1']} P1 and {cas['unallocated']['P2']} P2 unallocated."})
    if mv["confidence"] != "high":
        issues.append({"severity": "medium", "owner": "intelligence", "issue": f"Only {mv['reports']} report(s): attacker count and direction are not confirmed."})
    return {"issues": issues, "summary": f"{len(issues)} issue(s) found" if issues else "No conflicts found."}


TOOL_SPECS += [
    {"type": "function", "function": {"name": "casualty_plan", "description": "Estimate triage categories from reported casualties, ambulances needed vs available, and allocate patients to Helsinki-area hospitals.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "public_measures", "description": "Draft the public alert (English and Finnish), list places to lock down or shelter, and rail/metro/bus stations to close.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "check_plan", "description": "Check the combined plan for conflicts and gaps (coverage of places at risk, ambulances, hospital capacity, intelligence confidence).", "parameters": {"type": "object", "properties": {}}}},
]
FUNCS.update({"casualty_plan": casualty_plan, "public_measures": public_measures, "check_plan": check_plan})
