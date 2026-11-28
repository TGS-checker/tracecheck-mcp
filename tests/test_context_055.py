"""0.5.5: optional run context from a coordinator brief. Identifiers only, backward compatible."""
import io
import json
import pathlib

from starlette.testclient import TestClient

import scoring_cases
import server

ROOT = pathlib.Path(__file__).resolve().parent.parent
SIX = ["tests", "lint", "build", "docs", "accessibility", "security_scan"]
COORD = ("action:plan ok > action:delegate_build ok > action:delegate_qa ok > verify:tests passed > "
         "verify:lint passed > verify:build passed > verify:docs passed > verify:accessibility passed > final")


def _serve(goal, trace, outcome, **kw):
    return server.serve_evaluation(goal, trace, outcome, agent_name="ctx-test", **kw)


def test_without_context_scores_are_identical():
    for name, args in scoring_cases.cases().items():
        if len(args) == 3:
            assert server.score_trace(*args) == server.score_trace(*args, None) == server.score_trace(*args, {}), name
            assert not {"checks", "scope", "blockers"} & set(server.score_trace(*args)), name


def test_coordinator_six_checks_one_missing_claiming_success():
    r = _serve("code change", COORD, "success", expected_checks=SIX)
    assert r["checks"]["coverage"] == "5/6" and r["checks"]["missing"] == ["security_scan"]
    codes = [i["code"] for i in r["issues"]]
    assert codes[:2] == ["expected_check_missing", "success_with_open_checks"]
    assert r["issues"][0]["checks"] == ["security_scan"]
    fixes = {s["id"]: s["text"] for s in r["suggestions"]}
    assert "security_scan" in fixes["fix_run_expected_checks"]
    assert "partial" in fixes["fix_report_partial_open_checks"].lower()
    assert "security_scan" in fixes["fix_report_partial_open_checks"]
    honest = _serve("code change", COORD, "partial", expected_checks=SIX)
    assert honest["score"] > r["score"]
    assert "success_with_open_checks" not in [i["code"] for i in honest["issues"]]


def test_missing_check_named_as_open_blocker_is_reported_open():
    r = _serve("code change", COORD, "partial", expected_checks=SIX, open_blockers=["security_scan"])
    assert r["checks"]["reported_open"] == ["security_scan"] and r["checks"]["missing"] == []
    assert not [i for i in r["issues"] if i["code"].startswith("expected_check")]
    s = _serve("code change", COORD, "success", expected_checks=SIX, open_blockers=["security_scan"])
    assert {"success_with_open_checks", "success_with_open_blockers"} <= {i["code"] for i in s["issues"]}


def test_failed_unconfirmed_and_later_pass():
    base = COORD.replace(" > final", "")
    failed = _serve("code change", base + " > verify:security_scan failed > final", "success", expected_checks=SIX)
    assert failed["checks"]["failed"] == ["security_scan"]
    assert "expected_check_failed" in [i["code"] for i in failed["issues"]]
    fixed = _serve("code change", base + " > verify:security_scan failed > action:fix ok > verify:security_scan passed > final",
                   "success", expected_checks=SIX)
    assert fixed["checks"]["coverage"] == "6/6" and fixed["checks"]["failed"] == []
    unconf = _serve("code change", base + " > verify:security_scan > final", "success", expected_checks=SIX)
    assert unconf["checks"]["unconfirmed"] == ["security_scan"]
    assert "expected_check_unconfirmed" in [i["code"] for i in unconf["issues"]]


def test_check_field_on_dict_steps_and_not_expected():
    trace = [{"type": "action", "tool": "build", "status": "ok"},
             {"type": "verify", "check": "tests", "status": "passed"},
             {"type": "verify", "check": "smoke", "status": "passed"}]
    r = _serve("code change", trace, "success", expected_checks=["tests"])
    assert r["checks"]["coverage"] == "1/1" and r["checks"]["not_expected"] == ["smoke"]


def test_out_of_scope_and_in_scope():
    r = _serve("code change", "action:edit_file ok > action:deploy ok > verify:tests passed > final", "success",
               out_of_scope=["deploy"])
    assert r["scope"]["exceeded"] == ["deploy"] and r["issues"][0]["code"] == "scope_exceeded"
    assert "deploy" in next(s["text"] for s in r["suggestions"] if s["id"] == "fix_stay_in_scope")
    ok = _serve("code change", "action:edit_file ok > verify:tests passed > final", "success", out_of_scope=["deploy"])
    assert ok["scope"]["exceeded"] == [] and "scope_exceeded" not in [i["code"] for i in ok["issues"]]
    ins = _serve("data lookup", "action:search ok > action:send_email ok > verify passed > final", "success",
                 in_scope=["search"])
    assert ins["scope"]["exceeded"] == ["send_email"]


