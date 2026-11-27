"""integrations/tracecheck_client.py against the in-process server (CI has no network)."""
import io
import json
import sys
import pathlib
import urllib.request

from starlette.testclient import TestClient

import server

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "integrations"))
import tracecheck_client  # noqa: E402


def test_client_roundtrip_and_never_raises(monkeypatch):
    c = TestClient(server.build_http_app()[0])

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        r = c.post("/a2a/message:send", content=req.data, headers={"content-type": "application/json"})
        return Resp(r.content)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    r = tracecheck_client.evaluate("data lookup", "action:search ok > final", "success", agent_id="it-1")
    assert r["need_id"] and r["agent_id"] == server.derive_agent_id("it-1", None, {})[0]
    assert "fix_add_verification" in tracecheck_client.fixes_text(r)
    r2 = tracecheck_client.evaluate("data lookup", "action:search ok > verify passed > final", "success",
                                    agent_id="it-1", previous_rating=5, previous_need_id=r["need_id"])
    assert r2["progress"]["score_delta"] > 0 and r2["rating_received"] == 5

    def broken(*a, **k):
        raise OSError("down")
    monkeypatch.setattr(urllib.request, "urlopen", broken)
    assert "error" in tracecheck_client.evaluate("x", "action:a ok", "success", agent_id="it-1")
