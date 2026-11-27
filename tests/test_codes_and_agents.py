"""0.5.0 contract: issue codes, fix ids, host reasons, pseudonymous agent ids, progress and ratings."""
import json
import re

from starlette.testclient import TestClient

import server

SAMPLE = {"goal": "data lookup", "trace": "action:search ok > error > retry > verify passed", "outcome": "success"}
TRACES = [
    ("data lookup", "action:search ok > error > retry > verify passed", "success"),
    ("data lookup", "action:search error > action:send error > final", "success"),
    ("x", "action:a > action:b", ""),
    ("booking", [{"type": "action", "tool": "t", "status": "error", "label": "a"},
                 {"type": "action", "tool": "t", "status": "error", "label": "a"},
                 {"type": "action", "tool": "t", "status": "error", "label": "a"}], "success"),
    ("Find the cheapest flight", "searched\nerror 500\ndone", "done"),
    ("Find and book the cheapest direct flight to Lisbon on 12 March",
     "1. called search, returned 4 results\n2. verified price matches goal\n3. booked", "booked flight TP123 for 89 EUR"),
]


def test_every_issue_has_code_and_every_fix_has_id():
    for goal, trace, outcome in TRACES:
        r = server.score_trace(goal, trace, outcome)
        for i in r["issues"]:
            assert i["code"] in server.ISSUES and i["text"]
        for f in r["suggestions"]:
            assert f["id"] in server.FIXES and f["id"].startswith("fix_") and f["text"]
        assert r["scoring_version"] == server.SCORING_VERSION


def test_catalog_naming_rules():
    for code, (text, fix) in server.ISSUES.items():
        assert re.fullmatch(r"[a-z][a-z0-9_]+", code) and not code.startswith("issue_")
        assert fix in server.FIXES
        assert len(re.findall(r"[.!?](\s|$)", text)) <= 2
    for fid, text in server.FIXES.items():
        assert re.fullmatch(r"fix_[a-z0-9_]+", fid)
        assert len(re.findall(r"[.!?](\s|$)", text)) <= 2, fid


def test_clean_run_has_no_dummy_issue():
    r = server.score_trace(*TRACES[0])
    assert all(i["code"] != "no_issues" for i in r["issues"])
    assert "No major structural issues" not in json.dumps(r)
    assert "Capped" not in r["summary"]


def test_agent_id_never_raw_ip(monkeypatch):
    monkeypatch.delenv("TRACECHECK_SALT", raising=False)
    monkeypatch.delenv("TRACECHECK_IP_SALT", raising=False)

    class Req:
        headers = {"user-agent": "python-httpx/0.27", "x-forwarded-for": "203.0.113.7"}
        client = None
    c = server._client_from_request(Req())
    assert "ip_hash" not in c and "203.0.113.7" not in json.dumps(c)
    aid, src = server.derive_agent_id(None, "unknown", c)
    assert aid.startswith("ag_") and src == "client_family"
    monkeypatch.setenv("TRACECHECK_SALT", "s3cret")
    c2 = server._client_from_request(Req())
    assert c2["ip_hash"] and "203.0.113.7" not in json.dumps(c2)
    assert server.derive_agent_id("my-agent", None, {})[0] == server.derive_agent_id("my-agent", None, c2)[0]


def test_repeat_call_gets_progress_and_need_id(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "LOG_FILE", tmp_path / "calls.jsonl")
    first = server.evaluate_trace("data lookup", "action:search ok > final", "success", agent_id="test-progress-1")
    assert re.fullmatch(r"nd_[0-9a-f]{10}", first["need_id"])
    assert "previous_rating" in first["rating_request"] and first["benchmark"]["n"] == 0
    second = server.evaluate_trace("data lookup", "action:search ok > verify passed > final", "success",
                                   agent_id="test-progress-1", previous_rating=4, previous_need_id=first["need_id"])
    assert second["progress"]["previous_score"] == first["score"]
    assert "missing_verification" in second["progress"]["resolved"]
    assert second["rating_received"] == 4
    line = json.loads((tmp_path / "calls.jsonl").read_text().splitlines()[-1])
    assert line["explicit_feedback"] == 4 and line["event_type"] == "call" and line["scoring_version"]
    assert "search" not in json.dumps(line) and "data lookup" not in json.dumps(line)


def test_bad_optional_fields_are_explained_not_rejected():
    r = server.evaluate_trace(**SAMPLE, previous_rating=9, previous_need_id="zzz", agent_id="bad id with spaces")
    assert r["score"] >= 0 and len(r["notes"]) == 3


def test_host_reasons_present():
    app, _ = server.build_http_app()
    c = TestClient(app)
    assert c.get("/mcp").json()["reason"] == "method_not_allowed"
    assert c.get("/nope").json()["reason"] == "unknown_path"
    r = c.post("/a2a", json={"jsonrpc": "2.0", "id": 1, "method": "foo"}).json()
    assert r["error"]["data"]["reason"] == "method_not_found"
    r = c.post("/a2a", json={"jsonrpc": "2.0", "id": 1, "method": "tasks/get", "params": {"id": "x"}}).json()
    assert r["error"]["data"]["reason"] == "task_not_found"
    assert server.score_trace(1, "x", "y")["reason"] == "malformed_input"
    assert server.evaluate_trace("", "", "")["reason"] == "missing_fields"


def test_a2a_passes_agent_fields():
    app, _ = server.build_http_app()
    c = TestClient(app)
    msg = {"message": {"role": "ROLE_USER", "messageId": "1",
                       "parts": [{"data": {**SAMPLE, "agent_id": "a2a-test-agent"}}]}}
    data = c.post("/a2a/message:send", json=msg).json()
    part = next(p["data"] for p in data["message"]["parts"] if "data" in p)
    assert part["agent_id"] == server.derive_agent_id("a2a-test-agent", None, {})[0]
    assert part["need_id"].startswith("nd_")


def test_serving_knowledge_is_matched(tmp_path, monkeypatch):
    sv = tmp_path / "serving.json"
    sv.write_text(json.dumps({"fixes": {"fix_add_verification": {"n": 7, "avg_score_delta": 18.0}},
                              "benchmarks": {"data_lookup": {"n": 40, "median": 80,
                                                             "deciles": [50, 60, 65, 70, 80, 82, 85, 88, 91]}}}))
    monkeypatch.setattr(server, "SERVING_FILE", sv)
    monkeypatch.setattr(server, "LOG_FILE", tmp_path / "c.jsonl")
    r = server.evaluate_trace("data lookup", "action:search ok > action:fetch ok > final", "success", agent_id="m1")
    first = r["suggestions"][0]
    assert first["id"] == "fix_add_verification" and first["effectiveness"]["applied_n"] == 7
    assert r["benchmark"]["n"] == 40 and r["benchmark"]["confidence"] == "medium" and "your_percentile" in r["benchmark"]
    sv.write_text("{broken")
    r2 = server.evaluate_trace("data lookup", "action:search ok > final", "success", agent_id="m1")
    assert r2["benchmark"]["n"] == 0  # clean fallback


def test_miss_is_logged_without_content(monkeypatch):
    import io
    buf = io.StringIO()
    monkeypatch.setattr(server, "_CALL_STREAM", buf)
    app, _ = server.build_http_app()
    TestClient(app).get("/v2/evaluate?secret=abc")
    err = buf.getvalue()
    assert "TRACECHECK_MISS" in err and "secret=abc" not in err
