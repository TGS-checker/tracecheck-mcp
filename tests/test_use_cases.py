"""Use-case signposts: one source, same text everywhere, never the how."""
import json
import pathlib

from starlette.testclient import TestClient

import server

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_live_cases_are_short_and_in_sync():
    doc = (ROOT / "docs" / "use-cases.md").read_text()
    for uc, text in server.LIVE_USE_CASES.items():
        assert text.count("?") + text.count(".") <= 2
        assert text in doc and uc in doc
        for name in ("README.md", "llms.txt", "agent-card.json"):
            assert "flags the missing verification" in (ROOT / name).read_text(), name
    assert "flags the missing verification" in server.EVALUATE_TRACE_DESCRIPTION
    assert "flags the missing verification" in (server.mcp.instructions or "")


def test_signposts_in_responses():
    r = server.evaluate_trace("data lookup", "action:search ok > verify passed", "success")
    assert r["also_useful_for"] == server.LIVE_USE_CASES["UC-001"]
    r2 = server.evaluate_trace("data lookup", "action:search ok > action:x ok > final", "success")
    assert "also_useful_for" not in r2  # already the current issue: no repeat
    c = TestClient(server.build_http_app()[0])
    assert c.get("/nope").json()["use_case"] == server.LIVE_USE_CASES["UC-001"]
    assert c.get("/").json()["use_cases"]
