"""Regression tests for POD's six 0.5.0 scoring points (fixtures in tests/fixtures/pod_*.json)."""
import copy
import json
import pathlib

import pytest

import server

FIX = pathlib.Path(__file__).parent / "fixtures"


def load(name):
    d = json.loads((FIX / f"pod_{name}.json").read_text())
    return {k: d[k] for k in ("goal", "trace", "outcome")}


def run(args):
    return server.score_trace(args["goal"], args["trace"], args["outcome"])


def codes(r):
    return [i["code"] for i in r["issues"]]


def fixes(r):
    return [s["id"] for s in r["suggestions"]]


def test_point1_incomplete_data_then_success():
    r = run(load("partial_success"))
    assert "conclusion_on_incomplete_data" in codes(r)
    assert "fix_report_incomplete" in fixes(r)
    assert r["dimensions"]["goal_alignment"] <= 40
    honest = load("partial_success") | {"outcome": "partial"}
    assert "conclusion_on_incomplete_data" not in codes(run(honest))


def test_point1_pagination_that_completes_is_not_flagged():
    args = {"goal": "data lookup", "outcome": "success",
            "trace": "action:list partial > action:list partial > action:list ok > verify passed"}
    assert "conclusion_on_incomplete_data" not in codes(run(args))


def test_point2_and_6_verify_kind_mismatch_gives_specific_fix():
    r = run(load("verify_kind"))
    assert "verify_kind_mismatch" in codes(r)
    assert "fix_add_verification" not in fixes(r)
    assert any(s.get("what_to_verify") for s in r["suggestions"])
    fitting = load("verify_kind")
    fitting["trace"] = copy.deepcopy(fitting["trace"])
    fitting["trace"][-1]["verify_kind"] = "script"
    r2 = run(fitting)
    assert "verify_kind_mismatch" not in codes(r2)
    assert r2["dimensions"]["goal_alignment"] > r["dimensions"]["goal_alignment"]


def test_point3_requirement_lowered_not_rewarded():
    r = run(load("requirement_lowered"))
    assert "requirement_lowered" in codes(r)
    plain = load("requirement_lowered")
    plain["trace"] = copy.deepcopy(plain["trace"])
    plain["trace"][2].pop("requirement_lowered")
    r_plain = run(plain)
    assert r["dimensions"]["error_recovery"] < r_plain["dimensions"]["error_recovery"]
    assert r["score"] < r_plain["score"]


def test_point4_subscores_move_across_fixtures():
    results = [run(load(n)) for n in ("high_stakes", "missing_verify", "partial_success",
                                      "requirement_lowered", "verify_kind")]
    for dim in ("goal_clarity", "observability", "efficiency"):
        assert len({r["dimensions"][dim] for r in results}) > 1, dim


def test_point5_high_stakes_errors_weigh_more():
    high = load("high_stakes") | {"outcome": "partial"}
    low = copy.deepcopy(high)
    low["trace"][1]["stakes"] = "low"
    r_high, r_low = run(high), run(low)
    assert r_high["dimensions"]["error_recovery"] < r_low["dimensions"]["error_recovery"]
    assert r_high["score"] < r_low["score"]
    assert any(i.get("stakes") == "high" for i in r_high["issues"])
    assert "ungrounded_success" in codes(run(load("high_stakes")))


def test_point6_missing_verify_names_what_to_check():
    r = run(load("missing_verify"))
    assert "missing_verification" in codes(r)
    s = next(s for s in r["suggestions"] if s["id"] == "fix_add_verification")
    assert s["what_to_verify"]


def test_point6_failed_verify_present_gets_specify_not_add():
    args = {"goal": "code change", "outcome": "partial", "trace": "action:edit ok > verify failed"}
    r = run(args)
    assert "fix_add_verification" not in fixes(r) and "fix_specify_verification" in fixes(r)


@pytest.mark.parametrize("name,score", [
    ("01_clean_success", 91), ("02_looping_redundant", 45), ("03_ignored_error_ungrounded", 40),
    ("04_vague_minimal", 35), ("05_error_then_retry_success", 91), ("06_verified_with_evidence", 88),
    ("07_pagination_not_a_loop", 89), ("08_privacy_light_steps", 91)])
def test_examples_01_08_base_scores_unchanged(name, score):
    ex = json.loads((FIX.parent.parent / "examples" / f"{name}.json").read_text())
    assert server.score_trace(ex["goal"], ex["trace"], ex["outcome"])["score"] == score
