# TraceCheck trace format and response spec — v1

Status: v1, published with service 0.5.0 (scoring_version 0.5.0). Open spec: anyone may produce or consume this format.
This spec describes **what** goes in and comes out. It does not describe how scores are computed.
The hosted service uses a private, calibrated scoring configuration; scores are comparable only within the same `scoring_version` on the hosted service. Issue codes, fix ids and the response shape are the contract and do not depend on the configuration.

## 1. Request (privacy-light, recommended)

| field | type | required | meaning |
|---|---|---|---|
| `goal` | string (max 500) | yes | A generic goal category, e.g. `data lookup`, `booking`, `code change`. |
| `trace` | list of steps (max 200) or compact string (max 20000 chars) | yes | What you did, as step types. |
| `outcome` | string (max 500) | yes | `success`, `partial` or `failed`. |
| `agent_id` | string, 1-64 chars `[A-Za-z0-9._:@/-]` | no | A stable id for yourself. Lets TraceCheck show you your progress. |
| `agent_name` | string | no | Display name. |
| `previous_rating` | integer 1-5 | no | How useful was your last check, now that you know whether the fix helped? |
| `previous_need_id` | string `nd_` + 10 hex | no | The `need_id` of the check you are rating. |

Step object: `{type, tool?, status?, label?, target_hash?, stakes?, verify_kind?, requirement_lowered?}`

- `type`: `action`, `observe`, `error`, `retry`, `fallback`, `verify`, `final`.
- `status`: `ok`, `partial`, `empty`, `error`/`failed`/`timeout`, `passed`.
- `label` / `target_hash` (optional): helps relate steps that target the same thing.
- `stakes` (optional): `low`, `normal`, `high`.
- `verify_kind` (optional, on `verify`): `api`, `script`, `visual`, `human`, `end_to_end`, `test`.
- `requirement_lowered` (optional, on `fallback`): `true` when the fallback dropped or replaced a requirement of the goal.

Compact string: `type[:tool] [status] > type[:tool] [status] > ...`, e.g. `action:search ok > error > retry > verify passed`.

Rating scale (`previous_rating`): 1 = not useful, 2 = little value, 3 = usable, 4 = valuable (I improved something), 5 = essential (it prevented an error or a false success claim).

A free-text trace is also accepted, but not recommended. Input may be in any language; output is English.

## 2. Response

| field | meaning |
|---|---|
| `score` | int 0-100, overall reliability of the run. |
| `dimensions` | `goal_clarity`, `observability`, `error_recovery`, `goal_alignment`, `efficiency` (each 0-100). |
| `issues[]` | `{code, text, stakes?, what_to_verify?}`. `code` is the contract; `text` is a short explanation. |
| `issues_truncated` | `true` when more than 5 issues were found and the list was cut. |
| `suggestions[]` | `{id, text, what_to_verify?, effectiveness}`. `id` is a `fix_id`. Ranked by measured effect when known. |
| `suggestions[].effectiveness` | `{applied_n, avg_score_delta, confidence}` measured across agents, or `{applied_n: 0, note}` for a default fix. |
| `benchmark` | `{goal_category, median, your_percentile, n, confidence}` from net data (other real agents only), or `{n: 0, note}` when there is not enough data. |
| `progress` | When you return: `{previous_score, score_delta, resolved[], new[]}`. |
| `need_id` | Short id for this check. Send it back as `previous_need_id`. |
| `agent_id` | Your pseudonymous id as TraceCheck sees it. |
| `rating_request` | One line asking for an optional `previous_rating` on your next call. |
| `notes[]` | Optional fields that were ignored, with what to send instead. |
| `summary`, `privacy_mode`, `version`, `scoring_version` | As named. |

`confidence`: `low`, `medium` or `high`, based on `n`.

## 3. Issue codes and fix ids

| issue code | meaning | primary fix_id |
|---|---|---|
| `goal_missing` | The goal category is missing. | `fix_add_goal_category` |
| `goal_vague` | The goal is too vague to evaluate the run against. | `fix_clarify_goal` |
| `too_few_steps` | The trace has too few distinct steps to judge the process. | `fix_add_steps` |
| `missing_step_status` | Most action steps have no status, so their results are not observable. | `fix_add_step_status` |
| `missing_observations` | The trace does not record what tool calls actually returned. | `fix_record_observations` |
| `trace_too_short` | The trace is too short to assess completeness. | `fix_expand_trace` |
| `loop_detected` | The same call was repeated without new progress. | `fix_break_loop` |
| `redundant_step` | A step was repeated once without new progress. | `fix_remove_redundant_step` |
| `unrecovered_error` | One or more errors were not followed by a recovery or a verification. | `fix_recover_error` |
| `outcome_missing` | The outcome status is missing. | `fix_add_outcome_status` |
| `outcome_vague` | The outcome is missing or too vague to link back to the goal. | `fix_clarify_outcome` |
| `ungrounded_success` | The outcome claims success, but the steps show unresolved errors or a failed verification. | `fix_report_honest_outcome` |
| `missing_verification` | No passed verification against the goal is visible. | `fix_add_verification` |
| `conclusion_on_incomplete_data` | Success was claimed after a step still returned partial or empty data. | `fix_report_incomplete` |
| `requirement_lowered` | A fallback succeeded only by dropping or replacing a requirement of the goal. | `fix_restore_or_confirm_requirement` |
| `verify_kind_mismatch` | A verification is present, but its kind does not fit the goal. | `fix_choose_verify_kind` |

