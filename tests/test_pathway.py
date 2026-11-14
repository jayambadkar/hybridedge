from hybridedge import pathway


def test_every_county_has_a_headline_and_sourced_proposals():
    for code in pathway.COUNTIES:
        c = pathway.county(code)
        assert c["headline"]["text"]
        assert any(p["stage"] == "treatment" for p in c["proposals"])  # the data gap is always addressed
        for p in c["proposals"]:
            assert p["owner"] and p["steps"] and p["kpi"]["indicator"]
            assert all(e in pathway.SOURCES for e in p["evidence"])


def test_people_needed_to_reach_the_screening_target():
    i = pathway.indicator("screen_cervical", "14")
    people = i["people"]
    assert round(people["attended"] / people["invited"] * 100, 1) == i["value"]
    assert (people["attended"] + people["more_to_reach_target"]) / people["invited"] >= 0.8


def test_claim_check_flags_figures_that_are_not_in_the_data():
    c = pathway.county("14")
    v = c["stages"][0]["indicators"][0]["value"]
    assert pathway.verify("14", f"Participation is {v} %.")["unmatched"] == []
    assert pathway.verify("14", "Participation is 512.34 %.")["unmatched"] == ["512.34"]
