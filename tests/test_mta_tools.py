"""The MTA tools are deterministic geometry: same reports in, same plan out."""
from hybridedge import mta_tools as T

PLACES = [
    {"name": "Narinkka Square", "lat": 60.16967, "lon": 24.93493},
    {"name": "Railway Square", "lat": 60.17038, "lon": 24.93985},
    {"name": "Helsinki Central Station", "lat": 60.17155, "lon": 24.94059},
    {"name": "Senate Square", "lat": 60.16951, "lon": 24.95227},
]
REPORTS = [
    {"id": "r1", "type": "Attack", "lat": 60.16962, "lon": 24.93455, "attackers": 2, "weapon": "knife", "casualties": 4, "t": 0},
    {"id": "r2", "type": "Sighting", "lat": 60.17012, "lon": 24.93722, "attackers": 2, "weapon": "firearm", "casualties": 2, "t": 180},
    {"id": "r3", "type": "Shots fired", "lat": 60.17058, "lon": 24.93928, "attackers": 2, "weapon": "firearm", "casualties": 3, "t": 360},
]


def ctx(n=3, ambulances=3):
    return {"reports": REPORTS[:n], "places": PLACES, "resources": {"arv": 4, "ambulance": ambulances}}


def test_empty_context_is_safe():
    for fn in (T.analyse_movement, T.set_zones, T.plan_deployment, T.check_plan):
        assert "summary" in fn({"reports": [], "places": PLACES})


def test_movement_tracks_reports_in_order():
    mv = T.analyse_movement(ctx())
    assert mv["reports"] == 3
    assert "firearm" in mv["weapons"]


def test_zones_are_nested():
    z = T.set_zones(ctx())
    assert z["hot"]["radius_m"] < z["warm"]["radius_m"] < z["cold"]["radius_m"]
    assert z["rvp"]["near"] and z["ccp"]["near"], "key points are named after real places"


def test_deterministic():
    assert T.plan_deployment(ctx()) == T.plan_deployment(ctx())


def test_check_plan_flags_ambulance_shortfall():
    issues = T.check_plan(ctx(ambulances=1))["issues"]
    assert any(i["owner"] == "casualties" and i["severity"] == "high" for i in issues)
