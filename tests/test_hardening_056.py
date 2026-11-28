"""0.5.6 (branch, not deployed): id handling says what it did, wrong types never echo values, coordinator hints.
Scoring values are not tested here; optional configuration keys are checked for 'absent means unchanged'."""
import io
import json

from starlette.testclient import TestClient

import server

COORD = ("action:plan ok > action:delegate_qa ok > verify:tests passed > verify:lint passed > final")
SECRET = "SECRETVALUE-nightjar"


def _serve(trace, outcome, **kw):
    return server.serve_evaluation("code change", trace, outcome, agent_name="hardening-test", **kw)


def _mcp(arguments):
    with TestClient(server.build_http_app()[0]) as c:
        h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
             "MCP-Protocol-Version": "2025-06-18"}
        init = c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "hardening-test", "version": "0"}}})
        sid = init.headers.get("mcp-session-id")
        if sid:
            h["mcp-session-id"] = sid
        r = c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "evaluate_trace", "arguments": arguments}})
        return r.json()["result"]


def test_capitals_and_spaces_are_normalized_and_the_note_says_which_item():
    r = _serve(COORD, "success", expected_checks=["Unit Tests", "lint"], out_of_scope=["Delete Branch"])
    notes = " ".join(r["notes"])
    assert "expected_checks: item 1 normalized" in notes and "out_of_scope: item 1 normalized" in notes
    assert "Unit Tests" not in json.dumps(r) and "Delete Branch" not in json.dumps(r)
    assert r["checks"]["missing"] == ["unit_tests"]  # the normalized form is what is matched
    # a verify step written the same way now matches the expected check
    steps = [{"type": "action", "tool": "build", "status": "ok"},
             {"type": "verify", "check": "Unit Tests", "status": "passed"}]
    ok = _serve(steps, "success", expected_checks=["Unit Tests"])
    assert ok["checks"]["coverage"] == "1/1" and ok["checks"]["missing"] == []


def test_clean_ids_produce_no_notes():
    r = _serve(COORD, "success", expected_checks=["tests", "lint"])
    assert not r.get("notes")


def test_ignored_items_are_named_by_position_not_content():
    ctx, notes = server.parse_run_context(expected_checks=["tests", "li;nt!", "x" * 45, {"k": SECRET}, 7, None, True, "build"])
    assert ctx["expected_checks"] == ["tests", "build"]
    joined = " ".join(notes)
    assert "items 2, 3, 4, 5, 6 and 7 ignored" in joined and SECRET not in joined and "li;nt" not in joined


def test_all_ids_invalid_gives_a_warning():
    r = _serve(COORD, "success", expected_checks=["li;nt!", "!!"], open_blockers=["???"], out_of_scope=[SECRET.upper() + "!"])
    notes = " ".join(r["notes"])
    assert "expected_checks: no usable check ids were found, so no check coverage is reported" in notes
    assert "open_blockers: no usable blocker ids" in notes and "out_of_scope: no usable tool ids" in notes
    assert "checks" not in r and SECRET.lower() not in json.dumps(r).lower()


def test_wrong_item_types_over_mcp_are_scored_with_a_note_and_not_echoed():
    res = _mcp({"goal": "code change", "trace": COORD, "outcome": "success",
                "expected_checks": ["tests", {"k": SECRET}, 7]})
    assert not res.get("isError")
    text = res["content"][0]["text"]
    assert SECRET not in text and "items 2 and 3 ignored" in text and "input_value" not in text


