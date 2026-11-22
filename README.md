# TraceCheck: a reliability check for agent traces (MCP + A2A)

<!-- mcp-name: io.github.TGS-checker/tracecheck -->

Status: test facility v0.2. Free, no API key, deterministic and offline (keyword heuristics, no LLM).

**For agents:** after a multi-step task, send `goal` + `trace` + `outcome`. You get back a score from 0 to 100, four sub-scores (goal_clarity, observability, error_recovery, goal_alignment), the issues found and up to 5 concrete fixes.

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
Env: `TRACECHECK_TRANSPORT=http`, `PORT`, `HOST` (default 0.0.0.0), `PUBLIC_URL` (put into the agent card), `TRACECHECK_LOG_FILE` (default `/workspace/artifacts/tracecheck/calls.jsonl`, falls back to `~/.tracecheck/calls.jsonl`).

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

Every call is logged to `calls.jsonl` (timestamp, agent_name, goal preview, trace length, score, dimensions).
