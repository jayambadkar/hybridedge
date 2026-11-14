"""Cancer care pathway analysis by wellbeing services county (web/pathway/).

For each county this module answers three questions from public, aggregate data:

    1. Is there a gap?            county value vs the whole country, the best county and the national target
    2. Where in the pathway?      detection -> primary care -> referral -> treatment -> outcome
    3. What should be done?       concrete proposals, each with an owner, steps, a KPI and its evidence

All figures come from the snapshot built by scripts/build_pathway_data.py (Sotkanet, THL).
Every number carries its source id, and the limits of the data are stated next to it.
Nothing here is patient-level data, and nothing is estimated beyond simple arithmetic
on published counts.
"""
from __future__ import annotations

import json
import math
import re
import statistics
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

DATA = json.loads((Path(__file__).with_name("pathway_data.json")).read_text())
COUNTIES = {c["code"]: c for c in DATA["counties"]}
META = DATA["indicators"]
VALUES = DATA["values"]

SCREENING_TARGET = 80.0  # national strategy: 80 % participation in every programme, in every county
MIN_COUNTIES = 15  # a year is used only when at least this many counties report
SMALL_POPULATION = 80_000
STANDS_OUT = 0.5  # stage score (in standard deviations) above which a stage is called the county's gap

STAGES = [
    {"id": "detection", "name": "Early detection", "question": "Do people take part in screening?"},
    {"id": "primary", "name": "Primary care", "question": "Can people see a health centre doctor quickly?"},
    {"id": "referral", "name": "Referral", "question": "Do referrals move quickly into specialist care?"},
    {"id": "treatment", "name": "Treatment start", "question": "Does treatment start within the target time?"},
    {"id": "outcome", "name": "Outcome", "question": "Do patients survive as long as elsewhere?"},
]
STAGE_NAME = {s["id"]: s["name"] for s in STAGES}

LABELS = {
    "screen_cervical": ("Cervical screening participation", "%"),
    "screen_breast": ("Breast screening participation", "%"),
    "screen_bowel": ("Bowel screening participation", "%"),
    "gp_wait_7d": ("Non-urgent doctor visits that waited over 7 days", "%"),
    "referral_3d": ("Referrals processed within 3 days", "%"),
    "wait_6m": ("Waiting over 6 months for specialist care", "per 10 000"),
    "surv_breast": ("Breast cancer five-year survival", "%"),
    "surv_cervical": ("Cervical cancer five-year survival", "%"),
    "surv_bowel": ("Bowel cancer five-year survival", "%"),
    "travel_barrier": ("Poor travel connections made care harder", "%"),
    "smokers": ("Daily smokers", "%"),
    "hpv_girls": ("HPV vaccine coverage, girls", "%"),
}
PROXY = {"gp_wait_7d", "referral_3d", "wait_6m"}  # all patients, not only cancer patients

# ------------------------------------------------------------------ sources
SOURCES: dict[str, dict[str, str]] = {
    "STRATEGY": {
        "title": "Kansallinen syöpästrategia 2026–2035 (National Cancer Strategy)",
        "publisher": "Ministry of Social Affairs and Health, 2026",
        "url": "https://files.hackjunction.app/resources/Kansallinen_syopastrategia_2026_2035_FINAL.pdf",
        "used_for": "Targets: 80 % screening participation in every county; at least 70 % of cancers found at stage I–II; patients treated within target times (Goal 3).",
    },
    "ROADMAP": {
        "title": "Toimeenpanon tiekartta 2026–2031 (Implementation roadmap)",
        "publisher": "Ministry of Social Affairs and Health, 2026",
        "url": "https://files.hackjunction.app/resources/Kansallinen_syopastrategia_toimeenpanon_tiekartta_2026_2031.pdf",
        "used_for": "The national measures each proposal implements locally (measure number and page are given with the proposal).",
    },
    "THL2016": {
        "title": "Hoitoonpääsyn seuranta syövän hoidossa. Ohjaus 21/2016",
        "publisher": "Finnish Institute for Health and Welfare (THL), 2016",
        "url": "https://www.julkari.fi/handle/10024/131630",
        "used_for": "Defines which dates to record to monitor waiting times in cancer care. The strategy names it as the basis of the target times.",
    },
    "VIRTANEN2015": {
        "title": "Improving cervical cancer screening attendance in Finland",
        "publisher": "Virtanen A, Anttila A, Luostarinen T, Malila N, Nieminen P. Int J Cancer 2015;136:E677–84",
        "url": "https://doi.org/10.1002/ijc.29176",
        "used_for": "31 Finnish municipalities, 31 053 invitees: reminder letters raised participation from 72.6 % to 79.2 %; self-sampling for those still missing raised it to 82.2 %.",
    },
    "VIRTANEN2011": {
        "title": "Self-sample HPV tests as an intervention for nonattendees of cervical cancer screening in Finland: a randomized trial",
        "publisher": "Virtanen A, Nieminen P, Luostarinen T, Anttila A. Cancer Epidemiol Biomarkers Prev 2011;20:1960–9",
        "url": "https://pubmed.ncbi.nlm.nih.gov/21752985/",
        "used_for": "Randomised trial in Espoo among women who had not attended: 29.8 % took part with a self-sampling kit, 26.2 % with a reminder letter.",
    },
    "JENSEN2015": {
        "title": "Diagnostic intervals before and after implementation of cancer patient pathways",
        "publisher": "Jensen H, Tørring ML, Olesen F, Overgaard J, Fenger-Grøn M, Vedsted P. BMC Cancer 2015;15:308",
        "url": "https://doi.org/10.1186/s12885-015-1317-7",
        "used_for": "Denmark, 12 558 patients: the median time from first GP visit to diagnosis was 17 days shorter after standardised cancer pathways were introduced. Patients not referred through a pathway did not benefit.",
    },
}
for _key, _m in META.items():
    SOURCES[f"SOTKA-{_m['id']}"] = {
        "title": f"Sotkanet indicator {_m['id']}: {_m['title']}",
        "publisher": f"{_m['organization']}, published by THL in Sotkanet (data updated {_m['updated']})",
        "url": _m["url"],
        "used_for": LABELS.get(_key, (_m["title"],))[0],
    }

