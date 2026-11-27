"""Host-principle behaviour: no bare 404s, A2A aliases, MCP reject logging, actionable help."""

import json

import pytest
from starlette.testclient import TestClient

import server

SAMPLE = {
    "goal": "data lookup",
    "trace": "action:search ok > error > retry > verify passed",
    "outcome": "success",
}


def _client():
    app, _ = server.build_http_app()
    return TestClient(app)


def _help(data: dict) -> dict:
    err = data.get("error")
    if isinstance(err, dict) and isinstance(err.get("data"), dict) and "example" in err["data"]:
        return err["data"]
    return data


def assert_actionable(data: dict) -> dict:
    payload = _help(data)
    assert payload.get("error"), data
    assert payload.get("hint"), data
    assert str(payload.get("docs", "")).endswith("/llms.txt"), data
    example = payload["example"]
    # One short example, no duplicated MCP/A2A blocks or how_to lists.
    assert "mcp" not in example and "a2a" not in example
    assert "how_to" not in payload
    assert "links" not in payload
    args = example.get("arguments") or example
    assert example.get("name", "evaluate_trace") == "evaluate_trace"
    assert "action:search ok" in str(args.get("trace", args))
    blob = json.dumps(payload)
    # No large duplicated blocks.
    assert blob.count("action:search ok") <= 2
    return payload


@pytest.fixture(scope="module")
def client():
    with _client() as c:
        yield c


def test_unknown_path_returns_actionable_json(client):
    r = client.get("/this-path-does-not-exist")
    assert r.status_code == 404
    assert_actionable(r.json())


def test_unknown_a2a_task_is_task_not_found(client):
    r = client.get("/a2a/tasks/does-not-exist")
    assert r.status_code == 404
    data = r.json()
    assert data["error"]["code"] == -32001
    assert "TaskNotFound" in data["error"]["message"]
    payload = assert_actionable(data)
    assert payload["taskId"] == "does-not-exist"


def test_post_card_url_and_v1_message_send_score(client):
    body = {"message": {"role": "user", "parts": [{"data": SAMPLE}]}}
    paths = [
        "/.well-known/agent-card.json",
        "/.well-known/agent.json",
        "/v1/message:send",
        "/.well-known/agent-card.json/v1/message:send",
        "/.well-known/agent-card.json/a2a",
    ]
    for path in paths:
        r = client.post(path, json=body)
        assert r.status_code == 200, (path, r.status_code, r.text[:300])
        data = r.json()
        blob = json.dumps(data)
        assert '"score"' in blob, (path, blob[:400])


def test_mcp_reject_logs_reason_and_returns_help(client):
    import io
    buf = io.StringIO()
    old = server._CALL_STREAM
    server._CALL_STREAM = buf
    try:
        r = client.post(
            "/mcp",
            content=b"not-json",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
    finally:
        server._CALL_STREAM = old
    assert r.status_code >= 400
    assert_actionable(r.json())
    logged = buf.getvalue()
    assert "TRACECHECK_MCP_REJECT" in logged
    line = next(l for l in logged.splitlines() if "TRACECHECK_MCP_REJECT" in l)
    payload = json.loads(line.split("TRACECHECK_MCP_REJECT", 1)[1])
    assert "reason" in payload and payload["reason"]
    assert payload.get("status") == r.status_code
    assert "not-json" not in line


def test_evaluate_trace_missing_fields_is_actionable():
    result = server.evaluate_trace()
    assert_actionable(result)


def test_malformed_score_trace_is_actionable():
    result = server.score_trace("find the cheapest direct flight", {"not": "a trace"}, "success claimed")
    assert_actionable(result)


def _mcp_headers(session=None):
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if session:
        h["mcp-session-id"] = session
    return h


def _rpc(client, method, params=None, sid=None, id_=1):
    r = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}},
        headers=_mcp_headers(sid),
    )
    return r


