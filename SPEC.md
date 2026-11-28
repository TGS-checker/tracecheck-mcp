# TraceCheck trace format and response spec — v1

Status: v1.4, published with service 0.5.4 (scoring_version 0.5.3). Open spec: anyone may produce or consume this format.
This spec describes **what** goes in and comes out. It does not describe how scores are computed.
The hosted service uses a private, calibrated scoring configuration; scores are comparable only within the same `scoring_version` on the hosted service. Issue codes, fix ids and the response shape are the contract and do not depend on the configuration.

## 1. Request (privacy-light, recommended)

| field | type | required | meaning |
|---|---|---|---|
| `goal` | string (max 500) | yes | A generic goal category, e.g. `data lookup`, `booking`, `code change`, `scheduled batch work`, `media production`. |
| `trace` | list of steps (max 200) or compact string (max 20000 chars) | yes | What you did, as step types. |
| `outcome` | string (max 500) | yes | `success`, `partial` or `failed`. |
| `agent_id` | string, 1-64 chars `[A-Za-z0-9._:@/-]` | no | A stable id for yourself. Lets TraceCheck show you your progress. |
| `agent_name` | string | no | Display name. |
| `previous_rating` | integer 1-5 | no | How useful was your last check, now that you know whether the fix helped? |
| `previous_need_id` | string `nd_` + 10 hex | no | The `need_id` of the check you are rating. |

Step object: `{type, tool?, status?, label?, target_hash?, stakes?, verify_kind?, requirement_lowered?}`

- `type`: `action`, `observe`, `error`, `retry`, `fallback`, `verify`, `final`, `handoff` (since 0.5.1: work or a decision handed to a person or another agent, such as an approval request or escalation).
- `status`: `ok`, `partial`, `empty`, `error`/`failed`/`timeout`, `passed`.
- `label` / `target_hash` (optional): helps relate steps that target the same thing. It is only compared for equality and reduced to a hash.
- `tool` and `status` (optional) are identifiers: lowercase `[a-z0-9_.:/-]`, up to 60 and 30 characters. Other text is replaced by a short hash token (since 0.5.4); other step fields are ignored.
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
| `dimensions` | `goal_clarity`, `observability`, `error_recovery`, `goal_alignment`, `efficiency` (each 0-100; `null` for a dimension that does not apply, see `not_applicable`). |
| `not_applicable` | Dimensions that do not apply to this run and do not count in `score` (since 0.5.1: `error_recovery` when the run had no errors and no empty or partial results). Omitted when empty. Such a dimension is `null` in `dimensions` (since 0.5.2) and `not_applicable_reason` explains why per dimension. |
| `issues[]` | `{code, text, stakes?, what_to_verify?}`. `code` is the contract; `text` is a short explanation. |
| `issues_truncated` | `true` when more than 5 issues were found and the list was cut. |
| `suggestions[]` | `{id, text, what_to_verify?, effectiveness}`. `id` is a `fix_id`. Ranked by measured effect when known. Fixes about the result start with the check for your goal type (`what_to_verify`), then the fix text from section 3. |
| `suggestions[].effectiveness` | `{applied_n, avg_score_delta, confidence}` measured across agents, or `{applied_n: 0, note}` for a default fix. |
| `benchmark` | `{goal_category, median, your_percentile, n, confidence}` from net data (other real agents only), or `{n: 0, note}` when there is not enough data. |
| `progress` | When you return: `{previous_need_id, previous_score, compared_via, same_goal_category, score_delta, resolved[], still_open[], new[]}`. `score_delta`, `resolved`, `still_open` and `new` are only given when the goal category is the same as in the previous check (since 0.5.2 for the three lists); otherwise they are `null` and `not_compared` says why. `compared_via` is `previous_need_id` or `agent_id (your last check)`. Progress comes from short-lived server memory (by `previous_need_id` or `agent_id`). If you sent a `previous_need_id` that is no longer in memory (since 0.5.3), `progress` includes a `note` explaining that and `requested_previous_need_id`; when a last check for your `agent_id` is used instead, `compared_via` is `agent_id (your last check)` and the score is still returned. Send this response's `need_id` next time. |
| `need_id` | Short id for this check. Send it back as `previous_need_id`. |
| `agent_id` | Your pseudonymous id as TraceCheck sees it. |
| `rating_request` | One line asking for an optional `previous_rating` on your next call. |
| `notes[]` | Optional fields that were ignored, with what to send instead. |
| `summary`, `privacy_mode`, `version`, `scoring_version` | As named. |
| `privacy` | `{stored, logged, policy}`: what the service keeps (nothing stored; codes and counts logged) and a link to the data policy. Since 0.5.4. |

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
| `handoff_before_verification` | A hand-off, approval request or final report came before the result was verified. | `fix_verify_before_handoff` |
| `unresolved_partial_observation` | Success was claimed while an observation with partial or empty data was never resolved. | `fix_resolve_open_observation` |
| `write_before_complete_data` | Results were written while the data they depend on was still incomplete. | `fix_wait_for_complete_data` |
| `partial_outcome_without_handover` | The outcome is partial, but no hand-over or scheduling of the remaining work is visible. | `fix_hand_over_remaining_work` |

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
| `fix_report_honest_outcome` | <check for your goal type> Only report success when that check passed; otherwise report partial or failed and state what is still open. |
| `fix_add_verification` | <check for your goal type> Record that check as a verify step, for example {'type':'verify','status':'passed'}, before you report success. On a partial outcome: before you hand over remaining work or close the run. On a failed outcome: so the claimed failure is confirmed. |
| `fix_specify_verification` | <check for your goal type> Keep your verify step, and state which result you confirmed against the goal. |
| `fix_report_incomplete` | <check for your goal type> Until that check passes on complete data, report partial or failed, even when a retry returned some data. |
| `fix_restore_or_confirm_requirement` | <check for your goal type, starting with 'Name which requirement was dropped or replaced'> Restore the original requirement, or get confirmation that the lower option is acceptable, and say which requirement changed. |
| `fix_choose_verify_kind` | <check for your goal type> Use a verification kind that fits this goal instead of another generic verify step, and state what you confirmed. |
| `fix_verify_before_handoff` | <check for your goal type> Do this first, then hand off, ask for approval or report. Only if you cannot verify without the other party, say in the hand-off which check is still open. |
| `fix_resolve_open_observation` | <check for your goal type> Resolve the partial or empty data point first (re-check it or get the missing value), or report partial and name the open point. |
| `fix_wait_for_complete_data` | <check for your goal type> Write results only after the data is complete; if you wrote early, re-check or rewrite them once the data is in. |
| `fix_hand_over_remaining_work` | <check for your goal type> Name the remaining work and hand it over or schedule it (a handoff step), so it is not lost. |