LIMITATIONS = [
    "No public indicator reports, by county, the time from referral or diagnosis to the first cancer treatment. The treatment-start stage therefore shows no number. This is the largest data gap, and closing it is one of the proposals.",
    "Waiting times in primary care and referral handling cover all patients, not only people with suspected cancer. They are used as a proxy for how fast a cancer suspicion can move.",
    "Five-year survival is age-standardised and based on patients diagnosed over ten years, so it changes slowly and reflects care given in the past. Sotkanet publishes no confidence intervals: treat differences of a few percentage points, and all figures for small counties, with caution.",
    "Stage at diagnosis is not available by county. Nationally, clinical notifications that include stage cover only about 40 % of cases (National Cancer Strategy, p. 30).",
    "Comparisons show where a county differs from others. They do not prove what causes the difference.",
    "Evidence for each proposal comes from the studies cited. Where a result is carried over from another setting (for example from cervical to bowel screening), the proposal says so.",
]


def src(key: str) -> str:
    return f"SOTKA-{META[key]['id']}"


# ------------------------------------------------------------------ numbers
@lru_cache(maxsize=None)
def latest_year(key: str) -> str:
    years = sorted({y for code, s in VALUES[key].items() if code != "FI" for y in s}, reverse=True)
    for y in years:
        if sum(1 for code, s in VALUES[key].items() if code != "FI" and y in s) >= MIN_COUNTIES:
            return y
    return years[0]


def value(key: str, code: str, year: str | None = None) -> float | None:
    cell = VALUES[key].get(code, {}).get(year or latest_year(key))
    return cell["v"] if cell else None


def count(key: str, code: str) -> int | None:
    cell = VALUES[key].get(code, {}).get(latest_year(key))
    return cell["n"] if cell else None


@lru_cache(maxsize=None)
def spread(key: str) -> dict:
    y = latest_year(key)
    vals = {c: value(key, c, y) for c in COUNTIES}
    have = {c: v for c, v in vals.items() if v is not None}
    higher = META[key]["higher_is_better"]
    order = sorted(have, key=lambda c: have[c], reverse=higher)
    return {"year": int(y), "mean": statistics.mean(have.values()), "sd": statistics.pstdev(have.values()) or 1.0,
            "min": min(have.values()), "max": max(have.values()), "n": len(have), "order": order,
            "best": {"code": order[0], "name": COUNTIES[order[0]]["name"], "value": have[order[0]]},
            "national": value(key, "FI", y)}


