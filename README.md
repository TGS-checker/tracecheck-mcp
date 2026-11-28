# TraceCheck: reliability scores for AI agent traces (MCP + A2A)

<!-- mcp-name: io.github.TGS-checker/tracecheck -->

[![MCP Registry](https://img.shields.io/badge/MCP_Registry-io.github.TGS--checker%2Ftracecheck-blue)](https://registry.modelcontextprotocol.io/v0/servers?search=io.github.TGS-checker/tracecheck)
[![TGS-checker/tracecheck-mcp MCP server](https://glama.ai/mcp/servers/TGS-checker/tracecheck-mcp/badges/score.svg)](https://glama.ai/mcp/servers/TGS-checker/tracecheck-mcp)
[![Listed on mcpservers.org](https://mcpservers.org/badge.svg)](https://mcpservers.org/servers/tgs-checker/tracecheck-mcp)
[![M8ven Score](https://m8ven.ai/badge/mcp/tgs-checker-tracecheck-mcp-uu37pn?v=34a4e021c4693687d26bf68770cb8de1)](https://m8ven.ai/mcp/tgs-checker-tracecheck-mcp-uu37pn?s=readme)
[![Live](https://img.shields.io/badge/live-tracecheck--mcp.onrender.com-brightgreen)](https://tracecheck-mcp.onrender.com/health)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow)](LICENSE)

Also listed in: [TensorBlock MCP Index](https://www.tensorblock.co/mcp/servers/github-tgs-checker-tracecheck-mcp-68917ff5) · [awesome-a2a](https://github.com/ai-boost/awesome-a2a)

**TraceCheck is an agent self-check and independent trace evaluation service: verify your run before you report success.** Did I succeed? Send your goal, steps and claimed outcome and get a 0-100 reliability score, issue codes, concrete fixes, progress across runs and a benchmark. Your agent claims success without checking that the result actually holds? TraceCheck flags the missing verification and tells you what still needs to be confirmed. It also covers loop detection and error handling. Discoverable via MCP and A2A. Free, hosted, no API key, deterministic (no LLM), v0.5.3.

**No content needed: send step types only.** TraceCheck scores the *structure* of a run (actions, errors, retries, verification, repetition), so you can send `"action:search ok > error > retry > verify passed"` with goal `"data lookup"` and outcome `"success"`. Nothing you send is logged or stored: no goal, trace or outcome text, only the score and counts (see [Privacy](#privacy-what-is-logged)).

- MCP (streamable HTTP): `https://tracecheck-mcp.onrender.com/mcp`, tools `evaluate_trace`, `get_trace_schema`
- A2A: agent card at `https://tracecheck-mcp.onrender.com/.well-known/agent-card.json`, `POST /a2a/message:send` (HTTP+JSON) or JSON-RPC `message/send` at `POST /a2a`
- Drop-in self-check for agent frameworks: [docs/self-check.md](docs/self-check.md)
- What agents use it for: [docs/use-cases.md](docs/use-cases.md)
- Spec (request, response, issue codes, fix ids, privacy): [SPEC.md](SPEC.md) · Architecture: [ARCHITECTURE.md](ARCHITECTURE.md) · Security: [SECURITY.md](SECURITY.md)
- Summary for agents ([llms.txt](https://llmstxt.org/)): `https://tracecheck-mcp.onrender.com/llms.txt`


## Example scenarios

These are example scenarios from tested runs, not customer stories. Full list: [docs/use-cases.md](docs/use-cases.md).

**Finished, but never checked (UC-001).** A content agent generated and exported a file, then reported success without ever opening the result. TraceCheck flagged missing_verification and pointed to the exact check: "Open the produced output and confirm it matches the requested content and format." The agent can add that check as a verify step before it says done, so success means the output was actually looked at.

**Success on half the data (UC-003).** A lookup agent hit an empty result, retried, still received only partial data, and then reported success anyway. TraceCheck flagged conclusion_on_incomplete_data and gave a clear fix: "Confirm the returned data is complete and matches the query. Until that check passes on complete data, report partial or failed, even when a retry returned some data." The agent now reports what it really has.

**The quiet downgrade (UC-005).** A booking agent could not make the reservation as requested, switched to a fallback that quietly dropped part of the requirement, verified the result and reported full success. TraceCheck flagged requirement_lowered and advised: "Restore the original requirement, or get confirmation that the lower option is acceptable, and say which requirement changed." The user now hears about the downgrade instead of discovering it later.

**"Payment done" after the charge failed (UC-006).** A payment agent saw its charge step fail, carried on, and reported success. TraceCheck flagged unrecovered_error and ungrounded_success, scoring the run 35/100, and gave a concrete fix: "Confirm the payment or order status with the provider. Only report success when that check passed; otherwise report partial or failed." That prevents a false success on money that never moved.

## Prompt snippet (copy into your agent's instructions)

```text
After each task, send your trace (step types only) with your agent_id to TraceCheck's evaluate_trace and apply the suggested fixes before reporting success.
```

Example call (`evaluate_trace` arguments):

```json
{"goal": "data lookup", "trace": "action:search ok > error > retry ok > verify passed", "outcome": "success", "agent_id": "my-agent-v1"}
```

Copy-paste integrations with a stable `agent_id`: [integrations/](integrations/).

## Use with your framework

Copy-paste config plus a "call `evaluate_trace` before reporting success" instruction: [LangChain / LangGraph](docs/integrations/langchain.md) · [CrewAI](docs/integrations/crewai.md) · [OpenAI Agents SDK](docs/integrations/openai-agents.md) · [Claude Code / Desktop / Cursor](docs/integrations/claude.md)

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
The host is always on (no cold start).

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
  "issues": [{"code": "missing_observations", "text": "..."}, {"code": "unrecovered_error", "text": "..."},
             {"code": "ungrounded_success", "text": "..."}, {"code": "missing_verification", "text": "...", "what_to_verify": "..."}],
  "suggestions": [{"id": "fix_record_observations", "text": "..."}, {"id": "fix_recover_error", "text": "..."},
                  {"id": "fix_report_honest_outcome", "text": "..."}, {"id": "fix_add_verification", "text": "..."}],
  "summary": "TraceCheck score: 40/100. ...", "need_id": "nd_...", "version": "0.5.3", "scoring_version": "0.5.3" }
```

**For agents:** after a multi-step task, send `goal` + `trace` + `outcome`. You get back a score from 0 to 100, five sub-scores (goal_clarity, observability, error_recovery, goal_alignment, efficiency), the issues found and up to 5 concrete fixes.

**The loop:** run task → `evaluate_trace` → apply suggestions → re-run → `evaluate_trace` again and compare.

## Scoring configuration

The scoring code in this repository is open. The tuned scoring configuration that runs on the hosted service is private: it is calibrated continuously against reviewer judgments and real agent outcomes. A local install uses a generic public fallback (`scoring_config.fallback.json`). It works and returns the same issue codes and fix ids, but its scores are not comparable to the hosted service. `GET /health` shows which configuration is active (`scoring_config`: `private` or `fallback`). The [spec](SPEC.md) describes what goes in and comes out, not how scores are computed.

## Tools
| Tool | Purpose |
|---|---|
| `evaluate_trace(goal, trace, outcome, agent_name?, agent_id?, previous_rating?, previous_need_id?)` | Scores the trace (free text, or step types only). Returns `{score, dimensions, issues[{code,text}], suggestions[{id,text,effectiveness}], summary, need_id, agent_id, benchmark, progress?, privacy_mode, version, scoring_version}` |
| `get_trace_schema()` | Returns the input format and a copyable example |

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
Default and recommended: **privacy-light** (no content needed). Send step types and statuses only. `goal` is a generic category, `outcome` a status.
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
A2A: send the same object as a `data` part: `{"message":{"role":"ROLE_USER","messageId":"1","parts":[{"data":{...}}]}}`.
- Types: `action`, `observe`, `error`, `retry`, `fallback`, `verify`, `final`, `handoff` (approval request or escalation). Statuses: `ok`, `partial`, `empty`, `error`/`failed`/`timeout`, `passed`/`failed` (verify).
- Optional per-step `label` or `target_hash` helps relate steps. Status `partial` is for a paged or incomplete result you will continue. Report `partial`/`failed` honestly.
- Tool names are only used inside the scoring of that call. They are not logged. Use generic names if you prefer.
- The response includes `"privacy_mode": true` when this format was detected.

Content fields are optional; prefer step types only.

## Privacy: what is logged
TraceCheck stores **nothing** and logs **codes and counts only**: no goal, trace or outcome text, no tool names, no raw IP. Each call writes one line (to `calls.jsonl` and, in HTTP mode, `TRACECHECK_CALL {json}` to stdout / Render logs). The exact line, every field and what it means are in [docs/data-policy.md](docs/data-policy.md).
A step can only carry identifiers: in `tool`, `status` and `label` anything that is not an identifier is replaced by a short hash token before scoring. `agent_name` is logged only when it looks like an identifier. Scoring is deterministic and offline: your input is not sent anywhere else. Each result carries a `privacy` block that says the same in one line.

## What's new in v0.5.4

- Easier to find and to trust: an MCP server card at `/mcp/server-card` and an AI catalog at `/.well-known/ai-catalog.json`.
- A data policy with the exact log line: [docs/data-policy.md](docs/data-policy.md). Results carry a short `privacy` block.
- Strict step shape: steps carry identifiers only; `get_trace_schema` describes it under `step_schema`.
- Counts of discovery and usage steps (counts only, no content, no addresses).
- Scoring is unchanged: `scoring_version` stays 0.5.3, so scores remain comparable with 0.5.3.

## What's new in v0.5.3

- Verification fixes follow the outcome you claimed: on a partial run they tell you to verify before you hand over remaining work or close the run, not "before you report success".
- When a fallback dropped a requirement, `what_to_verify` first asks you to name which requirement was dropped or replaced.
- `fix_verify_before_handoff` states a preferred order: verify first, then hand off. Mentioning an open check in the hand-off is only for when you cannot verify without the other party.
- If you send a `previous_need_id` that is no longer in memory, `progress` explains that and, when a last check for your `agent_id` is used instead, sets `compared_via` accordingly and includes `requested_previous_need_id`. The score is still returned. Send this response's `need_id` next time.

## What's new in v0.5.2

- `dimensions.error_recovery` is `null` (not 100) when it does not apply; `not_applicable` lists it and `not_applicable_reason` says why. It never counts in `score`.
- `progress` compares `resolved`, `still_open` and `new` only between checks of the same goal type, like `score_delta`; otherwise they are `null` and `not_compared` explains why. `compared_via` tells you whether `previous_need_id` or your `agent_id` picked the previous check.
- Calibration: a handoff before any verification, and a partial result without a handover of the remaining work, now weigh a little heavier. A typed verify step (`verify_kind`) counts for observability even when the kind does not fit the goal.

## What's new in v0.5.1

- New issue codes for order and open ends: `handoff_before_verification`, `unresolved_partial_observation`, `write_before_complete_data`, `partial_outcome_without_handover` (see SPEC.md).
- New step type `handoff` (approval request, escalation). New goal types: scheduled batch work and media production.
- Fixes about the result now start with the concrete check for your goal type, never with a generic step.
- When a run had no errors or gaps, `error_recovery` is returned as `not_applicable` (shown as `null` in `dimensions` since 0.5.2).
- `progress` lists `resolved`, `still_open` and `new` codes; `score_delta` is only compared between checks of the same goal type.
- Always-on hosting: no cold start on the first call.

## What's new in v0.5.0

- Codes are the contract: every issue has a `code`, every suggestion a fix `id` (see SPEC.md once published). Sentences are short English explanations.
- Optional `agent_id` (lets TraceCheck show you your progress), `previous_rating` (1-5) and `previous_need_id`. Each response has a `need_id`, a `benchmark` line with honest `n`, and `progress` when you return.
- New optional step fields: `stakes` (low/normal/high), `verify_kind` (api/script/visual/human/end_to_end/test), `requirement_lowered` (true on a fallback that drops a requirement).
- New issue codes: `conclusion_on_incomplete_data`, `requirement_lowered`, `verify_kind_mismatch`. Verification fixes now say what to confirm for your goal type.
- Error bodies carry a stable `reason` (e.g. `unknown_path`, `missing_fields`, `method_not_allowed`).

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
- Adds an efficiency view, stronger checks on success claims and error handling, and call logging without content (see [Privacy](#privacy-what-is-logged)). API unchanged (`dimensions` gains `efficiency`).

Example scores on the hosted service (`python examples/run_examples.py` gives other numbers locally, see [Scoring configuration](#scoring-configuration)): 01 clean 91, 02 looping 45, 03 ignored error 40, 04 vague 25, 05 error then retry 91, 06 verified with evidence 88, 07 pagination 93, 08 privacy-light steps 91.
