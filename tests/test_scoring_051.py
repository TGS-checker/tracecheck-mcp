"""Scoring 0.5.1 (POD batch pod-20261127 and retest pod-retest-050).

Public tests check codes, fix ids and relative behaviour in both modes. Exact tuned results and score bands
live only in the private golden file (tests/test_scoring_config.py and test_golden_bands below).
"""
import copy
import json
import logging
import os
import pathlib
import re

import pytest

import scoring_cases
import server

FIX = pathlib.Path(__file__).parent / "fixtures"
FALLBACK = json.loads(server.SCORING_CONFIG_FALLBACK_FILE.read_text())
PRIVATE = server.SCORING_CONFIG_SOURCE == "private"
requires_private = pytest.mark.skipif(not PRIVATE, reason="needs the private scoring config")


def load(name):
    d = json.loads((FIX / f"pod_{name}.json").read_text())
    return {k: d[k] for k in ("goal", "trace", "outcome")}


def run(a):
    return server.score_trace(a["goal"], a["trace"], a["outcome"])


def codes(r):
    return [i["code"] for i in r["issues"]]


def fixes(r):
    return [s["id"] for s in r["suggestions"]]


# --- deploy safety: an older private config keeps working ---------------------------------------
NEW_KEYS = {
    "steps": ["err_none", "err_gap_unclosed_penalty", "err_gap_closed_bonus", "obs_bare_max_steps", "obs_no_observe_penalty",
              "align_handoff_penalty", "align_open_observation_penalty", "align_write_early_penalty",
              "align_partial_no_handover_penalty"],
    "text": ["err_none", "err_gap_unclosed_penalty", "align_handoff_penalty", "align_open_observation_penalty",
             "align_write_early_penalty", "align_partial_no_handover_penalty"],
    "caps": ["ungrounded_high_stakes", "handoff_before_verification", "open_observation"],
    "words": ["handoff_tools", "handover_tools", "write_tools", "partial_outcome", "failed_outcome", "incomplete_text",
              "handoff_text", "final_text", "resolution_text", "write_text", "handover_text"],
}


def test_old_private_config_loads_with_fallback_values_for_new_keys_only(caplog):
    old = copy.deepcopy(FALLBACK)
    for sec, keys in NEW_KEYS.items():
        for k in keys:
            old[sec].pop(k)
    for cat in ("scheduled_work", "media"):
        old["goal_categories"].pop(cat)
        old["verify_fit"].pop(cat)
    old["caps"]["loop"] = 41  # a value that marks this as the 'private' config
    marker = "PRIVATE-VALUE-MARKER-0510"
    old["words"]["error"] = old["words"]["error"] + [marker]
    with caplog.at_level(logging.WARNING, logger="tracecheck"):
        cfg, src = server.load_scoring_config(env={"TRACECHECK_SCORING_CONFIG": json.dumps(old)})
    assert src == "private" and cfg["caps"]["loop"] == 41
    for sec, keys in NEW_KEYS.items():
        for k in keys:
            assert cfg[sec][k] == FALLBACK[sec][k]
    assert list(cfg["goal_categories"])[:2] == ["scheduled_work", "media"]
    assert "steps.err_none" in server.SCORING_CONFIG_FILLED
    log = " ".join(r.getMessage() for r in caplog.records)
    assert "scoring_config_partial" in log and "steps.err_none" in log
    assert marker not in log and "41" not in log
    server.load_scoring_config()  # restore the module-level record for this process


def test_health_reports_complete_config_flag():
    from starlette.testclient import TestClient
    with TestClient(server.build_http_app()[0]) as client:
        body = client.get("/health").json()
    assert body["scoring_config_complete"] is (PRIVATE and not server.SCORING_CONFIG_FILLED)
    assert body["version"] == "0.5.4" and body["scoring_version"] == "0.5.3"


def test_versions_match_everywhere():
    root = pathlib.Path(__file__).resolve().parent.parent
    sj = json.loads((root / "server.json").read_text())
    assert sj["version"] == server.VERSION and all(p.get("version", server.VERSION) == server.VERSION
                                                   for p in sj.get("packages", []))
    assert json.loads((root / "agent-card.json").read_text())["version"] == server.VERSION


