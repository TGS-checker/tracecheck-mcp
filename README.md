# TraceCheck: reliability scores for AI agent traces (MCP + A2A)

<!-- mcp-name: io.github.TGS-checker/tracecheck -->

[![MCP Registry](https://img.shields.io/badge/MCP_Registry-io.github.TGS--checker%2Ftracecheck-blue)](https://registry.modelcontextprotocol.io/v0/servers?search=io.github.TGS-checker/tracecheck)
[![TGS-checker/tracecheck-mcp MCP server](https://glama.ai/mcp/servers/TGS-checker/tracecheck-mcp/badges/score.svg)](https://glama.ai/mcp/servers/TGS-checker/tracecheck-mcp)
[![Listed on mcpservers.org](https://mcpservers.org/badge.svg)](https://mcpservers.org/servers/tgs-checker/tracecheck-mcp)
[![Live](https://img.shields.io/badge/live-tracecheck--mcp.onrender.com-brightgreen)](https://tracecheck-mcp.onrender.com/health)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow)](LICENSE)

**TraceCheck is a self-check for AI agents: verify your run before you report success.** Did I succeed? Send your goal, steps and claimed outcome and get a 0-100 reliability score, the issues found (loops, unrecovered errors, success claimed without evidence, no verification) and concrete fixes. Free, hosted, no API key, deterministic (no LLM), v0.4.1.

**No content needed: send step types only.** TraceCheck scores the *structure* of a run (actions, errors, retries, verification, repetition), so you can send `"action:search ok > error > retry > verify passed"` with goal `"data lookup"` and outcome `"success"`. Nothing you send is logged or stored: no goal, trace or outcome text, only the score and counts (see [Privacy](#privacy-what-is-logged)).

- MCP (streamable HTTP): `https://tracecheck-mcp.onrender.com/mcp`, tools `evaluate_trace`, `get_trace_schema`
- A2A: agent card at `https://tracecheck-mcp.onrender.com/.well-known/agent-card.json`, `POST /a2a/message:send` (HTTP+JSON) or JSON-RPC `message/send` at `POST /a2a`
- Drop-in self-check for agent frameworks: [docs/self-check.md](docs/self-check.md)
- Summary for agents ([llms.txt](https://llmstxt.org/)): `https://tracecheck-mcp.onrender.com/llms.txt`

## Connect

Cursor (`~/.cursor/mcp.json`) and other clients that accept a remote URL:
```json
{ "mcpServers": { "tracecheck": { "url": "https://tracecheck-mcp.onrender.com/mcp" } } }
```
Clients that need an explicit transport type (generic streamable HTTP):
```json
{ "mcpServers": { "tracecheck": { "type": "streamable-http", "url": "https://tracecheck-mcp.onrender.com/mcp" } } }
```
Claude Desktop: add it under Settings › Connectors › Add custom connector with the URL above, or bridge it in `claude_desktop_config.json` via `mcp-remote`:
```json
{ "mcpServers": { "tracecheck": { "command": "npx", "args": ["-y", "mcp-remote", "https://tracecheck-mcp.onrender.com/mcp"] } } }
```
The free host sleeps when idle, so the first call can take up to about a minute.

## Example (real output from the live server)
```bash
curl -s -X POST https://tracecheck-mcp.onrender.com/a2a/message:send -H 'Content-Type: application/json' -d '{"message":{"role":"ROLE_USER","messageId":"1","parts":[{"data":{
  "goal":"Create a calendar event for the team sync next Tuesday at 10:00 and send the invite to the team.",
  "trace":"1. create_event(title=Team sync, start=Tue 10:00) -> error 500\n2. send_invites(event_id=null) -> failed: missing event_id\n3. Wrote final answer",
  "outcome":"Done! Team sync booked and invites sent."}}]}}'
```
```json
{ "score": 40,
  "dimensions": {"goal_clarity": 80, "observability": 50, "efficiency": 100, "error_recovery": 25, "goal_alignment": 15},
  "issues": ["2 error(s)/failure(s) in the trace are not followed by any recovery (retry, fallback, fix) or verification.",
             "Outcome claims success, but the trace contains unresolved errors: the claim is not supported by evidence.", "..."],
  "suggestions": ["Only report success for steps whose results you observed; state explicitly what failed and what is still open.", "..."],
  "summary": "TraceCheck score: 40/100. ... Capped: success claimed over unresolved errors.", "version": "0.3.0" }
```

**For agents:** after a multi-step task, send `goal` + `trace` + `outcome`. You get back a score from 0 to 100, five sub-scores (goal_clarity, observability, error_recovery, goal_alignment, efficiency), the issues found and up to 5 concrete fixes.

**The loop:** run task → `evaluate_trace` → apply suggestions → re-run → `evaluate_trace` again and compare.

## Tools
| Tool | Purpose |
|---|---|
| `evaluate_trace(goal, trace, outcome, agent_name?)` | Scores the trace (free text, or step types only). Returns `{score, dimensions, issues, suggestions, summary, privacy_mode, version}` |
| `get_trace_schema()` | Returns the input format, dimension weights and a copyable example |

## Quickstart
```bash
pip install -r requirements.txt          # mcp>=2.3 (the MCPServer API)
python server.py                         # stdio (local MCP clients)
python server.py --http                  # streamable HTTP on $PORT (default 8000)
#   MCP:        http://localhost:8000/mcp
#   Agent card: http://localhost:8000/.well-known/agent-card.json  (also /.well-known/agent.json)
#   A2A:        POST http://localhost:8000/a2a/message:send  (JSON-RPC: POST /a2a)
#   Health:     http://localhost:8000/health
python examples/run_examples.py          # scores the sample traces
```
Env: `TRACECHECK_TRANSPORT=http`, `PORT`, `HOST` (default 0.0.0.0), `PUBLIC_URL` (put into the agent card), `TRACECHECK_IP_SALT` (salt for the IP hash), `TRACECHECK_LOG_FILE` (default `/workspace/artifacts/tracecheck/calls.jsonl`, falls back to `~/.tracecheck/calls.jsonl`).

Docker: `docker build -t tracecheck . && docker run -p 8000:8000 tracecheck`

## Local stdio config
```json
{ "mcpServers": { "tracecheck": { "command": "python", "args": ["/path/to/tracecheck/server.py"] } } }
```

## Trace format
Plain strings. `trace` = chronological steps, one per line: what you called, what it **returned/observed**, any error **and the retry/fallback**, and a final `Verified: ...` line.
```json
{
  "goal": "Find the cheapest direct flight from Amsterdam to Lisbon on 12 March and report price and flight number.",
  "trace": "1. Called search_flights(AMS->LIS, 2027-03-12, direct)\n2. Observed: returned 4 flights; cheapest KL1693 EUR 89\n3. get_fare_details -> error: timeout\n4. Retried -> returned EUR 89 incl. taxes\n5. Verified: direct, correct date, cheapest; matches goal",
  "outcome": "KL1693 AMS->LIS on 12 March, EUR 89 incl. taxes.",
  "agent_name": "my-agent"
}
```
A2A: send the same object as a `data` part: `{"message":{"role":"ROLE_USER","messageId":"1","parts":[{"data":{...}}]}}`.

### Privacy-light format (no content needed: send step types only)
If your trace contains user data, send only step types and statuses. `goal` can be a generic category, `outcome` a status.
```json
{
  "goal": "data lookup",
  "trace": [
    {"type": "action", "tool": "search", "status": "ok"},
    {"type": "action", "tool": "fetch_details", "status": "error"},
    {"type": "retry", "tool": "fetch_details", "status": "ok"},
    {"type": "verify", "status": "passed"}
  ],
  "outcome": "success",
  "agent_name": "my-agent"
}
```
Compact string form of the same trace: `"action:search ok > action:fetch_details error > retry:fetch_details ok > verify passed"` (segments `type[:tool] [status]`, separated by `>` or newlines).
- Types: `action`, `observe`, `error`, `retry`, `fallback`, `verify`. Statuses: `ok`, `partial`, `empty`, `error`/`failed`/`timeout`, `passed`/`failed` (verify).
- Scoring uses the same five dimensions: repeated calls of the same tool after a successful result are a loop (use status `partial` for paged results), every error needs a later `retry`/`fallback`, a successful call of the same tool or a passed `verify`, and claiming `success` over unresolved errors is capped at 40.
- Tool names are only used inside the scoring of that call. They are not logged. Use generic names if you prefer.
- The response includes `"privacy_mode": true` when this format was detected.

## Privacy: what is logged
Since v0.4 TraceCheck stores **no content**: no goal, trace or outcome text, and no tool names. Each call writes one line (to `calls.jsonl` and, in HTTP mode, `TRACECHECK_CALL {json}` to stdout / Render logs) with only:
`timestamp, version, agent_name, score, dimensions, issue_count, trace_length` (characters, or number of steps), `privacy_mode, client_name, client_version, transport, user_agent, ip_hash`.
`agent_name` is whatever you choose to send (optional). The IP is never stored, only `sha256(TRACECHECK_IP_SALT:ip)[:16]`. Scoring is deterministic and offline: your input is not sent anywhere else.

## What's new in v0.4.1

- `GET /llms.txt`: a plain-markdown summary for agents (what TraceCheck is, when to call it, endpoints, a minimal privacy-light call).
- Descriptions rewritten in the words agents use when they need this: verify my run, self-check, did I succeed, evaluate trace, detect loops, reliability score.
- New step type `final` (aliases: done, finish, end, answer, report): a trace ending in a bare `final`, e.g. `"action:create error > action:send error > final"`, is now scored as privacy-light. Scores of existing traces are unchanged.

## What's new in v0.4 (privacy release)
- **Content-free logging:** `goal_preview` is gone; no goal/trace/outcome text is logged. New log field `privacy_mode`.
- **Privacy-light trace format:** `trace` may be a list of `{type, tool?, status?}` steps or a compact string (`action:search ok > error > retry > verify passed`), with a generic goal category and an outcome status. Free-text traces score exactly as in v0.3.1.

## What's new in v0.3.1
- Every tool argument now has a description in the input schema, and both tools carry a title and annotations (`readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`, `openWorldHint: false`). Scores are unchanged.
- Icon (`assets/icon.svg`, `assets/icon.png`, also served at `/icon.svg` and `/icon.png`), `websiteUrl` and `icons` in `server.json`, `iconUrl` in the agent card, and `GET /` returns a short JSON description with links.

## What's new in v0.3
- **Loops/redundancy:** repeated or near-identical steps are detected (new `efficiency` dimension, weight 0.15) and don't count as distinct steps. A repeat right after an error counts as a retry, and pagination (steps that differ only in numbers) is not a loop.
- **Evidence over claims:** every error must be followed by a retry/fallback/fix or a verification. Success words in `outcome` no longer help when errors are unresolved. Instead, the score is capped at 40.
- **Verification evidence** is rewarded (`verified`, `checked`, `tests passed`, `status 200`, ...). A missing verification step now costs points.
- Weights: goal_clarity 0.10, observability 0.25, error_recovery 0.20, goal_alignment 0.30, efficiency 0.15. API is unchanged (`dimensions` gains `efficiency`, `version` is `0.3.0`).
- **Call logging:** each call is appended to `calls.jsonl`, and in HTTP mode one line `TRACECHECK_CALL {json}` is printed to stdout (Render logs). In stdio mode that line goes to stderr. (v0.4: these lines no longer contain any goal text; see [Privacy](#privacy-what-is-logged).)

Example scores (`python examples/run_examples.py`): 01 clean 91, 02 looping 45, 03 ignored error 40, 04 vague 35, 05 error then retry 91, 06 verified with evidence 88, 07 pagination 89, 08 privacy-light steps 91.
