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
    mcp_call = example["mcp"]
    assert mcp_call["method"] == "tools/call"
    assert mcp_call["params"]["name"] == "evaluate_trace"
    assert "action:search ok" in mcp_call["params"]["arguments"]["trace"]
    assert "parts" in example["a2a"]["message"] or "data" in json.dumps(example["a2a"])
    assert len(payload["how_to"]) == 3
    blob = " ".join(payload["how_to"]).lower()
    assert "get_trace_schema" in blob
    assert "step types" in blob
    assert "score" in blob
    assert "/mcp" in payload["links"]["mcp"]
    assert "agent-card.json" in payload["links"]["a2a_agent_card"]
    assert "/health" in payload["links"]["health"]
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