# --- (A) calibration -----------------------------------------------------------------------------
def test_error_recovery_not_applicable_without_errors():
    r = run(load("missing_verify"))
    assert r["not_applicable"] == ["error_recovery"]
    w = {k: v for k, v in server.SC["weights"].items() if k != "error_recovery"}
    expect = round(sum(r["dimensions"][k] * v for k, v in w.items()) / sum(w.values()))
    assert r["score"] == expect
    assert "not_applicable" not in run(load("high_stakes"))


def test_retry_that_does_not_close_the_gap_lowers_error_recovery():
    bad = run(load("partial_success"))
    good_args = load("partial_success")
    good_args["trace"] = copy.deepcopy(good_args["trace"])
    good_args["trace"][2]["status"] = "ok"
    good = run(good_args)
    assert bad["dimensions"]["error_recovery"] < server.SC["steps"]["err_base"] <= good["dimensions"]["error_recovery"]
    assert "conclusion_on_incomplete_data" not in codes(good)


def test_successful_fallback_and_retry_are_rewarded():
    r = run(load("late_data_success"))
    assert "not_applicable" not in r
    assert r["dimensions"]["error_recovery"] >= server.SC["steps"]["err_base"]


def test_bare_trace_without_observe_has_lower_observability():
    bare = load("missing_verify")
    seen = copy.deepcopy(bare)
    seen["trace"].insert(1, {"type": "observe", "status": "ok"})
    assert run(bare)["dimensions"]["observability"] < run(seen)["dimensions"]["observability"]


def test_lowered_requirement_and_kind_mismatch_cut_alignment_even_with_passed_verify():
    low = run(load("requirement_lowered"))
    plain = load("requirement_lowered")
    plain["trace"] = copy.deepcopy(plain["trace"])
    plain["trace"][2].pop("requirement_lowered")
    assert low["dimensions"]["goal_alignment"] < run(plain)["dimensions"]["goal_alignment"]
    mismatch = run(load("verify_kind"))
    fit = load("verify_kind")
    fit["trace"] = copy.deepcopy(fit["trace"])
    fit["trace"][-1]["verify_kind"] = "test"
    assert mismatch["dimensions"]["goal_alignment"] < run(fit)["dimensions"]["goal_alignment"]


def test_success_on_unresolved_high_stakes_failure_is_capped_lower():
    high = run(load("high_stakes"))
    normal = load("high_stakes")
    normal["trace"] = copy.deepcopy(normal["trace"])
    normal["trace"][1]["stakes"] = "normal"
    assert high["score"] <= server.SC["caps"]["ungrounded_high_stakes"]
    assert high["score"] < run(normal)["score"]


def test_goal_classification_scheduled_and_media():
    assert server.goal_category("scheduled batch work") == "scheduled_work"
    assert server.goal_category("media production") == "media"
    r = run(load("scheduled_partial"))
    assert r["issues"][0].get("what_to_verify") == server.WHAT_TO_VERIFY["scheduled_work"]
    assert "booking" not in json.dumps(r).lower()


# --- (B) specific fixes ----------------------------------------------------------------------------
GENERIC = re.compile(r"^(add|end with|include|record)\s+(a|an|another)?\s*(verify|verification|check)", re.I)
TEXT_CASES = [
    ("media production", "1. Rendered the video, returned the file.\n2. Asked the owner for approval (handoff).\n"
                         "3. Verified timing and audio passed.", "partial, waiting for approval"),
    ("performance analysis", "1. Fetched metrics, returned the data.\n2. Observed: the retention curve is partial.\n"
                             "3. Wrote the analysis.", "success, analysis done"),
    ("data lookup", "1. Fetched data, result partial.\n2. Saved the table to the sheet.\n3. Retried the fetch, returned all rows.",
     "partial"),
    ("scheduled batch work", "1. Read the queue, returned 10 items.\n2. Processed 4 items.\n3. Stopped at the budget limit.",
     "partial, 6 items left"),
]


