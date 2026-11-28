# TraceCheck data policy

**TraceCheck is an independent, fast check for agents. It works from step types only: no content, no sensitive data.**
This page says exactly what the hosted service (`https://tracecheck-mcp.onrender.com`) receives, what it writes down and what it does not.

## What you send, what is kept

- You send a goal category, step types with optional identifiers and statuses, and an outcome status.
- **Stored: nothing.** There is no database and no user account. Results are computed and returned.
- **Logged: codes and counts only.** One line per check, with the fields listed below. Never the goal, the trace, the outcome or any step text.
- A step can only carry identifiers. In `tool`, `status` and `label` anything that is not an identifier is replaced by a short hash token before scoring, and labels are always reduced to a hash. Other fields are ignored.
- Free-text traces are accepted for compatibility, are scored in memory and are not logged. Prefer step types.

## The exact log line

One `TRACECHECK_CALL` line per evaluation (the same fields are written to a local `calls.jsonl` on the instance):

```
TRACECHECK_CALL {"event_id": "2fd1de1642144bbc", "event_type": "call", "ts_utc": "2026-11-28T09:35:59.066981Z", "timestamp": "2026-11-28T09:35:59.066981Z", "schema_version": 1, "server_version": "0.5.5", "version": "0.5.5", "scoring_version": "0.5.5", "agent_id": "ag_x", "agent_id_source": null, "agent_kind": "agent", "agent_name": "other", "client_family": "x", "goal_category": null, "arg_shape": "privacy_light", "step_count": 2, "error_count": 0, "retry_count": 0, "has_verify": true, "step_pattern": "ac>ve", "score": 67, "dimensions": {"goal_clarity": 85, "observability": 25, "efficiency": 60, "error_recovery": null, "goal_alignment": 100}, "issue_count": 1, "issue_codes": ["too_few_steps"], "fix_ids": ["fix_add_steps"], "need_id": null, "knowledge_match": false, "benchmark_given": false, "prev_score_same_agent_need": null, "score_delta": null, "issues_resolved": null, "explicit_feedback": null, "previous_need_id": null, "returning": null, "trace_length": 32, "privacy_mode": true, "context": null, "client_name": "x", "client_version": "1", "transport": "http", "user_agent": "pytest/1", "ip_hash": "abc"}
```

| Field | Meaning |
|---|---|
| `event_id`, `event_type`, `ts_utc`, `timestamp`, `schema_version`, `server_version`, `version`, `scoring_version` | Identifiers of the event and of the service version. |
| `agent_id`, `agent_id_source` | A pseudonymous id: a hash of the `agent_id` you chose (or of your name or client), never a raw value. |
| `agent_kind` | `agent`, `own_test`, `reviewer`, `checker`, `crawler` or `unknown`: a coarse label used to tell real use from tests and scanners. |
| `agent_name` | The name you sent, only if it looks like an identifier (letters, digits, `._:@/-`, up to 64 characters). Anything else is logged as `other`. |
| `client_family` | Coarse client family such as `python-httpx` or `claude-code`. |
| `client_name`, `client_version`, `user_agent` | What your MCP client or HTTP library reports about itself (client info and the `User-Agent` header, up to 200 characters). Do not put content there. |
| `ip_hash` | A salted hash of network address and user agent, only when the operator has configured a salt. The address itself is never logged. |
| `transport` | `http` or `stdio`. |
| `goal_category` | The generic category derived from your goal, not your goal text. |
| `arg_shape`, `step_count`, `error_count`, `retry_count`, `has_verify`, `step_pattern`, `trace_length`, `privacy_mode` | The shape of the trace: counts and type initials only, no tool names. |
| `score`, `dimensions`, `issue_count`, `issue_codes`, `fix_ids` | The result: numbers and public codes. |
| `need_id`, `previous_need_id`, `explicit_feedback`, `prev_score_same_agent_need`, `score_delta`, `issues_resolved`, `returning` | Progress across your runs, only when you send `agent_id` or `previous_need_id`. |
| `knowledge_match`, `benchmark_given` | Whether benchmark data was available for your check. |
| `context` | Only when you send `expected_checks`, `out_of_scope`, `in_scope` or `open_blockers`: counts only (`expected_checks_n`, `passed_checks_n`, `scope_given`, `scope_exceeded_n`, `open_blockers_n`). The check, tool and blocker ids themselves are never logged. Otherwise `null`. |

An argument of the wrong type gets a plain JSON error that names the argument only; the value you sent is not repeated in the answer or the log.

## Counters

For service health and for knowing whether the service is findable and used, the server counts events (for example: card fetched, tools listed, check completed) per day, per coarse client family and per kind. A `TRACECHECK_STAGE` line has the fields `event_type`, `ts_utc`, `server_version`, `day`, `stage`, `client_family`, `kind`, `n` and `u_day` (distinct visitors that day, counted with a hash that rotates daily and lives only in memory). Counts only: no addresses, no content.

## Other lines

- `TRACECHECK_MISS`: a request for something that does not exist (`reason`, `path`, `method`, `tool`, `client_family`). Used to add missing aliases.
- `TRACECHECK_MCP_REJECT`: a short reason code for a rejected MCP request. No request content.

## What we do not do

- No goal, trace, outcome or step text is logged or stored.
- No raw IP address is logged or stored.
- No account, key or cookie is needed or set.
- The service does not call other services with what you send.

The scoring code is open source. Questions or concerns: open an issue at https://github.com/TGS-checker/tracecheck-mcp/issues.
