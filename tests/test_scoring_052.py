"""Scoring 0.5.2 (POD retest pod-retest-051).

Public tests: behaviour and structure in both modes (public fallback and private config), plus deploy safety with
an older private config. Exact tuned values and the locked POD-confirmed scores live only in the private golden file.
"""
import copy
import json
import os
import pathlib

import pytest

import scoring_cases
import server
from test_scoring_051 import FALLBACK, PRIVATE, _golden_doc, load, requires_private, run


def _na_run():
    return server.score_trace("media production", "action:render ok > observe ok > verify passed", "success")


# --- (a) not applicable is never shown as 100 ---------------------------------------------------------
def test_error_recovery_not_applicable_is_null_with_a_reason():
    r = _na_run()
    assert r["not_applicable"] == ["error_recovery"]
    assert r["dimensions"]["error_recovery"] is None
    assert set(r["dimensions"]) == {"goal_clarity", "observability", "error_recovery", "goal_alignment", "efficiency"}
    assert "nothing to recover" in r["not_applicable_reason"]["error_recovery"]
    assert "not counted" in r["summary"] and "error recovery 100" not in r["summary"]
    w = {k: v for k, v in server.SC["weights"].items() if k != "error_recovery"}
    expect = round(sum(r["dimensions"][k] * v for k, v in w.items()) / sum(w.values()))
    assert r["score"] == expect


def test_error_recovery_applicable_stays_numeric_and_has_no_reason():
    r = run(load("high_stakes"))
    assert isinstance(r["dimensions"]["error_recovery"], int)
    assert "not_applicable" not in r and "not_applicable_reason" not in r


def test_not_applicable_also_on_free_text():
    r = server.score_trace("Find the cheapest direct flight AMS to LIS and report price",
                           "1. search flights returned 5 results\n2. picked the cheapest\nVerified: price matches the page",
                           "Cheapest direct flight AMS-LIS is 89 EUR, verified on the page")
    assert not r["privacy_mode"]
    assert r["dimensions"]["error_recovery"] is None and r["not_applicable"] == ["error_recovery"]


def test_event_log_accepts_null_dimension(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "LOG_FILE", tmp_path / "c.jsonl")
    server.evaluate_trace("media production", "action:render ok > observe ok > verify passed", "success", agent_id="p052a")
    rows = [json.loads(l) for l in (tmp_path / "c.jsonl").read_text().splitlines() if l.strip()]
    assert rows[-1]["dimensions"]["error_recovery"] is None


# --- (b) progress per goal type only ---------------------------------------------------------------------
def test_progress_without_previous_need_id_does_not_compare_across_goal_types(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "LOG_FILE", tmp_path / "c.jsonl")
    server.evaluate_trace("data lookup", "action:search ok > final", "success", agent_id="p052b")
    b = server.evaluate_trace("media production", "action:render ok > verify passed", "success", agent_id="p052b")
    p = b["progress"]
    assert p["same_goal_category"] is False and p["compared_via"].startswith("agent_id")
    assert p["score_delta"] is None and p["resolved"] is None and p["still_open"] is None and p["new"] is None
    assert "data_lookup" in p["not_compared"] and "media" in p["not_compared"] and "previous_need_id" in p["not_compared"]


def test_progress_same_goal_type_lists_codes_and_names_the_basis(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "LOG_FILE", tmp_path / "c.jsonl")
    a = server.evaluate_trace("data lookup", "action:search ok > final", "success", agent_id="p052c")
    b = server.evaluate_trace("data lookup", "action:search ok > observe ok > action:fetch ok > verify passed", "success",
                              agent_id="p052c")
    assert b["progress"]["compared_via"].startswith("agent_id") and isinstance(b["progress"]["score_delta"], int)
    assert "missing_verification" in b["progress"]["resolved"] and b["progress"]["still_open"] == []
    c = server.evaluate_trace("data lookup", "action:search ok > final", "success", agent_id="p052c",
                              previous_need_id=b["need_id"])
    assert c["progress"]["compared_via"] == "previous_need_id" and "missing_verification" in c["progress"]["new"]
    assert a["need_id"] != b["need_id"]