def indicator(key: str, code: str) -> dict:
    sp = spread(key)
    v = value(key, code)
    higher = META[key]["higher_is_better"]
    label, unit = LABELS[key]
    out: dict[str, Any] = {
        "key": key, "label": label, "unit": unit, "year": sp["year"], "value": v, "national": sp["national"],
        "best": sp["best"], "range": [sp["min"], sp["max"]], "counties": sp["n"], "higher_is_better": higher,
        "source": src(key), "proxy": key in PROXY,
    }
    if v is None:
        return {**out, "missing": True}
    z = (v - sp["mean"]) / sp["sd"]
    out["worse_by_sd"] = round(-z if higher else z, 2)  # positive = worse than the average county
    out["rank"] = sp["order"].index(code) + 1  # 1 = best
    if sp["national"] is not None:
        out["vs_national"] = round(v - sp["national"], 1)
    if META[key]["stage"] == "detection":
        out["target"] = SCREENING_TARGET
        out["to_target"] = round(max(0.0, SCREENING_TARGET - v), 1)
    series = VALUES[key].get(code, {})
    out["trend"] = [{"year": int(y), "value": series[y]["v"]} for y in sorted(series)][-8:]
    past = [p for p in out["trend"] if p["year"] <= sp["year"] - 2]
    if past:
        delta = round(v - past[-1]["value"], 1)
        out["change"] = {"since": past[-1]["year"], "delta": delta, "worse": (delta < 0) if higher else (delta > 0)}
    n = count(key, code)
    if n and v:
        total = n / (v / 100)
        if META[key]["stage"] == "detection":
            out["people"] = {"attended": n, "invited": round(total), "more_to_reach_target": max(0, math.ceil(total * SCREENING_TARGET / 100 - n))}
        elif key == "gp_wait_7d":
            out["people"] = {"waited_over_7_days": n, "visits": round(total)}
        elif key == "referral_3d":
            out["people"] = {"within_3_days": n, "referrals": round(total), "more_to_match_best": max(0, math.ceil(total * sp["best"]["value"] / 100 - n))}
    return out


STAGE_KEYS = {
    "detection": ["screen_cervical", "screen_breast", "screen_bowel"],
    "primary": ["gp_wait_7d"],
    "referral": ["referral_3d", "wait_6m"],
    "outcome": ["surv_bowel", "surv_cervical", "surv_breast"],
}


def _population(code: str) -> int | None:
    v = value("population", code)
    return int(v) if v else None


@lru_cache(maxsize=None)
def county(code: str) -> dict:
    if code not in COUNTIES:
        raise KeyError(code)
    c = COUNTIES[code]
    pop = _population(code)
    stages = []
    for s in STAGES:
        if s["id"] == "treatment":
            stages.append({**s, "indicators": [], "score": None, "status": "not_measured",
                           "note": "Not published by county. The national strategy sets this as a Goal 3 indicator, but no public data shows whether patients here start treatment within the target times."})
            continue
        inds = [indicator(k, code) for k in STAGE_KEYS[s["id"]]]
        scored = [i["worse_by_sd"] for i in inds if "worse_by_sd" in i]
        score = round(statistics.mean(scored), 2) if scored else None
        status = "no_data" if score is None else "behind" if score >= STANDS_OUT else "ahead" if score <= -STANDS_OUT else "typical"
        stages.append({**s, "indicators": inds, "score": score, "status": status})
    by_id = {s["id"]: s for s in stages}
    care = [s for s in stages if s["id"] in ("detection", "primary", "referral") and s["score"] is not None]
    worst = max(care, key=lambda s: s["score"], default=None)
    gap_stage = worst["id"] if worst and worst["score"] >= STANDS_OUT else None
    below_target = [i for i in by_id["detection"]["indicators"] if i.get("to_target", 0) > 0]
    warnings = [{"label": i["label"], "stage": s["id"], "source": i["source"], **i["change"]}
                for s in stages for i in s["indicators"] if i.get("change", {}).get("worse") and abs(i["change"]["delta"]) >= 3]
    out = {
        "code": code, "name": c["name"], "name_fi": c["name_fi"], "population": pop,
        "small": bool(pop and pop < SMALL_POPULATION),
        "stages": stages, "gap_stage": gap_stage,
        "context": [indicator(k, code) for k in ("travel_barrier", "smokers", "hpv_girls")],
        "warnings": warnings,
    }
    out["headline"] = _headline(out, by_id, below_target)
    out["proposals"] = _proposals(out, by_id)
    return out


def _fmt(v: float | None, unit: str = "%") -> str:
    if v is None:
        return "no data"
    s = f"{v:.1f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)
    return f"{s} %" if unit == "%" else f"{s} {unit}"


