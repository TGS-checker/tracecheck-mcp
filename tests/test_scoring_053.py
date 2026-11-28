"""Scoring 0.5.3 (POD retest pod-retest-052, approved items 1-5).

Public tests: outcome-aware fix text, lowered-requirement what_to_verify, preferred handoff order,
and a teaching note when previous_need_id is no longer in memory. Exact tuned scores live only in
the private golden file.
"""
import json
import os
import pathlib

import pytest

import scoring_cases
import server
from test_scoring_051 import FALLBACK, PRIVATE, _golden_doc, codes, fixes, load, requires_private, run


def _sug(r, fid):
    return next(s for s in r["suggestions"] if s["id"] == fid)


# --- tip 6: verification fix follows the claimed outcome ------------------------------------------
def test_fix_add_verification_on_partial_does_not_say_report_success():
    r = run(load("scheduled_partial"))
    assert "missing_verification" in codes(r) and "partial_outcome_without_handover" in codes(r)
    text = _sug(r, "fix_add_verification")["text"]
    assert "report success" not in text.lower()
    assert "hand over" in text.lower() or "close the run" in text.lower()


def test_fix_add_verification_on_success_still_says_report_success():
    r = run(load("missing_verify"))
    text = _sug(r, "fix_add_verification")["text"]
    assert "before you report success" in text


def test_fix_add_verification_on_failed_confirms_the_failure():
    r = server.score_trace("content production",
                           "action:generate ok > action:export ok > final", "failed")
    text = _sug(r, "fix_add_verification")["text"]
    assert "report success" not in text.lower()
    assert "failure" in text.lower() or "failed" in text.lower()


# --- tip 4: name the lowered requirement in what_to_verify ----------------------------------------
def test_requirement_lowered_what_to_verify_names_the_requirement():
    r = run(load("requirement_lowered"))
    assert "requirement_lowered" in codes(r)
    s = _sug(r, "fix_restore_or_confirm_requirement")
    assert "which requirement" in s["what_to_verify"].lower()
    assert "dropped or replaced" in s["what_to_verify"].lower()
    # still includes the goal-type check (booking)
    assert "booking" in s["what_to_verify"].lower()
    assert s["text"].startswith(s["what_to_verify"])


def test_what_to_verify_unchanged_when_no_requirement_was_lowered():
    r = run(load("high_stakes"))
    assert "requirement_lowered" not in codes(r)
    for s in r["suggestions"]:
        if s["id"] in server.GOAL_FIXES:
            assert s["what_to_verify"] == server.WHAT_TO_VERIFY["purchase"]
            assert not s["what_to_verify"].lower().startswith("name which requirement")


# --- tip 7: verify first, then hand off ------------------------------------------------------------
def test_verify_before_handoff_states_preferred_order():
    r = run(load("media_handoff"))
    assert "fix_verify_before_handoff" in fixes(r)
    text = _sug(r, "fix_verify_before_handoff")["text"].lower()
    assert "do this first" in text
    assert "only if you cannot verify" in text
    # not the old equal-choice wording
    assert "or say in the hand-off" not in text


# --- need_id expiry teaches the agent, never rejects the score ------------------------------------
def test_expired_need_id_falls_back_to_agent_id_with_a_teaching_note(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "LOG_FILE", tmp_path / "c.jsonl")
    a = server.evaluate_trace("data lookup", "action:search ok > final", "success", agent_id="p053e")
    b = server.evaluate_trace("data lookup", "action:search ok > observe ok > verify passed", "success",
                              agent_id="p053e", previous_need_id="nd_0000000000")
    assert isinstance(b["score"], int) and "error" not in b
    p = b["progress"]
    assert p["compared_via"].startswith("agent_id")
    assert p["requested_previous_need_id"] == "nd_0000000000"
    assert p["previous_need_id"] == a["need_id"]
    assert "not in memory" in p["note"] and "need_id next time" in p["note"]
    assert "service restart" in p["note"]
    # no internal how (cap, store size)
    assert "5000" not in p["note"] and "TTL" not in p["note"] and "_NEED_MEMORY" not in p["note"]


def test_valid_need_id_has_no_expiry_note_or_requested_field(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "LOG_FILE", tmp_path / "c.jsonl")
    a = server.evaluate_trace("data lookup", "action:search ok > final", "success", agent_id="p053v")
    b = server.evaluate_trace("data lookup", "action:search ok > observe ok > verify passed", "success",
                              agent_id="p053v", previous_need_id=a["need_id"])
    p = b["progress"]
    assert p["compared_via"] == "previous_need_id"
    assert "requested_previous_need_id" not in p
    assert "note" not in p or "not in memory" not in p.get("note", "")


def test_expired_need_id_without_agent_memory_still_returns_a_score(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "LOG_FILE", tmp_path / "c.jsonl")
    r = server.evaluate_trace("data lookup", "action:search ok > final", "success",
                              agent_id="p053-new", previous_need_id="nd_aaaaaaaaaa")
    assert isinstance(r["score"], int)
    assert "not in memory" in r["progress"]["note"]
    assert r["progress"]["requested_previous_need_id"] == "nd_aaaaaaaaaa"
    assert "compared_via" not in r["progress"]  # no fallback check to compare


# --- tip 8 is private-only; public tests only check relative order and locked scores --------------
def test_open_unresolved_observation_scores_below_the_same_run_resolved():
    bad = run(load("late_data_success"))
    good = load("late_data_success")
    import copy
    good["trace"] = copy.deepcopy(good["trace"])
    good["trace"].insert(5, {"type": "observe", "status": "ok"})
    good["trace"].insert(-1, {"type": "verify", "status": "passed"})
    assert run(good)["score"] > bad["score"]
    assert "unresolved_partial_observation" in codes(bad)


@requires_private
def test_locked_gap_zero_scores_hold():
    doc = _golden_doc()
    if not doc or not doc.get("locked"):
        pytest.skip("private locked scores not available")
    cases = scoring_cases.cases()
    for name, want in doc["locked"].items():
        assert server.score_trace(*cases[name])["score"] == want, name


def test_versions_are_0_5_3():
    root = pathlib.Path(__file__).resolve().parent.parent
    assert server.VERSION == "0.5.4" and server.SCORING_VERSION == "0.5.3"
    assert json.loads((root / "server.json").read_text())["version"] == "0.5.4"
    assert json.loads((root / "agent-card.json").read_text())["version"] == "0.5.4"
    assert f'version = "{server.VERSION}"' in (root / "pyproject.toml").read_text()


def test_fallback_has_no_new_required_keys_beyond_0_5_2():
    # 0.5.3 adds no new fallback keys; an older private config must keep loading as complete.
    old = json.loads(json.dumps(FALLBACK))
    cfg, src = server.load_scoring_config(env={"TRACECHECK_SCORING_CONFIG": json.dumps(old)})
    assert src == "private" and server.SCORING_CONFIG_FILLED == []
    server.load_scoring_config()