# --- (c) order calibration, (d) observability -----------------------------------------------------------
def test_handoff_before_verification_stays_below_the_same_run_verified_first():
    bad = run(load("media_handoff"))
    good = load("media_handoff")
    good["trace"] = copy.deepcopy(good["trace"])
    good["trace"].insert(5, {"type": "verify", "status": "passed", "verify_kind": "visual"})
    assert run(good)["score"] > bad["score"]
    assert "handoff_before_verification" in [i["code"] for i in bad["issues"]]


def test_partial_without_handover_scores_below_the_same_run_handed_over():
    bad = run(load("scheduled_partial"))
    good = load("scheduled_partial")
    good["trace"] = (copy.deepcopy(good["trace"])[:-1]
                     + [{"type": "verify", "status": "passed"}, {"type": "handoff", "status": "ok"}, {"type": "final", "status": "ok"}])
    assert "partial_outcome_without_handover" not in [i["code"] for i in run(good)["issues"]]
    assert run(good)["score"] > bad["score"]


def test_typed_verify_step_adds_observability_even_when_the_kind_does_not_fit():
    base = load("verify_kind")
    untyped = copy.deepcopy(base)
    for st in untyped["trace"]:
        st.pop("verify_kind", None)
    typed, plain = run(base), run(untyped)
    assert "verify_kind_mismatch" in [i["code"] for i in typed["issues"]]
    assert typed["dimensions"]["observability"] == plain["dimensions"]["observability"] + server.SC["steps"]["obs_verify_kind_bonus"]


# --- deploy safety: an older private config (0.5.1) keeps working ---------------------------------------
def _old_config():
    old = copy.deepcopy(FALLBACK)
    old["steps"].pop("obs_verify_kind_bonus")
    old["caps"]["loop"] = 41  # marks it as 'private'
    return old


def test_old_private_config_without_the_new_key_loads_via_env_json_and_path(tmp_path, caplog):
    old = _old_config()
    cfg, src = server.load_scoring_config(env={"TRACECHECK_SCORING_CONFIG": json.dumps(old)})
    assert src == "private" and cfg["caps"]["loop"] == 41
    assert cfg["steps"]["obs_verify_kind_bonus"] == FALLBACK["steps"]["obs_verify_kind_bonus"]
    assert server.SCORING_CONFIG_FILLED == ["steps.obs_verify_kind_bonus"]
    f = tmp_path / "scoring.json"
    f.write_text(json.dumps(old))
    cfg, src = server.load_scoring_config(env={"TRACECHECK_SCORING_CONFIG_PATH": str(f)})
    assert src == "private" and cfg["steps"]["obs_verify_kind_bonus"] == FALLBACK["steps"]["obs_verify_kind_bonus"]
    cfg, src = server.load_scoring_config(env={})
    assert src == "fallback" and cfg == FALLBACK and server.SCORING_CONFIG_FILLED == []
    server.load_scoring_config()  # restore the module-level record for this process


def test_new_code_scores_with_an_old_config_without_errors(monkeypatch):
    old = _old_config()
    cfg, _ = server.load_scoring_config(env={"TRACECHECK_SCORING_CONFIG": json.dumps(old)})
    monkeypatch.setattr(server, "SC", cfg)
    monkeypatch.setattr(server, "_ST", cfg["steps"])
    for name in ("verify_kind", "media_handoff", "scheduled_partial", "high_stakes"):
        r = run(load(name))
        assert isinstance(r["score"], int) and 0 <= r["score"] <= 100, name
    server.load_scoring_config()


def test_fallback_has_the_new_key_and_versions_agree():
    assert "obs_verify_kind_bonus" in FALLBACK["steps"]
    root = pathlib.Path(__file__).resolve().parent.parent
    assert server.VERSION == "0.5.5" and server.SCORING_VERSION == "0.5.5"
    assert f'version = "{server.VERSION}"' in (root / "pyproject.toml").read_text()


# --- (E) locked golden traces (private) -----------------------------------------------------------------
@requires_private
def test_locked_pod_traces_keep_their_confirmed_scores():
    doc = _golden_doc()
    if not doc or not doc.get("locked"):
        pytest.skip("private locked scores not available")
    cases = scoring_cases.cases()
    for name, want in doc["locked"].items():
        assert server.score_trace(*cases[name])["score"] == want, name