def _headline(c: dict, by_id: dict, below_target: list[dict]) -> dict:
    gs = c["gap_stage"]
    if gs:
        worst = max((i for i in by_id[gs]["indicators"] if "worse_by_sd" in i), key=lambda i: i["worse_by_sd"])
        text = (f"In {c['name']} the gap opens at {STAGE_NAME[gs].lower()}: {worst['label'].lower()} is {_fmt(worst['value'], worst['unit'])}, "
                f"against {_fmt(worst['national'], worst['unit'])} nationally and {_fmt(worst['best']['value'], worst['unit'])} in {worst['best']['name']}.")
        key = worst["key"]
    elif below_target:
        worst = max(below_target, key=lambda i: i["to_target"])
        text = (f"{c['name']} is close to or better than the average county at every measured stage. The remaining gap is the national one: "
                f"{worst['label'].lower()} is {_fmt(worst['value'])}, below the 80 % target.")
        key = worst["key"]
    else:
        text, key = f"{c['name']} meets the screening target and is close to or better than the average county at every measured stage.", None
    return {"text": text, "stage": gs, "indicator": key,
            "unmeasured": "Whether patients start treatment within the target time is not published for any county."}


# ------------------------------------------------------------------ proposals
def _reminder(c: dict, i: dict) -> dict:
    prog = {"screen_cervical": "cervical", "screen_breast": "breast", "screen_bowel": "bowel"}[i["key"]]
    p = i.get("people") or {}
    more = p.get("more_to_reach_target")
    kit = {"cervical": "Post an HPV self-sampling kit to those who still have not attended six weeks later.",
           "bowel": "Post a second test kit, with a text message the week before, to those who have not returned the first one after six weeks.",
           "breast": "Phone those who still have not attended six weeks later and offer evening or Saturday times."}[prog]
    transfer = None if prog == "cervical" else (
        "The cited trials are from cervical screening in Finland. Using the same reminder steps in "
        f"{prog} screening is an assumption: measure the effect from the first invitation round.")
    return {
        "id": f"reminder-{prog}", "stage": "detection",
        "title": f"Second-chance invitations in {prog} screening",
        "why": (f"{i['label']} is {_fmt(i['value'])} ({i['year']}), {i['to_target']:g} points below the 80 % target."
                + (f" About {more:,} more people a year would need to take part." if more else "")),
        "owner": "County screening coordinator, with the screening provider",
        "steps": [
            "Take the list of people who did not attend from the screening provider after each invitation round.",
            "Send a reminder letter with a pre-booked time (or a kit already enclosed) within four weeks.",
            kit,
            "Write invitations and reminders in the main languages spoken in the county, not only Finnish and Swedish.",
        ],
        "first_90_days": "Agree the reminder steps in the screening contract, and run them for one invitation round.",
        "kpi": {"indicator": i["label"], "now": i["value"], "target": SCREENING_TARGET, "unit": "%", "source": i["source"],
                "review": "After each invitation round; published yearly in Sotkanet."},
        "size": {"people": more, "text": f"{more:,} more participants a year closes the gap to 80 %" if more else None},
        "evidence": ["VIRTANEN2015", "VIRTANEN2011"],
        "strategy": {"measure": "2.4.1 and 1.3.1", "page": "pp. 33 and 47–48", "source": "ROADMAP",
                     "text": "At least 80 % participation in every programme in every county; equal participation across language groups."},
        "assumption": transfer,
    }


def _fast_lane(c: dict, i: dict) -> dict:
    p = i.get("people") or {}
    return {
        "id": "fast-lane", "stage": "primary",
        "title": "A fast lane for suspected cancer at the health centre",
        "why": (f"{_fmt(i['value'])} of non-urgent doctor visits waited over 7 days ({i['year']}), against {_fmt(i['national'])} nationally "
                f"and {_fmt(i['best']['value'])} in {i['best']['name']}."
                + (f" That was {p['waited_over_7_days']:,} visits." if p.get("waited_over_7_days") else "")
                + " A cancer suspicion that starts in this queue starts late."),
        "owner": "Chief physician of each health centre",
        "steps": [
            "Add a short alarm-symptom checklist to the first contact (phone, digital, desk): for example blood in stool or urine, a new lump, unexplained weight loss, a cough lasting over three weeks in a smoker.",
            "Book anyone who meets the checklist with a doctor within three working days, outside the normal queue.",
            "Give the doctor direct access to first tests (blood count, FIT, chest X-ray, ultrasound) without a specialist referral.",
            "Name one nurse per health centre who follows these patients until a referral is sent or cancer is ruled out.",
        ],
        "first_90_days": "Pilot in two health centres. Count the patients flagged, days to the doctor, and referrals sent.",
        "kpi": {"indicator": "Days from first contact to a doctor for patients flagged by the checklist", "now": None, "target": 3, "unit": "working days",
                "source": None, "review": "Monthly, from the appointment system. The public indicator to watch alongside it is the share of visits waiting over 7 days."},
        "size": {"people": p.get("waited_over_7_days"), "text": None},
        "evidence": ["JENSEN2015"],
        "strategy": {"measure": "2.5.1", "page": "pp. 50–51", "source": "ROADMAP",
                     "text": "Improve access to primary care so that a cancer diagnosis is not delayed unnecessarily."},
        "assumption": "The public waiting-time figure covers all patients. How long people with alarm symptoms wait is not published, so the pilot must measure it.",
    }


