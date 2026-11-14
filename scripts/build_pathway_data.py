"""Build the data snapshot behind the cancer pathway app (web/pathway/).

Everything comes from public, aggregate sources. No patient-level data.

  Sotkanet (THL indicator bank, open REST API)   indicator values by wellbeing services county
  Statistics Finland (geo.stat.fi, open WFS)     county boundaries, 1:4 500 000

    uv run python scripts/build_pathway_data.py

Writes hybridedge/pathway_data.json. The app never calls these services at run time, so it
works without a network; re-run this script to refresh the numbers.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import httpx

SOTKA = "https://sotkanet.fi/rest/1.1"
WFS = ("https://geo.stat.fi/geoserver/wfs?service=WFS&version=2.0.0&request=GetFeature"
       "&typeName=tilastointialueet:hyvinvointialue4500k&outputFormat=application/json&srsName=EPSG:4326")
OUT = Path(__file__).resolve().parents[1] / "hybridedge" / "pathway_data.json"
YEARS = range(2015, 2027)
WHOLE_COUNTRY = 658

# id -> (key, stage, True if a higher value is better)
INDICATORS = {
    3620: ("screen_cervical", "detection", True),
    3621: ("screen_breast", "detection", True),
    6103: ("screen_bowel", "detection", True),
    6411: ("gp_wait_7d", "primary", False),
    13: ("referral_3d", "referral", True),
    3336: ("wait_6m", "referral", False),
    3625: ("surv_breast", "outcome", True),
    3626: ("surv_cervical", "outcome", True),
    3627: ("surv_bowel", "outcome", True),
    5201: ("travel_barrier", "context", False),
    4404: ("smokers", "context", False),
    3869: ("hpv_girls", "context", True),
    127: ("population", "context", True),
}


def strip(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()


def simplify(coords, places=3):
    """Round coordinates and drop repeated points: enough for a small overview map."""
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], places), round(coords[1], places)]
    out = [simplify(c, places) for c in coords]
    if out and isinstance(out[0][0], (int, float)):
        dedup = [out[0]]
        for p in out[1:]:
            if p != dedup[-1]:
                dedup.append(p)
        return dedup
    return out


def main() -> None:
    with httpx.Client(timeout=90, headers={"User-Agent": "pathway-snapshot"}) as c:
        regions = c.get(f"{SOTKA}/regions").json()
        counties = {r["id"]: r for r in regions if r["category"] == "HYVINVOINTIALUE"}
        meta, values = {}, {}
        for iid, (key, stage, higher) in INDICATORS.items():
            m = c.get(f"{SOTKA}/indicators/{iid}").json()
            meta[key] = {
                "id": iid, "stage": stage, "higher_is_better": higher,
                "title": m["title"]["en"], "title_fi": m["title"]["fi"],
                "organization": m["organization"]["title"]["en"],
                "updated": m.get("data-updated"),
                "description": strip(m["description"].get("en") or m["description"].get("fi"))[:1200],
                "url": f"https://sotkanet.fi/sotkanet/en/metadata/indicators/{iid}",
            }
            q = "&".join(f"years={y}" for y in YEARS)
            rows = c.get(f"{SOTKA}/json?indicator={iid}&{q}&genders=total").json()
            series: dict[str, dict[str, dict]] = {}
            for r in rows:
                if r["region"] in counties or r["region"] == WHOLE_COUNTRY:
                    code = "FI" if r["region"] == WHOLE_COUNTRY else counties[r["region"]]["code"]
                    if r.get("value") is not None:
                        series.setdefault(code, {})[str(r["year"])] = {"v": r["value"], "n": r.get("absValue")}
            values[key] = series
        geo = c.get(WFS).json()

    features = [{"code": f["properties"]["hyvinvointialue"], "geometry": {"type": f["geometry"]["type"], "coordinates": simplify(f["geometry"]["coordinates"])}}
                for f in geo["features"]]
    snapshot = {
        "built": dt.date.today().isoformat(),
        "counties": sorted(({"code": r["code"], "name": r["title"]["en"].replace(" wellbeing services county", ""), "name_fi": r["title"]["fi"]} for r in counties.values()), key=lambda x: x["code"]),
        "indicators": meta,
        "values": values,
        "geo": features,
    }
    OUT.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB), {len(snapshot['counties'])} counties, {len(meta)} indicators")


if __name__ == "__main__":
    main()
