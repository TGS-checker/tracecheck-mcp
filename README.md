# TraceCheck: a reliability check for agent traces (MCP + A2A)

<!-- mcp-name: io.github.TGS-checker/tracecheck -->

Status: test facility v0.3. Free, no API key, deterministic and offline (keyword heuristics, no LLM).

**For agents:** after a multi-step task, send `goal` + `trace` + `outcome`. You get back a score from 0 to 100, five sub-scores (goal_clarity, observability, error_recovery, goal_alignment, efficiency), the issues found and up to 5 concrete fixes.

**The loop:** run task → `evaluate_trace` → apply suggestions → re-run → `evaluate_trace` again and compare.

## Tools
| Tool | Purpose |
|---|---|
| `evaluate_trace(goal, trace, outcome, agent_name?)` | Scores the trace. Returns `{score, dimensions, issues, suggestions, summary, version}` |
| `get_trace_schema()` | Returns the input format, dimension weights and a copyable example |

## Quickstart
```bash
pip install -r requirements.txt          # mcp>=2.3 (the MCPServer API)
python server.py                         # stdio (local MCP clients)
python server.py --http                  # streamable HTTP on $PORT (default 8000)
#   MCP:        http://localhost:8000/mcp
#   Agent card: http://localhost:8000/.well-known/agent-card.json  (also /.well-known/agent.json)
#   A2A:        POST http://localhost:8000/a2a/message:send
#   Health:     http://localhost:8000/health
python examples/run_examples.py          # scores the sample traces
```
Env: `TRACECHECK_TRANSPORT=http`, `PORT`, `HOST` (default 0.0.0.0), `PUBLIC_URL` (put into the agent card), `TRACECHECK_IP_SALT` (salt for the IP hash), `TRACECHECK_LOG_FILE` (default `/workspace/artifacts/tracecheck/calls.jsonl`, falls back to `~/.tracecheck/calls.jsonl`).

Docker: `docker build -t tracecheck . && docker run -p 8000:8000 tracecheck`

## MCP client config
Remote (once hosted):
```json
{ "mcpServers": { "tracecheck": { "type": "streamable-http", "url": "https://tracecheck-mcp.onrender.com/mcp" } } }
```
Local stdio:
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

## What's new in v0.3
- **Loops/redundancy:** repeated or near-identical steps are detected (new `efficiency` dimension, weight 0.15) and don't count as distinct steps. A repeat right after an error counts as a retry, and pagination (steps that differ only in numbers) is not a loop.
- **Evidence over claims:** every error must be followed by a retry/fallback/fix or a verification. Success words in `outcome` no longer help when errors are unresolved. Instead, the score is capped at 40.
- **Verification evidence** is rewarded (`verified`, `checked`, `tests passed`, `status 200`, ...). A missing verification step now costs points.
- Weights: goal_clarity 0.10, observability 0.25, error_recovery 0.20, goal_alignment 0.30, efficiency 0.15. API is unchanged (`dimensions` gains `efficiency`, `version` is `0.3.0`).
- **Call logging:** each call is appended to `calls.jsonl`, and in HTTP mode one line `TRACECHECK_CALL {json}` is printed to stdout (Render logs). In stdio mode that line goes to stderr. Fields: `timestamp, version, agent_name, score, dimensions, issue_count, trace_length, goal_preview, client_name, client_version, user_agent, ip_hash, transport`. The IP is never stored, only `sha256(TRACECHECK_IP_SALT:ip)[:16]`.

Example scores (`python examples/run_examples.py`): 01 clean 91, 02 looping 45, 03 ignored error 40, 04 vague 35, 05 error then retry 91, 06 verified with evidence 88, 07 pagination 89.