def _referral(c: dict, i: dict, w: dict) -> dict:
    p = i.get("people") or {}
    more = p.get("more_to_match_best")
    return {
        "id": "referral", "stage": "referral",
        "title": "Standard referral criteria and same-week handling for suspected cancer",
        "why": (f"{_fmt(i['value'])} of referrals to specialist care were processed within 3 days ({i['year']}), against {_fmt(i['national'])} nationally "
                f"and {_fmt(i['best']['value'])} in {i['best']['name']}."
                + (f" {_fmt(w['value'], 'per 10 000')} residents waited over six months for specialist care (national {_fmt(w['national'], 'per 10 000')})." if w.get("value") is not None else "")),
        "owner": "County medical director, with the central hospital's cancer unit",
        "steps": [
            "Adopt the national referral criteria for the five most common cancers as soon as they are published, and build them into the referral form.",
            "Mark referrals that meet the criteria as 'suspected cancer' so they are read by a specialist within one working day.",
            "Book the first tests at the moment the referral is accepted, not after the first visit.",
            "Return referrals that lack required information within one day, with a list of what is missing.",
        ],
        "first_90_days": "Start with bowel and lung cancer referrals from all health centres to the central hospital.",
        "kpi": {"indicator": i["label"], "now": i["value"], "target": i["best"]["value"], "unit": "%", "source": i["source"],
                "review": f"Monthly from the hospital's referral system; target is the level of the best county ({i['best']['name']})."},
        "size": {"people": more, "text": f"{more:,} more referrals a year handled within 3 days would match {i['best']['name']}" if more else None},
        "evidence": ["JENSEN2015"],
        "strategy": {"measure": "2.5.2", "page": "pp. 52–53", "source": "ROADMAP",
                     "text": "Standardised referral criteria in national care pathways, modelled on the Danish, Swedish and Norwegian cancer pathways."},
        "assumption": "The public referral figures cover all specialties. The Danish result is from Denmark's health system and may not carry over in full.",
    }


def _measure(c: dict) -> dict:
    return {
        "id": "measure-treatment", "stage": "treatment",
        "title": "Start measuring time to treatment, and publish it",
        "why": ("No public data shows whether patients in any county start cancer treatment within the target time, although this is a Goal 3 indicator "
                "of the national strategy. Without it, a delay in this county cannot be seen, compared or fixed."),
        "owner": "County medical director, with the cancer unit and the patient-record team",
        "steps": [
            "Record four dates for every new cancer patient, as THL's guide defines them: referral arrived, first specialist visit, treatment decision, first treatment.",
            "Report the median and the share within target every month, by cancer type and by the patient's home municipality.",
            "Review every case that exceeds the target in the cancer unit's monthly meeting, and record the cause of the delay.",
            "Share the figures with the national cancer centre (FICAN) so counties can be compared.",
        ],
        "first_90_days": "Agree the four date fields with the patient-record team and report the first month for breast, bowel and lung cancer.",
        "kpi": {"indicator": "Share of new cancer patients with all four dates recorded", "now": None, "target": 95, "unit": "%",
                "source": None, "review": "Monthly. Once coverage is reliable, the KPI becomes the share of patients treated within the national target time."},
        "size": {"people": None, "text": None},
        "evidence": ["THL2016", "STRATEGY"],
        "strategy": {"measure": "3.1.2 and 3.1.4", "page": "pp. 56–58", "source": "ROADMAP",
                     "text": "Cancer-specific target times are to be defined in 2026–2028. Indicator: patients are treated within the target times."},
        "assumption": None,
    }


def _proposals(c: dict, by_id: dict) -> list[dict]:
    ranked: list[tuple[float, dict]] = []
    for i in by_id["detection"]["indicators"]:
        if i.get("to_target", 0) > 0:
            ranked.append((i["to_target"], _reminder(c, i)))
    gp = by_id["primary"]["indicators"][0]
    if "worse_by_sd" in gp and gp["worse_by_sd"] > 0:
        ranked.append((by_id["primary"]["score"] + 0.5, _fast_lane(c, gp)))
    ref, wait = by_id["referral"]["indicators"]
    if "worse_by_sd" in ref and ref["rank"] > 1 and by_id["referral"]["score"] > 0:
        ranked.append((by_id["referral"]["score"] + 0.5, _referral(c, ref, wait)))
    gs = c["gap_stage"]
    ranked = sorted(ranked, key=lambda t: (t[1]["stage"] != gs, -t[0]))
    props = [p for _, p in ranked]
    # Keep one screening programme near the top; the rest follow.
    first_detection = next((p for p in props if p["stage"] == "detection"), None)
    head = [p for p in props if p["stage"] != "detection" or p is first_detection][:2]
    rest = [p for p in props if p not in head]
    out = head + [_measure(c)] + rest
    for n, p in enumerate(out, 1):
        p["priority"] = n
    return out


