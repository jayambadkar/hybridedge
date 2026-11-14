from hybridedge.store import short_title


def test_short_title_cuts_at_a_word():
    t = short_title("Tram derailment on Mannerheimintie, 2 carriages on their side, around 15 injured")
    assert t.endswith("…") and not t.endswith("sid…")
    assert short_title("Short one") == "Short one"
