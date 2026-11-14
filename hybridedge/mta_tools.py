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