# ------------------------------------------------------------------ national view
@lru_cache(maxsize=None)
def overview() -> dict:
    rows = []
    for code in COUNTIES:
        c = county(code)
        rows.append({"code": code, "name": c["name"], "population": c["population"], "small": c["small"], "gap_stage": c["gap_stage"],
                     "scores": {s["id"]: s["score"] for s in c["stages"]},
                     "values": {i["key"]: i.get("value") for s in c["stages"] for i in s["indicators"]},
                     "headline": c["headline"]["text"]})
    findings = []
    for key in ("screen_bowel", "screen_cervical", "screen_breast"):
        sp = spread(key)
        hit = sum(1 for c in COUNTIES if (value(key, c) or 0) >= SCREENING_TARGET)
        findings.append({"stage": "detection", "source": src(key), "key": key,
                         "text": f"{LABELS[key][0]}: {hit} of {sp['n']} counties reach the 80 % target ({sp['year']}). Range {_fmt(sp['min'])} to {_fmt(sp['max'])}."})
    sp = spread("gp_wait_7d")
    findings.append({"stage": "primary", "source": src("gp_wait_7d"), "key": "gp_wait_7d",
                     "text": f"Share of non-urgent health centre doctor visits that waited over 7 days: from {_fmt(sp['min'])} to {_fmt(sp['max'])} depending on the county ({sp['year']})."})
    sp = spread("referral_3d")
    findings.append({"stage": "referral", "source": src("referral_3d"), "key": "referral_3d",
                     "text": f"Referrals processed within 3 days: from {_fmt(sp['min'])} to {_fmt(sp['max'])} ({sp['year']}, {sp['n']} counties report)."})
    findings.append({"stage": "treatment", "source": "STRATEGY", "key": None,
                     "text": "Time from diagnosis to first treatment: not published for any county. The largest data gap in the pathway."})
    sp = spread("surv_bowel")
    findings.append({"stage": "outcome", "source": src("surv_bowel"), "key": "surv_bowel",
                     "text": f"Bowel cancer five-year survival: from {_fmt(sp['min'])} to {_fmt(sp['max'])} between counties (national {_fmt(sp['national'])}, {sp['year']})."})
    gaps = {s["id"]: sum(1 for r in rows if r["gap_stage"] == s["id"]) for s in STAGES}
    return {"built": DATA["built"], "stages": STAGES, "counties": rows, "findings": findings, "gap_counts": gaps,
            "labels": {k: {"label": v[0], "unit": v[1], "higher_is_better": META[k]["higher_is_better"], "source": src(k), "year": spread(k)["year"],
                           "national": spread(k)["national"], "stage": META[k]["stage"]} for k, v in LABELS.items()},
            "target": SCREENING_TARGET}


def sources() -> dict:
    return {"built": DATA["built"], "sources": SOURCES, "limitations": LIMITATIONS,
            "method": [
                "For each indicator the latest year reported by at least 15 counties is used.",
                "A county's distance from the others is its value minus the mean of all counties, divided by their standard deviation. A stage's score is the mean of its indicators.",
                f"A stage is named as the county's gap when its score is at least {STANDS_OUT} standard deviations worse than the average county, and it is the worst of the three measured care stages.",
                "People counts are arithmetic on published counts: invited = attended ÷ participation rate; needed = invited × 80 % − attended.",
                "A change is flagged as an early warning when an indicator has worsened by 3 points or more against the value two or more years earlier.",
            ]}


# ------------------------------------------------------------------ claim check
_NUM = re.compile(r"(?<![\w.,])(\d{1,3}(?:[ ,  ]\d{3})+|\d+(?:[.,]\d+)?)(?![\w]|[.,]\d)")


def _facts(code: str) -> set[float]:
    """Every number a brief about this county may legitimately contain."""
    found: set[float] = set()

    def walk(x: Any) -> None:
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            found.add(round(float(x), 1))
            found.add(float(round(x)))
        elif isinstance(x, str):
            for m in _NUM.findall(x):
                try:
                    found.add(round(float(re.sub(r"[ ,  ](?=\d{3})", "", m).replace(",", ".")), 1))
                except ValueError:
                    pass
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(county(code))
    walk(SOURCES)
    walk(overview()["findings"])
    return found


