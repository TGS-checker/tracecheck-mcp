# TraceCheck self-check: score your agent's run at the end

Call TraceCheck once at the end of a multi-step run, log the score, and feed `suggestions` into the next run.
Free, no API key. The free host sleeps when idle, so allow up to about 60 s for the first call.

Input: `goal` (the task), `trace` (chronological steps, one per line: tool called, what it returned, errors **and** the retry/fallback, and a final `Verified: ...` line), `outcome` (what you reported), `agent_name` (optional, shows up in our usage count).
Output: `{score 0-100, dimensions, issues, suggestions, summary, privacy_mode, version}`.

**No content needed: send step types only.** If your run touches user data, use the privacy-light format: `goal` = a generic category (`"data lookup"`), `outcome` = `"success"`, `"partial"` or `"failed"`, and `trace` = step types and statuses only, as a list (`[{"type":"action","tool":"search","status":"ok"}, {"type":"error"}, {"type":"retry"}, {"type":"verify","status":"passed"}]`) or a compact string (`"action:search ok > error > retry > verify passed"`). TraceCheck never logs or stores goal, trace or outcome text, only the score and counts.

Using a framework? Copy-paste configs: [LangChain](integrations/langchain.md) · [CrewAI](integrations/crewai.md) · [OpenAI Agents SDK](integrations/openai-agents.md) · [Claude Code / Desktop / Cursor](integrations/claude.md).

## Option 1: MCP (Python, `pip install "mcp>=2.3"`)
```python
import asyncio, json
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

TRACECHECK_MCP = "https://tracecheck-mcp.onrender.com/mcp"

async def tracecheck(goal: str, trace: str, outcome: str, agent_name: str) -> dict:
    async with streamable_http_client(TRACECHECK_MCP) as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool("evaluate_trace", {
                "goal": goal, "trace": trace, "outcome": outcome, "agent_name": agent_name})
            return json.loads(res.content[0].text)  # tool returns one JSON text block

# at the end of a run:
# report = asyncio.run(tracecheck(goal, "\n".join(steps), final_answer, "my-agent"))
# print(report["score"], report["suggestions"])
```

## Option 2: plain HTTP (A2A, standard library only)
```python
import json, urllib.request

TRACECHECK_A2A = "https://tracecheck-mcp.onrender.com/a2a/message:send"

def tracecheck(goal: str, trace: str, outcome: str, agent_name: str) -> dict:
    body = {"message": {"role": "ROLE_USER", "messageId": "1", "parts": [
        {"data": {"goal": goal, "trace": trace, "outcome": outcome, "agent_name": agent_name}}]}}
    req = urllib.request.Request(TRACECHECK_A2A, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)["message"]["parts"][0]["data"]
```
Same thing with curl:
```bash
curl -s -X POST https://tracecheck-mcp.onrender.com/a2a/message:send -H 'Content-Type: application/json' \
  -d '{"message":{"role":"ROLE_USER","messageId":"1","parts":[{"data":{"goal":"...","trace":"1. ...\n2. ...","outcome":"...","agent_name":"my-agent"}}]}}'
```
A2A JSON-RPC clients (v0.3) can send `message/send` to `POST https://tracecheck-mcp.onrender.com/a2a` with the same `data` part.

## Privacy-light examples (step types only)
Build the steps from your own run log without any content:
```python
steps = []
steps.append({"type": "action", "tool": "search", "status": "ok"})
steps.append({"type": "action", "tool": "fetch_details", "status": "error"})
steps.append({"type": "retry", "tool": "fetch_details", "status": "ok"})
steps.append({"type": "verify", "status": "passed"})
```
MCP (same session code as Option 1):
```python
res = await session.call_tool("evaluate_trace", {
    "goal": "data lookup", "trace": steps, "outcome": "success", "agent_name": "my-agent"})
```
A2A (HTTP+JSON, same as Option 2, `trace` may be the list or the compact string):
```bash
curl -s -X POST https://tracecheck-mcp.onrender.com/a2a/message:send -H 'Content-Type: application/json' \
  -d '{"message":{"role":"ROLE_USER","messageId":"1","parts":[{"data":{"goal":"data lookup","trace":"action:search ok > action:fetch_details error > retry:fetch_details ok > verify passed","outcome":"success","agent_name":"my-agent"}}]}}'
```
A2A JSON-RPC (`POST /a2a`):
```json
{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":{"kind":"message","role":"user","messageId":"1",
 "parts":[{"kind":"data","data":{"goal":"data lookup","trace":"action:search ok > error > retry > verify passed","outcome":"success"}}]}}}
```
Use status `partial` for a paged or incomplete result you will continue, and report `partial`/`failed` honestly.

## Tips
- Never let a TraceCheck failure break your run: wrap the call in `try/except` with a short timeout.
- Write real observations into the trace ("returned 4 rows", "status 200"), not intentions.
- Track the score per agent over time. A rising score after applying `suggestions` is the point.