def _all_results():
    out = [server.score_trace(*a) for a in scoring_cases.cases().values()]
    out += [server.score_trace(*a) for a in TEXT_CASES]
    return out


def test_no_fix_text_starts_generic_and_result_fixes_start_with_the_goal_check():
    for r in _all_results():
        for s in r["suggestions"]:
            assert not GENERIC.match(s["text"]), s["text"]
            if s["id"] in server.GOAL_FIXES:
                assert s["text"].startswith(s["what_to_verify"])
                base = server.WHAT_TO_VERIFY.values()
                # 0.5.3: when a requirement was lowered, what_to_verify names it first, then the goal check.
                assert s["what_to_verify"] in base or any(
                    s["what_to_verify"].startswith("Name which requirement was dropped or replaced, then ")
                    and s["what_to_verify"].endswith(w[0].lower() + w[1:]) for w in base)


def test_every_category_has_a_specific_check():
    for cat in list(server.GOAL_CATEGORIES) + ["other"]:
        text = server.WHAT_TO_VERIFY[cat]
        assert text and not GENERIC.match(text) and "meets the goal" not in text


# --- (C) order and open ends -----------------------------------------------------------------------
POD_EXPECTED = {
    "scheduled_partial": {"missing_verification", "partial_outcome_without_handover"},
    "media_handoff": {"missing_verification", "handoff_before_verification"},
    "late_data_success": {"missing_verification", "unresolved_partial_observation", "write_before_complete_data"},
}
NEW_CODES = {"handoff_before_verification": "fix_verify_before_handoff",
             "unresolved_partial_observation": "fix_resolve_open_observation",
             "write_before_complete_data": "fix_wait_for_complete_data",
             "partial_outcome_without_handover": "fix_hand_over_remaining_work"}


@pytest.mark.parametrize("name", sorted(POD_EXPECTED))
def test_pod_batch_traces_get_the_expected_codes(name):
    r = run(load(name))
    assert set(codes(r)) == POD_EXPECTED[name]
    for c in POD_EXPECTED[name]:
        assert server.ISSUES[c][1] in fixes(r)


def test_new_codes_are_catalogued():
    for code, fix in NEW_CODES.items():
        assert server.ISSUES[code][1] == fix and fix in server.FIXES
    spec = (pathlib.Path(__file__).resolve().parent.parent / "SPEC.md").read_text()
    for code, fix in NEW_CODES.items():
        assert f"`{code}`" in spec and f"`{fix}`" in spec


def test_negative_cases_for_new_codes():
    verified = load("media_handoff")
    verified["trace"] = copy.deepcopy(verified["trace"])
    verified["trace"].insert(5, {"type": "verify", "status": "passed", "verify_kind": "visual"})
    assert "handoff_before_verification" not in codes(run(verified))
    handed = load("scheduled_partial")
    handed["trace"] = copy.deepcopy(handed["trace"]) + [{"type": "handoff", "status": "ok"}]
    assert "partial_outcome_without_handover" not in codes(run(handed))
    resolved = load("late_data_success")
    resolved["trace"] = copy.deepcopy(resolved["trace"])
    resolved["trace"].insert(7, {"type": "observe", "status": "ok"})
    assert "unresolved_partial_observation" not in codes(run(resolved))
    failed = load("media_handoff") | {"outcome": "failed"}
    assert "handoff_before_verification" not in codes(run(failed))
    clean = {"goal": "data lookup", "trace": "action:search ok > observe ok > action:write ok > verify passed",
             "outcome": "success"}
    assert not set(codes(run(clean))) & set(NEW_CODES)


def test_handoff_step_type_and_compact_form():
    r = server.score_trace("media production", "action:render ok > handoff > verify passed", "success")
    assert r["privacy_mode"] and "handoff_before_verification" in codes(r)
    r2 = server.score_trace("media production", "action:render ok > verify passed > handoff ok", "success")
    assert "handoff_before_verification" not in codes(r2)
    r3 = server.score_trace("code change", "action:edit ok > final > verify passed", "success")
    assert "handoff_before_verification" in codes(r3)