def verify(code: str, text: str) -> dict:
    """Check each figure in a written brief against the county's data. Small counters and list numbers are ignored."""
    facts = _facts(code)
    checked, unmatched = 0, []
    for line in text.splitlines():
        body = re.sub(r"^\s*(?:[-*#>]+|\d+[.)])\s*", "", line)  # list markers are not figures
        for m in _NUM.finditer(body):
            raw = m.group(1)
            try:
                n = float(re.sub(r"[ ,  ](?=\d{3})", "", raw).replace(",", "."))
            except ValueError:
                continue
            if n < 10 and "." not in raw and "," not in raw:
                continue  # "3 days", "two health centres": wording, not data
            checked += 1
            if round(n, 1) not in facts and float(round(n)) not in facts:
                unmatched.append(raw)
    return {"checked": checked, "matched": checked - len(unmatched), "unmatched": unmatched}


# ------------------------------------------------------------------ agent tools
def _code(ctx: dict, args: dict) -> str:
    raw = str(args.get("county") or ctx.get("county") or "").strip()
    if raw in COUNTIES:
        return raw
    low = raw.lower()
    for code, c in COUNTIES.items():
        if low and (low == c["name"].lower() or low in c["name"].lower() or low in c["name_fi"].lower()):
            return code
    return ctx.get("county") if ctx.get("county") in COUNTIES else "20"


def _slim(i: dict) -> dict:
    keep = ("label", "unit", "year", "value", "national", "best", "rank", "counties", "vs_national", "target", "to_target", "people", "change", "source", "missing")
    return {k: i[k] for k in keep if k in i}


def _summary(c: dict) -> dict:
    return {"key_finding": c["headline"]["text"], "priority_order": [p["title"] for p in c["proposals"][:3]]}


def _stage_tool(stage: str):
    def run(ctx: dict, args: dict) -> dict:
        c = county(_code(ctx, args))
        s = next(x for x in c["stages"] if x["id"] == stage)
        out: dict[str, Any] = {"county": c["name"], "stage": s["name"], "status": s["status"], "indicators": [_slim(i) for i in s["indicators"]]}
        if s.get("note"):
            out["note"] = s["note"]
        out["proposals"] = [{k: p[k] for k in ("priority", "title", "why", "owner", "steps", "first_90_days", "kpi", "evidence", "strategy", "assumption")}
                            for p in c["proposals"] if p["stage"] == stage]
        out["county_summary"] = _summary(c)
        if c["small"]:
            out["caution"] = "Small county: figures vary from year to year."
        return out
    return run


def _outcomes(ctx: dict, args: dict) -> dict:
    c = county(_code(ctx, args))
    s = next(x for x in c["stages"] if x["id"] == "outcome")
    t = next(x for x in c["stages"] if x["id"] == "treatment")
    return {"county": c["name"], "outcome": [_slim(i) for i in s["indicators"]], "treatment_start": {"status": "not measured", "note": t["note"], "source": "STRATEGY"},
            "proposals": [{k: p[k] for k in ("priority", "title", "why", "owner", "steps", "first_90_days", "kpi", "evidence", "strategy")} for p in c["proposals"] if p["stage"] == "treatment"],
            "county_summary": _summary(c),
            "caution": "Survival reflects patients diagnosed over ten years and has no published confidence interval. Do not claim a cause."}


FUNCS = {"detection_gap": _stage_tool("detection"), "primary_care_gap": _stage_tool("primary"), "referral_gap": _stage_tool("referral"), "treatment_and_outcomes": _outcomes}

_ARGS = {"type": "object", "properties": {"county": {"type": "string", "description": "County name or code. Leave empty to use the county selected in the app."}}}
TOOL_SPECS = [
    {"type": "function", "function": {"name": "detection_gap", "description": "Screening participation in the county against the 80 % target, with the people counts and the proposals for this stage.", "parameters": _ARGS}},
    {"type": "function", "function": {"name": "primary_care_gap", "description": "Waiting for a health centre doctor in the county against other counties, with the proposal for this stage.", "parameters": _ARGS}},
    {"type": "function", "function": {"name": "referral_gap", "description": "Referral handling and waiting for specialist care in the county, with the proposal for this stage.", "parameters": _ARGS}},
    {"type": "function", "function": {"name": "treatment_and_outcomes", "description": "Five-year survival in the county, and what is known (and not known) about time to treatment.", "parameters": _ARGS}},
]
NAMES = set(FUNCS)


