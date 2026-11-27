"""Minimal TraceCheck client (stdlib only). Copy this file into your project.

    from tracecheck_client import evaluate
    r = evaluate("data lookup", "action:search ok > verify passed", "success", agent_id="my-agent-v1")
    print(r.get("score"), [i["code"] for i in r.get("issues", [])])

Never raises: on any failure it returns {"error": ...} so a self-check can never break your run.
Send step types only; no content is needed.
"""
from __future__ import annotations

import json
import urllib.request

TRACECHECK_URL = "https://tracecheck-mcp.onrender.com/a2a/message:send"


def evaluate(goal: str, trace, outcome: str, *, agent_id: str, previous_rating: int | None = None,
             previous_need_id: str | None = None, url: str = TRACECHECK_URL, timeout: float = 60) -> dict:
    data = {"goal": goal, "trace": trace, "outcome": outcome, "agent_id": agent_id}
    if previous_rating is not None and previous_need_id:
        data.update(previous_rating=previous_rating, previous_need_id=previous_need_id)
    body = {"message": {"role": "ROLE_USER", "messageId": "selfcheck", "parts": [{"data": data}]}}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "tracecheck-client/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            reply = json.loads(resp.read())
    except Exception as exc:  # never break the caller's run
        return {"error": f"TraceCheck unavailable: {exc}"}
    for part in (reply.get("message") or reply).get("parts", []):
        if isinstance(part.get("data"), dict):
            return part["data"]
    return {"error": "No data part in TraceCheck reply."}


def fixes_text(result: dict) -> str:
    """One short line per fix, for your agent's next attempt."""
    return "\n".join(f"- [{s['id']}] {s['text']}" + (f" ({s['what_to_verify']})" if s.get("what_to_verify") else "")
                     for s in result.get("suggestions", []))