def test_get_mcp_returns_405_with_allow_post(client):
    r = client.get("/mcp", headers={"Accept": "application/json"})
    assert r.status_code == 405
    assert "POST" in r.headers.get("allow", "")
    data = r.json()
    assert_actionable(data)
    assert "POST" in (data.get("error") or "") + (data.get("hint") or "")
    assert "llms.txt" in data["docs"]


def test_get_mcp_with_sse_accept_is_405_and_opens_no_stream(client):
    """MCP clients that send Accept: text/event-stream must not hang on a server-initiated SSE stream."""
    import asyncio

    seen = []
    recorded = []

    async def fake_app(scope, receive, send):
        seen.append(True)
        raise AssertionError("GET /mcp must not reach the SSE stream handler")

    async def send(message):
        recorded.append(message)

    async def receive():
        return {"type": "http.disconnect"}

    async def run():
        mw = server.McpGetHelp(fake_app)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/mcp",
            "headers": [(b"accept", b"application/json, text/event-stream")],
        }
        await mw(scope, receive, send)

    asyncio.run(run())
    assert not seen
    assert recorded and recorded[0].get("status") == 405
    allow = {k: v for k, v in recorded[0].get("headers") or []}
    assert allow.get(b"allow") == b"POST"

    r = client.get("/mcp", headers={"Accept": "text/event-stream"})
    assert r.status_code == 405
    assert "POST" in r.headers.get("allow", "")
    assert "text/event-stream" not in r.headers.get("content-type", "")
    assert_actionable(r.json())


def test_health_includes_scoring_version(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["version"] == server.VERSION
    assert data["scoring_version"] == server.SCORING_VERSION


def test_unknown_path_mcpx_is_unknown_path_not_session(client):
    r = client.get("/mcpx")
    assert r.status_code == 404
    data = r.json()
    payload = assert_actionable(data)
    blob = json.dumps(data).lower()
    assert "session_not_found" not in blob
    assert payload.get("reason") == "unknown_path" or "unknown path" in payload["error"].lower()


def test_unknown_tool_lists_names_and_suggestion(client):
    init = _rpc(client, "initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "host-test", "version": "0"},
    })
    assert init.status_code in (200, 202)
    sid = init.headers.get("mcp-session-id")
    r = _rpc(client, "tools/call", {"name": "eval_trace", "arguments": {}}, sid=sid, id_=2)
    assert r.status_code == 200, r.text
    body = r.json()
    result = body.get("result") or {}
    assert result.get("isError") is True or result.get("is_error") is True
    text = json.dumps(body)
    assert "evaluate_trace" in text
    assert "get_trace_schema" in text
    assert "did you mean" in text.lower()
    assert "eval_trace" in text


def test_missing_fields_tool_result_is_error(client):
    init = _rpc(client, "initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "host-test", "version": "0"},
    })
    sid = init.headers.get("mcp-session-id")
    r = _rpc(client, "tools/call", {"name": "evaluate_trace", "arguments": {}}, sid=sid, id_=2)
    assert r.status_code == 200, r.text
    body = r.json()
    result = body.get("result") or {}
    assert result.get("isError") is True or result.get("is_error") is True
    # The payload still carries a short actionable example.
    text = " ".join(
        (c.get("text") or "") for c in (result.get("content") or [])
        if isinstance(c, dict)
    ) or json.dumps(body)
    assert "error" in text.lower()
    assert "evaluate_trace" in text or "action:search ok" in text


def test_schema_leads_with_privacy_light_example():
    schema = server.get_trace_schema()
    example = schema["example_call"]
    assert example["goal"] == "data lookup"
    assert example["outcome"] == "success"
    assert schema.get("recommended_format") == "privacy_light"
    assert "example_call_with_content" not in schema
    note = json.dumps(schema.get("content_fields") or "").lower()
    assert "optional" in note
    assert "label" in json.dumps(schema).lower() or "target_hash" in json.dumps(schema).lower()