def run(name: str, args: dict, ctx: dict) -> dict:
    return FUNCS[name](ctx or {}, args or {})


# ------------------------------------------------------------------ agent team
ROLES: dict[str, dict[str, Any]] = {
    "detection": {
        "title": "Early detection",
        "brief": "Analyses screening participation in the county. Call detection_gap first and use only its figures.",
        "tools": ["detection_gap"], "must": "detection_gap",
        "report": "### Finding\nTwo sentences with the figures, the year and the 80 % target.\n### Proposals\nFor each proposal from the tool: title, owner, the steps as a numbered list, the KPI (now → target).\n### Evidence and limits\nBullets: the source ids in square brackets, and any assumption the tool states.",
    },
    "access": {
        "title": "Primary care and referral",
        "brief": "Analyses how fast a cancer suspicion can move from the health centre into specialist care. Call primary_care_gap, then referral_gap, and use only their figures.",
        "tools": ["primary_care_gap", "referral_gap"], "must": "primary_care_gap",
        "report": "### Finding\nTwo or three sentences with the figures, the year, the national value and the best county.\n### Proposals\nFor each proposal from the tools: title, owner, the steps as a numbered list, the KPI (now → target).\n### Evidence and limits\nBullets: the source ids in square brackets; say that the figures cover all patients, not only cancer.",
    },
    "treatment": {
        "title": "Treatment and outcomes",
        "brief": "Reports survival in the county and the missing data on time to treatment. Call treatment_and_outcomes first and use only its figures.",
        "tools": ["treatment_and_outcomes"], "must": "treatment_and_outcomes",
        "report": "### Finding\nSurvival figures against the national value, in one table: cancer | county | Finland | best county.\n### What is not measured\nOne sentence on time to treatment.\n### Proposal\nTitle, owner, the steps as a numbered list, the KPI.\n### Evidence and limits\nBullets with source ids in square brackets.",
    },
}

COMMON = """Current local time: {now}. You analyse cancer care in one Finnish wellbeing services county for its decision-makers.
Use the tools for every figure: never state a number, a year, a county name or a source that a tool did not return.
Cite sources by the ids the tools give, in square brackets, for example [SOTKA-6411] or [ROADMAP].
End your report with two lines copied exactly from county_summary: "County key finding: <key_finding>" and "Priority order: <priority_order>".
Write plainly for a non-technical reader. Do not claim that one thing causes another: the data shows differences, not causes."""

SYNTH = """You are the lead analyst. Write the COUNTY BRIEF for the county's director of health services, from the reports below.
It must be readable in under two minutes. Use exactly these sections:
**Key message** — restate the "County key finding" line from the reports, in at most two sentences. Do not replace it with another finding.
**Do first** — a numbered list of the three actions in the "Priority order" line, in that order. Each: what to do in one sentence, who owns it, and the KPI (now → target).
**What we cannot see** — one sentence on the missing data on time to treatment.
**Sources** — one line listing the source ids used, in square brackets.
Use the figures from the reports exactly. Do not calculate new figures. Do not add figures, causes or sources that are not in the reports. Current local time: {now}."""

PROFILE = {
    "label": "Cancer pathway analysts",
    "lead": "lead analyst for cancer care in a Finnish wellbeing services county",
    "roles": ROLES,
    "plan_extra": "",
    "fixed": [
        {"role": "detection", "task": "Report the screening gap in the selected county and the proposals to close it."},
        {"role": "access", "task": "Report waiting in primary care and referral handling in the selected county, and the proposals."},
        {"role": "treatment", "task": "Report survival in the selected county and what is not measured about time to treatment."},
    ],
    "fallback": [
        {"role": "detection", "task": "Report the screening gap in the selected county and the proposals to close it."},
        {"role": "access", "task": "Report waiting in primary care and referral handling in the selected county, and the proposals."},
    ],
    "synth": SYNTH,
    "review": False,
}


# ------------------------------------------------------------------ HTTP
api = APIRouter(prefix="/api/pathway", tags=["pathway"])


@api.get("/overview")
async def http_overview():
    return overview()


@api.get("/geo")
async def http_geo():
    return DATA["geo"]


@api.get("/county/{code}")
async def http_county(code: str):
    if code not in COUNTIES:
        raise HTTPException(404, "unknown county")
    return county(code)


@api.get("/sources")
async def http_sources():
    return sources()


class VerifyBody(BaseModel):
    county: str
    text: str


@api.post("/verify")
async def http_verify(body: VerifyBody):
    if body.county not in COUNTIES:
        raise HTTPException(404, "unknown county")
    return verify(body.county, body.text)