def test_open_blockers():
    s = _serve("code change", "action:edit_file ok > verify:tests passed > final", "success", open_blockers=2)
    assert s["blockers"] == {"open": 2} and "success_with_open_blockers" in [i["code"] for i in s["issues"]]
    p = _serve("code change", "action:edit_file ok > verify:tests passed > final", "partial", open_blockers=2)
    codes = [i["code"] for i in p["issues"]]
    assert "success_with_open_blockers" not in codes and "partial_outcome_without_handover" not in codes
    z = _serve("code change", "action:edit_file ok > verify:tests passed > final", "success", open_blockers=0)
    assert "success_with_open_blockers" not in [i["code"] for i in z["issues"]]


def test_bad_ids_teach_the_shape_and_are_never_echoed_or_logged(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(server, "_CALL_STREAM", out)
    secret = "Check the hidden level of Project Nightjar"
    r = _serve("code change", COORD, "success", expected_checks=["tests", secret, "x" * 41, 7.5, True],
               out_of_scope="deploy, delete_branch", open_blockers=True)
    assert r["checks"]["expected"] == 1
    notes = " ".join(r["notes"])
    assert "expected_checks: 4 value(s) ignored" in notes and "lowercase letters" in notes and "e.g." in notes
    assert "open_blockers was ignored" in notes
    dump = json.dumps(r) + out.getvalue()
    assert "nightjar" not in dump.lower() and "x" * 41 not in dump
    line = json.loads(out.getvalue().strip().split(" ", 1)[1])
    assert line["context"] == {"expected_checks_n": 1, "passed_checks_n": 1, "scope_given": True,
                               "scope_exceeded_n": 0, "open_blockers_n": None}
    assert "tests" not in json.dumps(line["context"]) and "deploy" not in out.getvalue()


def test_list_limits_and_dedupe():
    ctx, notes = server.parse_run_context(expected_checks=["a", "A", "a "] + [f"c{i}" for i in range(40)])
    assert ctx["expected_checks"][0] == "a" and len(ctx["expected_checks"]) == server.CONTEXT_LIST_MAX
    assert notes and "max 30" in notes[0]
    assert server.parse_run_context(open_blockers=5000)[1]
    assert server.parse_run_context(open_blockers="3")[0] == {"open_blockers": {"count": 3}}


def test_free_text_trace_explains_step_format_is_needed():
    r = _serve("Fix the login bug", "1. edited file\n2. ran tests: all passed\nVerified: login works", "success",
               expected_checks=["tests"])
    assert "checks" not in r and "step format" in r["notes"][0]


def test_schema_description_and_example():
    schema = server.get_trace_schema()
    for f in ("expected_checks", "out_of_scope", "in_scope", "open_blockers", "id_rules"):
        assert f in schema["arguments"]
    assert "check" in schema["step_schema"]
    ex = dict(schema["example_coordinator_call"])
    r = _serve(ex.pop("goal"), ex.pop("trace"), ex.pop("outcome"), **{k: v for k, v in ex.items() if k != "agent_name"})
    assert r["checks"]["reported_open"] == ["security_scan"] and not r["issues"]
    assert "expected_checks" in server.EVALUATE_TRACE_DESCRIPTION
    for weird in ("penalt", "weight", "threshold", "cap "):
        assert weird not in server.EVALUATE_TRACE_DESCRIPTION.lower()


def test_mcp_tool_lists_and_accepts_context_fields():
    with TestClient(server.build_http_app()[0]) as c:
        h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
             "MCP-Protocol-Version": "2025-06-18"}
        init = c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "ctx-test", "version": "0"}}})
        sid = init.headers.get("mcp-session-id")
        if sid:
            h["mcp-session-id"] = sid
        tools = c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).text
        for f in ("expected_checks", "out_of_scope", "in_scope", "open_blockers"):
            assert f in tools
        r = c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "evaluate_trace", "arguments": {"goal": "code change", "trace": COORD, "outcome": "success",
                                                    "expected_checks": SIX}}}).text
        assert "security_scan" in r and "5/6" in r


def test_a2a_accepts_context_fields_camel_case():
    c = TestClient(server.build_http_app()[0])
    msg = {"message": {"role": "ROLE_USER", "messageId": "1", "parts": [{"data": {
        "goal": "code change", "trace": "action:edit_file ok > action:deploy ok > verify:tests passed > final",
        "outcome": "success", "outOfScope": ["deploy"]}}]}}
    data = c.post("/a2a/message:send", json=msg).json()
    part = next(p["data"] for p in data["message"]["parts"] if "data" in p)
    assert part["scope"]["exceeded"] == ["deploy"]
