"""The benchmark scorers: GSM8K answer extraction and BFCL AST matching."""
from bench.models import bfcl_correct, gsm8k_extract


def test_gsm8k_extracts_final_answer():
    assert gsm8k_extract("3 + 4 = 7\nAnswer: 1,250") == 1250
    assert gsm8k_extract("so the total is \\boxed{18}") == 18


def test_bfcl_accepts_any_allowed_value_and_optional_args():
    gt = [{"calc_area": {"base": [10], "height": [5], "unit": ["units", ""]}}]
    assert bfcl_correct([{"name": "calc_area", "args": {"base": 10, "height": 5}}], gt)
    assert bfcl_correct([{"name": "calc_area", "args": {"base": 10.0, "height": 5, "unit": "Units"}}], gt)


def test_bfcl_rejects_wrong_or_extra_args():
    gt = [{"calc_area": {"base": [10], "height": [5]}}]
    assert not bfcl_correct([{"name": "calc_area", "args": {"base": 11, "height": 5}}], gt)
    assert not bfcl_correct([{"name": "calc_area", "args": {"base": 10, "height": 5, "colour": "red"}}], gt)


def test_bfcl_parallel_calls_match_in_any_order():
    gt = [{"play": {"artist": ["A"]}}, {"play": {"artist": ["B"]}}]
    calls = [{"name": "play", "args": {"artist": "B"}}, {"name": "play", "args": {"artist": "A"}}]
    assert bfcl_correct(calls, gt)
    assert not bfcl_correct(calls[:1], gt)