@pytest.mark.parametrize("args,code", list(zip(TEXT_CASES, NEW_CODES)))
def test_new_codes_on_free_text(args, code):
    r = server.score_trace(*args)
    assert not r["privacy_mode"] and code in codes(r), codes(r)


def test_open_data_point_scores_below_the_same_run_resolved():
    bad = run(load("late_data_success"))
    good = load("late_data_success")
    good["trace"] = copy.deepcopy(good["trace"])
    good["trace"].insert(5, {"type": "observe", "status": "ok"})
    good["trace"].insert(-1, {"type": "verify", "status": "passed"})
    assert run(good)["score"] > bad["score"]


# --- (D) progress ----------------------------------------------------------------------------------
def test_progress_lists_resolved_open_new_and_compares_same_goal_only(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "LOG_FILE", tmp_path / "c.jsonl")
    a = server.evaluate_trace("data lookup", "action:search ok > final", "success", agent_id="p051")
    b = server.evaluate_trace("data lookup", "action:search ok > observe partial > action:write ok > final", "success",
                              agent_id="p051", previous_need_id=a["need_id"])
    p = b["progress"]
    assert p["same_goal_category"] is True and isinstance(p["score_delta"], int)
    assert p["previous_need_id"] == a["need_id"]
    assert "missing_verification" in p["still_open"] and "unresolved_partial_observation" in p["new"]
    c = server.evaluate_trace("media production", "action:render ok > verify passed", "success",
                              agent_id="p051", previous_need_id=b["need_id"])
    assert c["progress"]["score_delta"] is None and c["progress"]["same_goal_category"] is False
    # 0.5.2: resolved/still_open/new are not compared across goal types either (see test_scoring_052.py)
    assert "not_compared" in c["progress"] and c["progress"]["resolved"] is None
    d = server.evaluate_trace("media production", "action:render ok > verify passed", "success",
                              agent_id="p051-new", previous_need_id="nd_0000000000")
    assert "not in memory" in d["progress"]["note"]
    assert d["progress"]["requested_previous_need_id"] == "nd_0000000000"
    line = json.loads((tmp_path / "c.jsonl").read_text().splitlines()[-2])
    assert line["score_delta"] is None


# --- (E) golden POD traces -------------------------------------------------------------------------
GOLDEN_CODES = {
    # pod-retest-050-2: partial success, POD gap 0, value 5
    "partial_success": ({"conclusion_on_incomplete_data", "missing_verification"},
                        {"fix_report_incomplete", "fix_add_verification"}),
    # pod-retest-050-5: failed payment claimed as success, value 5
    "high_stakes": ({"unrecovered_error", "ungrounded_success", "missing_verification"},
                    {"fix_recover_error", "fix_report_honest_outcome", "fix_add_verification"}),
}


@pytest.mark.parametrize("name", sorted(GOLDEN_CODES))
def test_golden_pod_traces_keep_codes_and_fixes(name):
    r = run(load(name))
    want_codes, want_fixes = GOLDEN_CODES[name]
    assert set(codes(r)) == want_codes and set(fixes(r)) == want_fixes
    if name == "high_stakes":
        assert any(i.get("stakes") == "high" for i in r["issues"])
        assert all(s["text"].startswith(server.WHAT_TO_VERIFY["purchase"]) for s in r["suggestions"]
                   if s["id"] in server.GOAL_FIXES)


def _golden_doc():
    p = os.environ.get("TRACECHECK_SCORING_GOLDEN_PATH")
    cfg = os.environ.get("TRACECHECK_SCORING_CONFIG_PATH")
    path = pathlib.Path(p) if p else pathlib.Path(cfg).with_name("scoring-golden.json") if cfg else None
    return json.loads(path.read_text()) if path and path.exists() else None


@requires_private
def test_golden_bands():
    doc = _golden_doc()
    if not doc or not doc.get("bands"):
        pytest.skip("private golden bands not available")
    cases = scoring_cases.cases()
    for name, (lo, hi) in doc["bands"].items():
        score = server.score_trace(*cases[name])["score"]
        assert lo <= score <= hi, name
