"""Input limits, body size and rate limiting: helpful errors, never a crash."""
from starlette.testclient import TestClient

import server


def test_oversized_trace_gets_helpful_limit_error():
    r = server.evaluate_trace("data lookup", [{"type": "action", "status": "ok"}] * 300, "success")
    assert r["reason"] == "input_too_large" and "max 200" in r["error"] and r["limits"]["max_steps"] == 200
    r = server.evaluate_trace("x" * 600, "action:a ok > verify passed", "success")
    assert r["reason"] == "input_too_large"


def test_body_too_large_413():
    app, _ = server.build_http_app()
    c = TestClient(app)
    r = c.post("/a2a/message:send", content=b"{" + b" " * 300000 + b"}", headers={"content-type": "application/json"})
    assert r.status_code == 413 and r.json()["reason"] == "body_too_large"


def test_per_agent_rate_limit(monkeypatch):
    monkeypatch.setattr(server, "RATE_LIMIT_PER_MIN", 3)
    server._RATE.clear()
    for _ in range(3):
        assert server.rate_limited("ag_test", now=1000.0) == 0
    assert server.rate_limited("ag_test", now=1001.0) > 0
    assert server.rate_limited("ag_other", now=1001.0) == 0
    server._RATE.clear()