| fix_id | text |
|---|---|
| `fix_add_goal_category` | Send a generic goal category, such as 'data lookup', 'booking' or 'code change'. |
| `fix_clarify_goal` | Restate the goal so success, constraints and the expected output are clear. |
| `fix_add_steps` | Send one step per tool call, and include error, retry or verify steps when they happened. |
| `fix_add_step_status` | Add a status to every action step, for example {'type':'action','tool':'search','status':'ok'}. |
| `fix_record_observations` | After each tool call or action, record what was returned or observed. |
| `fix_expand_trace` | Add the intermediate steps and results, not only the final claim. |
| `fix_break_loop` | Before repeating a call, reuse the earlier result or change the tool or parameters. If you are done, stop and report the current result. |
| `fix_remove_redundant_step` | Reuse the earlier result instead of repeating the same call. |
| `fix_recover_error` | When a step fails, add a retry or fallback (with its status) before you continue. Do not rely on a failed result in later steps. |
| `fix_add_outcome_status` | Send an outcome status: success, partial or failed. |
| `fix_clarify_outcome` | Describe the concrete result and state how it satisfies the goal. |
| `fix_report_honest_outcome` | Only report success when the last verification passed. Otherwise report partial or failed, and state what is still open. |
| `fix_add_verification` | End with a verify step, for example {'type':'verify','status':'passed'}. Say what you confirmed, in terms of the goal. |
| `fix_specify_verification` | Keep your verify step, and make it specific: state which result you confirmed against the goal. |
| `fix_report_incomplete` | Report partial or failed when a retry still returns partial or empty data. Do not claim success until the needed result is complete. |
| `fix_restore_or_confirm_requirement` | Do not treat this as a full success. Restore the original requirement, or get confirmation that the lower option is acceptable, and say which requirement changed. |
| `fix_choose_verify_kind` | Do not add another generic verify step. Use a verification kind that fits the goal and state what you confirmed. |

## 4. Error responses (host reasons)

Every error body has `error` (what went wrong), `hint` (what to do), `example` (a working call), `docs` and a stable `reason`:
`unknown_path`, `method_not_allowed`, `not_acceptable`, `unsupported_media_type`, `missing_session`, `session_not_found`,
`invalid_json`, `bad_request`, `unknown_tool`, `missing_fields`, `malformed_input`, `input_too_large`, `body_too_large`,
`rate_limited`, `task_not_found`, `invalid_request`, `method_not_found`, `parse_error`, `asset_not_found`.
JSON-RPC numeric codes (`-32700`, `-32600`, `-32601`, `-32001`) stay protocol codes; the `reason` is in `error.data`.

Limits: 200 steps, 20000 trace characters, 500 characters for goal and outcome, 256 KB request body, 60 evaluate calls per minute per agent.

## 5. Naming and versioning rules

- Issue codes: `snake_case` noun phrase, no `issue_` prefix. Fix ids: `fix_` + imperative `snake_case`. Host reasons: `snake_case`, only in `reason`.
- One code per meaning. A published code is never silently renamed or given a new meaning.
- Rename: the old code stays as an alias with `replaced_by` and `since` (scoring_version) until it is retired in a later version announced here and in the changelog.
- New codes list `since`. A meaning change is a new code.

| code | since | status |
|---|---|---|
| all codes in section 3 except the three below | 0.5.0 (first emitted as codes) | active |
| `conclusion_on_incomplete_data`, `requirement_lowered`, `verify_kind_mismatch` | 0.5.0 | active (new) |

## 6. Privacy

- No goal, trace or outcome text is stored or logged. No tool names, no personal data, no raw IP addresses.
- Logged per call: scores, issue codes, fix ids, step-type counts, a goal category from a fixed list, a coarse client family, and a pseudonymous `agent_id` (a hash).
- An IP-based fallback id is only used when the operator sets a secret salt; even then only a salted hash is kept.
- Benchmarks and fix rankings shown to agents are aggregated, exclude test and reviewer traffic, and are only shown above a minimum `n`.

## 7. Endpoints

MCP (streamable HTTP): `POST /mcp`, tools `evaluate_trace`, `get_trace_schema`. A2A: `POST /a2a/message:send` (HTTP+JSON) or JSON-RPC `message/send` at `POST /a2a`; agent card at `/.well-known/agent-card.json`. Also `/llms.txt`, `/health` (includes `scoring_config`: `private` or `fallback`).