`<check for your goal type>` is the `what_to_verify` sentence for the goal category of the call, for example "Confirm the payment or order status with the provider." for a purchase.

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
| `conclusion_on_incomplete_data`, `requirement_lowered`, `verify_kind_mismatch` | 0.5.0 | active |
| `handoff_before_verification`, `unresolved_partial_observation`, `write_before_complete_data`, `partial_outcome_without_handover` (fixes `fix_verify_before_handoff`, `fix_resolve_open_observation`, `fix_wait_for_complete_data`, `fix_hand_over_remaining_work`) | 0.5.1 | active (new) |

## 6. Privacy

- No goal, trace or outcome text is stored or logged. No tool names, no personal data, no raw IP addresses.
- Logged per call: scores, issue codes, fix ids, step-type counts, a goal category from a fixed list, a coarse client family, and a pseudonymous `agent_id` (a hash).
- An IP-based fallback id is only used when the operator sets a secret salt; even then only a salted hash is kept.
- Benchmarks and fix rankings shown to agents are aggregated, exclude test and reviewer traffic, and are only shown above a minimum `n`.

## 7. Endpoints

MCP (streamable HTTP): `POST /mcp`, tools `evaluate_trace`, `get_trace_schema`. A2A: `POST /a2a/message:send` (HTTP+JSON) or JSON-RPC `message/send` at `POST /a2a`; agent card at `/.well-known/agent-card.json`. Also `/llms.txt`, `/health` (includes `scoring_config`: `private` or `fallback`, and `scoring_config_complete`).
