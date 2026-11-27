"""learn.py: ingest, daily + weekly analysis, net benchmarks, fix effect, public guard."""
import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("learn", ROOT / "scripts" / "learn.py")
learn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(learn)


def call(agent, ts, score, codes, fixes, kind="agent", cat="data_lookup", rating=None):
    d = {"event_id": f"{agent}-{ts}", "ts_utc": ts, "scoring_version": "0.5.0", "agent_id": agent,
         "agent_kind": kind, "goal_category": cat, "score": score, "issue_codes": codes, "fix_ids": fixes,
         "step_pattern": f"p{score}"}
    if rating:
        d["explicit_feedback"] = rating
    return "TRACECHECK_CALL " + json.dumps(d)


@pytest.fixture
def store(tmp_path):
    lines = []
    for i, a in enumerate(["ag_000000000001", "ag_000000000002", "ag_000000000003"]):
        lines.append(call(a, f"2026-11-2{i}T10:00:00Z", 60, ["missing_verification"], ["fix_add_verification"]))
        lines.append(call(a, f"2026-11-2{i+1}T10:00:00Z", 85, [], [], rating=4))
    lines.append(call("ag_00000000000r", "2026-11-25T10:00:00Z", 10, ["loop_detected"], ["fix_break_loop"], kind="reviewer"))
    lines.append('TRACECHECK_MCP_REJECT {"status": 404, "reason": "unknown_path", "path": "/v2/eval"}')
    lines.append('TRACECHECK_MCP_REJECT {"status": 404, "reason": "unknown_path", "path": "/v2/eval", "x": 1}')
    log = tmp_path / "log.jsonl"
    log.write_text("\n".join(lines))
    st = tmp_path / "stats.csv"
    assert learn.append_events(st, [learn.event_from_log(l) for l in log.read_text().splitlines()]) == 9
    assert learn.append_events(st, [learn.event_from_log(l) for l in log.read_text().splitlines()]) == 0
    return st


def test_daily_and_weekly(store):
    k, s = learn.analyse(learn.read_store(store), weekly=True)
    assert k["counts"]["net_calls"] == 6 and k["counts"]["agents"] == 4
    assert s["fixes"]["fix_add_verification"]["n"] == 3
    assert s["fixes"]["fix_add_verification"]["avg_score_delta"] == 25
    assert "fix_break_loop" not in s["fixes"]  # reviewer data never reaches agents
    assert k["ratings"]["rated_calls"] == 3 and k["ratings"]["avg"] == 4
    assert k["alias_suggestions"][0]["count"] == 2
    w = k["weekly"]
    assert w["funnel"]["improved_run"] == 3 and w["biggest_leak"]
    assert k["profiles"]["ag_000000000001"]["trend"] == 25
    learn.assert_public_safe(s)


def test_benchmark_needs_min_n(store):
    _, s = learn.analyse(learn.read_store(store))
    assert s["benchmarks"]["data_lookup"]["n"] == 6 and len(s["benchmarks"]["data_lookup"]["deciles"]) == 9
    k2, s2 = learn.analyse([e for e in learn.read_store(store) if e["agent_id"] != "ag_000000000003"])
    assert s2["benchmarks"] == {}  # too few runs and agents: no benchmark shown
    assert all(r["n"] >= learn.MIN_N_BENCHMARK for r in s["benchmarks"].values())


def test_guard_blocks_private_fields():
    with pytest.raises(ValueError):
        learn.assert_public_safe({"fixes": {}, "profiles": {"ag_123456789012": {}}})


def test_feedback_ingest_is_reviewer(tmp_path):
    csvp = tmp_path / "fb.csv"
    csvp.write_text("date,source,batch_id,trace_id,scoring_version,our_score,gap\n2026-11-27,pod-geldverdiener,b1,t1,0.5.0,80,5\n")
    evs = learn.feedback_events(csvp)
    assert evs[0]["agent_kind"] == "reviewer" and evs[0]["event_type"] == "feedback"
