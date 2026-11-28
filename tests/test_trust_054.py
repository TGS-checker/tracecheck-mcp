"""0.5.4: trust and discovery. Server card, AI catalog, stage counters, strict step shape, privacy block."""

import io
import json
import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import server

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://tracecheck.example"


@pytest.fixture(autouse=True)
def _public_url(monkeypatch):
    monkeypatch.setenv("PUBLIC_URL", BASE)


def _client():
    return TestClient(server.build_http_app()[0])


# --- server card -------------------------------------------------------------------
def test_server_card_matches_the_spec_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((Path(__file__).parent / "fixtures" / "server-card.schema.json").read_text())
    schema = {**schema, "$ref": "#/$defs/ServerCard"}
    card = _client().get("/mcp/server-card").json()
    jsonschema.validate(card, schema, format_checker=jsonschema.FormatChecker())
    assert len(card["description"]) <= 100
    assert card["version"] == server.VERSION
    assert card["remotes"] == [{"type": "streamable-http", "url": f"{BASE}/mcp"}]
    assert "tools" not in card  # the card never lists primitives


def test_server_card_headers_etag_and_304():
    c = _client()
    r = c.get("/mcp/server-card")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/mcp-server-card+json")
    assert "max-age" in r.headers["cache-control"] and "public" in r.headers["cache-control"]
    assert r.headers["access-control-allow-origin"] == "*"
    etag = r.headers["etag"]
    again = c.get("/mcp/server-card", headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.content == b""
    assert c.head("/mcp/server-card").status_code == 200
    # browser clients (Origin header): CORS per the card spec, ETag readable, preflight answered
    b = c.get("/mcp/server-card", headers={"Origin": "https://app.example"})
    assert b.headers["access-control-allow-origin"] == "*" and "etag" in b.headers["access-control-expose-headers"].lower()
    pre = c.options("/mcp/server-card", headers={"Origin": "https://app.example", "Access-Control-Request-Method": "GET",
                                                 "Access-Control-Request-Headers": "if-none-match"})
    assert pre.status_code == 200 and "GET" in pre.headers["access-control-allow-methods"]
    assert c.get("/mcp/server-card", headers={"If-None-Match": '"other"'}).status_code == 200


def test_server_card_is_not_swallowed_by_the_mcp_endpoint_handling():
    c = _client()
    assert c.get("/mcp/server-card").status_code == 200
    assert c.get("/mcp").status_code == 405  # the real endpoint keeps its behaviour
    assert c.get("/mcp/other").status_code in (404, 405)


def test_server_card_description_is_the_core_in_short():
    d = server.SERVER_CARD_DESCRIPTION
    for word in ("weight", "threshold", "regex", "pattern"):
        assert word not in d.lower()


# --- AI catalog --------------------------------------------------------------------
def test_ai_catalog_shape():
    c = _client()
    r = c.get("/.well-known/ai-catalog.json")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/ai-catalog+json")
    doc = r.json()
    assert doc["specVersion"] == server.AI_CATALOG_SPEC_VERSION
    types = {e["type"]: e for e in doc["entries"]}
    assert types["application/mcp-server-card+json"]["url"] == f"{BASE}/mcp/server-card"
    assert types["application/a2a-agent-card+json"]["url"] == f"{BASE}/.well-known/agent-card.json"
    ids = [e["identifier"] for e in doc["entries"]]
    assert len(set(ids)) == len(ids)
    assert all(re.match(r"^urn:air:[a-z0-9.\-]+:[a-z0-9._\-]+:[a-z0-9._\-]+$", i) for i in ids)
    assert "tools" not in json.dumps(doc).lower().replace("evaluation", "")
    etag = r.headers["etag"]
    assert c.get("/.well-known/ai-catalog.json", headers={"If-None-Match": etag}).status_code == 304


def test_agent_card_has_no_test_label():
    card = json.loads((ROOT / "agent-card.json").read_text())
    assert "(test)" not in json.dumps(card).lower()
    assert card["version"] == server.VERSION


# --- strict step shape -------------------------------------------------------------
def test_free_text_in_steps_becomes_hash_tokens():
    secret = "Send the Q3 numbers to jane@example.com password hunter2"
    steps = server._parse_steps([
        {"type": "action", "tool": secret, "status": secret, "label": secret, "note": secret, "content": secret},
        {"type": "verify", "status": "passed"}])
    blob = json.dumps(steps)
    assert "jane" not in blob and "hunter2" not in blob and "Q3" not in blob
    assert steps[0]["tool"].startswith("t_") and steps[0]["status"].startswith("s_") and steps[0]["label"].startswith("l_")
    assert set(steps[0]) <= {"type", "tool", "status", "label", "stakes", "verify_kind", "requirement_lowered"}


def test_identifiers_pass_unchanged_and_equal_text_stays_equal():
    a = server._parse_steps([{"type": "action", "tool": "search_web", "status": "ok", "label": "x"}])[0]
    assert a["tool"] == "search_web" and a["status"] == "ok"
    t1 = server._parse_steps([{"type": "action", "tool": "Do the thing now"}])[0]["tool"]
    t2 = server._parse_steps([{"type": "action", "tool": "do the thing now"}])[0]["tool"]
    assert t1 == t2 and server._parse_steps([{"type": "action", "tool": "other thing"}])[0]["tool"] != t1


def test_loop_detection_still_works_with_hashed_labels():
    base = {"type": "action", "tool": "shell", "status": "ok"}
    same = server._parse_steps([{**base, "label": "install deps"}, {**base, "label": "install deps"}])
    diff = server._parse_steps([{**base, "label": "build"}, {**base, "label": "test"}])
    assert server._count_identical_repeats(same)[0] == 1
    assert server._count_identical_repeats(diff)[0] == 0


def test_schema_documents_the_strict_step_shape():
    sch = server.get_trace_schema()
    assert "step_schema" in sch and "identifier" in sch["step_schema"]["tool"]


# --- privacy block and log hygiene -------------------------------------------------
def test_result_carries_a_privacy_block():
    res = server.score_trace("data lookup", "action:search ok > verify passed", "success")
    assert res["privacy"]["stored"] == "nothing"
    assert res["privacy"]["policy"].endswith("docs/data-policy.md")
    assert (ROOT / "docs" / "data-policy.md").is_file()


def test_agent_name_is_logged_only_when_it_is_an_identifier():
    assert server._log_agent_name("pod-fixture-gw") == "pod-fixture-gw"
    assert server._log_agent_name("please summarise my private notes") == "other"
    assert server._log_agent_name(None) == "unknown"
    assert server._log_agent_name("x" * 80) == "other"


def test_call_log_line_has_exactly_the_documented_fields(capsys, monkeypatch):
    monkeypatch.setattr(server, "_CALL_STREAM", io.StringIO())
    res = server.score_trace("data lookup", "action:search ok > verify passed", "success")
    server.record_call("action:search ok > verify passed", "my secret agent name with spaces", res,
                       {"user_agent": "pytest/1", "ip_hash": "abc"}, {"agent_id": "ag_x"})
    line = server._CALL_STREAM.getvalue().strip()
    entry = json.loads(line.split(" ", 1)[1])
    assert entry["agent_name"] == "other"
    assert set(entry) >= set(DOCUMENTED_CALL_FIELDS)
    doc = (ROOT / "docs" / "data-policy.md").read_text()
    for field in entry:
        assert f"`{field}`" in doc, f"{field} is logged but not in docs/data-policy.md"


DOCUMENTED_CALL_FIELDS = ["event_type", "agent_name", "score"]


# --- stage counters ----------------------------------------------------------------
@pytest.fixture
def stage(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(server, "_CALL_STREAM", out)
    monkeypatch.setitem(server._STAGE_STATE, "day", None)
    server._STAGE_STATE["delta"], server._STAGE_STATE["visitors"] = {}, {}
    monkeypatch.setattr(server, "STAGE_FLUSH_SECONDS", 10 ** 9)
    yield out
    server._STAGE_STATE["delta"], server._STAGE_STATE["visitors"] = {}, {}


def _lines(out):
    return [json.loads(l.split(" ", 1)[1]) for l in out.getvalue().splitlines() if l.startswith("TRACECHECK_STAGE ")]


def test_stage_counters_count_discovery_and_mcp_steps(stage):
    with _client() as c:
        _discovery_and_mcp(c)
    server.flush_stages()
    rows = _lines(stage)
    stages = {r["stage"] for r in rows}
    assert {"server_card", "ai_catalog", "a2a_card", "miss", "mcp_initialize", "mcp_tools_list"} <= stages
    assert all(r["kind"] == "crawler" and r["client_family"] == "scanner-x" for r in rows)
    assert all(r["u_day"] == 1 and r["n"] >= 1 for r in rows)
    assert server.flush_stages() == 0  # deltas reset after a flush


def _discovery_and_mcp(c):
    h = {"User-Agent": "scanner-x/1.0", "X-Forwarded-For": "203.0.113.9"}
    c.get("/mcp/server-card", headers=h)
    c.get("/.well-known/ai-catalog.json", headers=h)
    c.get("/.well-known/agent-card.json", headers=h)
    c.get("/nope", headers=h)
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
    c.post("/mcp", json=init, headers={**h, "Accept": "application/json, text/event-stream"})
    c.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
           headers={**h, "Accept": "application/json, text/event-stream"})


def test_stage_lines_never_contain_ips_or_content(stage):
    with _client() as c:
        c.get("/mcp/server-card", headers={"X-Forwarded-For": "198.51.100.77", "User-Agent": "tester/2"})
        c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "My secret question 12345"},
               headers={"X-Forwarded-For": "198.51.100.77", "Accept": "application/json, text/event-stream"})
    server.flush_stages()
    text = stage.getvalue()
    assert "198.51.100" not in text and "secret" not in text.lower() and "12345" not in text
    assert {r["stage"] for r in _lines(stage)} <= set(server.STAGES)