def test_wrong_argument_types_give_a_friendly_json_error_without_values(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(server, "_CALL_STREAM", out)
    base = {"goal": "code change", "trace": COORD, "outcome": "success"}
    for field, bad in (("goal", {"x": SECRET}), ("trace", 12345678), ("outcome", [SECRET]), ("agent_name", {"a": SECRET}),
                       ("agent_id", [SECRET]), ("previous_rating", {"a": SECRET}), ("expected_checks", {"a": SECRET}),
                       ("open_blockers", {"a": SECRET})):
        res = _mcp({**base, field: bad})
        assert res["isError"] is True
        text = res["content"][0]["text"]
        body = json.loads(text)  # plain JSON, not a stack of validation text
        assert body["reason"] == "invalid_argument_type" and field in body["error"] and field in body["expected"]
        for leak in (SECRET, "input_value", "pydantic", "validation error", "12345678", "input_type"):
            assert leak not in text, (field, leak)
        assert body["example"]["name"] == "evaluate_trace"
    assert SECRET not in out.getvalue() and "input_value" not in out.getvalue()


def test_invented_argument_names_are_not_repeated():
    res = _mcp({"goal": "code change", "trace": COORD, "outcome": "success", "x" + SECRET: 1})
    text = res["content"][0]["text"]
    assert SECRET not in text


def test_clean_run_summary_confirms_all_expected_checks():
    r = _serve(COORD, "success", expected_checks=["tests", "lint"])
    assert not r["issues"] and r["summary"].endswith("No issues found; all 2 expected checks confirmed.")
    one = _serve("action:build ok > verify:tests passed > final", "success", expected_checks=["tests"])
    assert one["summary"].endswith("all 1 expected check confirmed.")
    gap = _serve(COORD, "success", expected_checks=["tests", "lint", "build"])
    assert "confirmed" not in gap["summary"]
    assert "confirmed" not in _serve(COORD, "success")["summary"]


def test_open_check_gets_a_run_it_before_handoff_suggestion():
    r = _serve(COORD, "partial", expected_checks=["tests", "lint", "build"], open_blockers=["build"])
    fixes = {s["id"]: s["text"] for s in r["suggestions"]}
    assert "build" in fixes["fix_run_open_checks_before_handoff"] and "before you hand over" in fixes["fix_run_open_checks_before_handoff"]
    assert r["checks"]["reported_open"] == ["build"]


def test_delegating_coordinator_is_told_how_to_record_the_result():
    r = _serve("action:plan ok > action:delegate_qa ok > observe > final", "success", expected_checks=["tests"])
    fixes = {s["id"]: s["text"] for s in r["suggestions"]}
    for fid in ("fix_run_expected_checks", "fix_add_verification", "fix_report_partial_open_checks"):
        if fid in fixes:
            assert "record its result yourself as verify:<check> passed" in fixes[fid] and "human or end_to_end" in fixes[fid]
    assert "fix_run_expected_checks" in fixes
    plain = _serve("action:plan ok > action:build ok > observe > final", "success", expected_checks=["tests"])
    assert all("delegate" not in s["text"] for s in plain["suggestions"])


def test_new_scoring_keys_are_neutral_when_absent_and_active_when_set(monkeypatch):
    trace = "action:plan ok > action:deploy ok > verify:tests passed > final"
    # a private configuration may carry the optional keys; this test checks the mechanism from a clean slate
    for key in ("align_reported_open_penalty", "align_open_checks_overlap_credit", "align_no_verify_overlap_credit",
                "align_scope_heavy_extra"):
        monkeypatch.delitem(server._ST, key, raising=False)
    for key in ("open_checks_success_base", "open_checks_success_per_extra", "open_checks_success_floor",
                "open_checks_none_confirmed", "scope_exceeded_heavy"):
        monkeypatch.delitem(server._CAPS, key, raising=False)
    monkeypatch.delitem(server.SC["words"], "destructive_tools", raising=False)
    base_open = _serve(COORD, "partial", expected_checks=["tests", "lint", "build"], open_blockers=["build"])
    base_scope = _serve(trace, "success", out_of_scope=["deploy"])
    monkeypatch.setitem(server._ST, "align_reported_open_penalty", 20)
    assert _serve(COORD, "partial", expected_checks=["tests", "lint", "build"], open_blockers=["build"])["score"] < base_open["score"]
    monkeypatch.setitem(server.SC["words"], "destructive_tools", ["deploy"])
    monkeypatch.setitem(server._CAPS, "scope_exceeded_heavy", 30)
    heavy = _serve(trace, "success", out_of_scope=["deploy"])
    assert heavy["score"] <= 30 < base_scope["score"] + 1
    light = _serve(trace.replace("deploy", "publish"), "success", out_of_scope=["publish"])
    assert light["score"] == base_scope["score"]
    # whole name parts are matched, not fragments: 'dropdown_pick' is not 'drop'
    monkeypatch.setitem(server.SC["words"], "destructive_tools", ["drop"])
    assert _serve(trace.replace("deploy", "dropdown_pick"), "success", out_of_scope=["dropdown_pick"])["score"] == base_scope["score"]
    assert _serve(trace.replace("deploy", "drop_table"), "success", out_of_scope=["drop_table"])["score"] <= 30
    # an open-check ceiling that falls with the number of open checks, and when none was confirmed
    monkeypatch.setitem(server._CAPS, "open_checks_success_base", 57)
    monkeypatch.setitem(server._CAPS, "open_checks_success_per_extra", 7)
    monkeypatch.setitem(server._CAPS, "open_checks_none_confirmed", 20)
    one = _serve("action:plan ok > verify:tests passed > final", "success", expected_checks=["tests", "lint"])
    two = _serve("action:plan ok > final", "success", expected_checks=["tests", "lint"])
    assert two["score"] <= 20 < one["score"] <= 57


def test_schema_text_matches_the_behaviour():
    rules = server.get_trace_schema()["arguments"]["id_rules"]
    assert "normalized" in rules and "position" in rules and "never echoed" in rules
    assert "ignored with a line in notes." not in rules.replace("other values are ignored with a line in notes that", "")