def test_evaluate_trace_is_counted_with_unique_visitors(stage):
    res = server.score_trace("data lookup", "action:search ok > verify passed", "success")
    for who in ("h1", "h1", "h2"):
        server.record_call("action:search ok > verify passed", "agent-a", res, {"user_agent": "py/1", "ip_hash": who}, {})
    server.flush_stages()
    row = [r for r in _lines(stage) if r["stage"] == "evaluate_trace"][0]
    assert row["n"] == 3 and row["u_day"] == 2


def test_stage_keys_are_capped(stage, monkeypatch):
    monkeypatch.setattr(server, "_STAGE_MAX_KEYS", 5)
    for i in range(20):
        server.count_stage("root", f"agent{i}/1")
    server.flush_stages()
    rows = _lines(stage)
    assert len(rows) <= 6 and sum(r["n"] for r in rows) == 20


def test_data_policy_example_line_is_a_real_call_line():
    doc = (ROOT / "docs" / "data-policy.md").read_text()
    m = re.search(r"^TRACECHECK_CALL (\{.*\})$", doc, re.M)
    example = json.loads(m.group(1))
    out = io.StringIO()
    old = server._CALL_STREAM
    server._CALL_STREAM = out
    try:
        res = server.score_trace("data lookup", "action:search ok > verify passed", "success")
        server.record_call("action:search ok > verify passed", "my agent", res,
                           {"user_agent": "pytest/1", "ip_hash": "abc", "client_name": "x", "client_version": "1",
                            "transport": "http"}, {"agent_id": "ag_x"})
    finally:
        server._CALL_STREAM = old
    real = json.loads(out.getvalue().split(" ", 1)[1])
    assert set(example) == set(real), set(example) ^ set(real)  # the policy example lists exactly the logged fields


def test_stage_counters_are_flushed_on_lifespan_shutdown(stage):
    with _client() as c:
        c.get("/mcp/server-card", headers={"User-Agent": "tester/2"})
        assert _lines(stage) == []  # not flushed while running (interval not reached)
    assert any(r["stage"] == "server_card" for r in _lines(stage))
