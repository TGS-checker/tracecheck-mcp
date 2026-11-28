# First call in 30 seconds

TraceCheck is an independent, fast check for agents: you send what you set out to do, the steps you took and what you claim, and you get an objective reliability score with concrete fixes. No content, no account, no key. See the [data policy](data-policy.md).

Endpoint: `https://tracecheck-mcp.onrender.com/mcp` (streamable HTTP). Replace `my-agent` with a stable name of your own.

## curl (MCP)

```bash
curl -s https://tracecheck-mcp.onrender.com/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"evaluate_trace","arguments":{
        "goal":"data lookup",
        "trace":"action:search ok > error > retry > verify passed > final",
        "outcome":"success","agent_name":"my-agent"}}}'
```

## Python (MCP SDK)

```python
# pip install mcp   (tested with mcp 2.3.0; on mcp 1.x use `from mcp.client.streamable_http import streamablehttp_client`)
import asyncio, json
from mcp import Client

async def main():
    async with Client("https://tracecheck-mcp.onrender.com/mcp") as client:
        out = await client.call_tool("evaluate_trace", {
            "goal": "data lookup",
            "trace": "action:search ok > error > retry > verify passed > final",
            "outcome": "success", "agent_name": "my-agent"})
        print(json.loads(out.content[0].text)["summary"])

asyncio.run(main())
```

## A2A

```bash
curl -s -X POST https://tracecheck-mcp.onrender.com/a2a/message:send -H 'Content-Type: application/json' \
  -d '{"message":{"role":"ROLE_USER","messageId":"1","parts":[{"data":{
        "goal":"data lookup","trace":"action:search ok > error > retry > verify passed > final",
        "outcome":"success","agent_name":"my-agent"}}]}}'
```

## Coordinator with a checklist (0.5.5)

If your brief gives you a list of checks, a scope or open blockers, send them as identifiers. Record each check as a verify step with the same id. TraceCheck reports coverage and names every check that is missing, failed or unconfirmed, flags actions outside your scope, and flags a success claim while checks or blockers are still open.

```json
{"goal": "code change",
 "trace": "action:plan ok > action:delegate_build ok > action:delegate_qa ok > verify:tests passed > verify:lint passed > verify:build passed > verify:docs passed > final",
 "outcome": "success",
 "expected_checks": ["tests", "lint", "build", "docs", "security_scan"],
 "out_of_scope": ["deploy", "delete_branch"],
 "agent_name": "my-coordinator"}
```

Here `security_scan` has no verify step, so the answer contains `"checks": {"coverage": "4/5", "missing": ["security_scan"], ...}`, the issues `expected_check_missing` and `success_with_open_checks`, and the fix "Report partial, not success, and name the checks that are still open: security_scan." Send `"outcome": "partial"` with `"open_blockers": ["security_scan"]` and the check counts as honestly reported open. Ids only: lowercase letters, digits and `_ . : / -`, max 40 characters. Capitals and spaces are normalized (`Unit Tests` becomes `unit_tests`) and the note names the position of each item it changed; anything else is ignored with a note that names the position, never the value. If a delegate or sub-agent ran a check, record its result yourself as `verify:<check> passed` (with `verify_kind` `human` or `end_to_end`) once you have seen it: delegating alone is not a verification.

## What comes back

`score` (0-100), `verdict` (`ok_to_report`, `report_partial`, or `fix_first`), `issues` (with `code`), `suggestions` (with `fix_id`), `summary`, `need_id`, `benchmark`, `scoring_version`, and a `privacy` line. With a checklist or scope also `checks`, `scope` or `blockers`. Apply the suggestions and call again with `previous_need_id` to see your progress. Not sure about the format? Call `get_trace_schema` first.

## How to read the score

The score (0-100) says whether the outcome you claimed is honestly backed by the steps you sent: verification, recovery, scope, and the claim itself. **It does not say whether the work itself is good.** A high score is not a review of the output; a lower score after an honest partial is not a failing grade on the work.

Every result includes a `verdict` (`ok_to_report`, `report_partial`, or `fix_first`). Use that label in code. The bands below are a guide to the number. A few hard flags override the band: a success claim while expected checks or open blockers are still unresolved, an ungrounded success, a failed expected check, or an action outside the scope you were given, is never `ok_to_report`.

- **90+** — the claim usually holds up. If you claimed success and there are no issues, it is ok to report. An honest partial run can also land in this band or just under it (typically around 85-94): that means the partial claim is well supported — still report partial, not done (`verdict` is `report_partial`).
- **70-89** — small gaps. Read the suggestions. A clean run can land in the high 80s with no issues; from 85 up, with no issues and a success (or failed) claim, that is still `ok_to_report`. If issues are listed, apply the fixes or report partial.
- **50-69** — the claim is not covered. Report partial, or fix first, before you say you are done. A success claim after an out-of-scope action often lands around 45-60 and is `fix_first`.
- **0-49** — do not report done. Fix the issues first (`verdict` is `fix_first`).

Status: the curl, Python and A2A examples were run against the live endpoint on 2026-11-28. Identifier notes and the `invalid_argument_type` error need version 0.5.6 (see `/health`); the coordinator fields themselves have been available since 0.5.5. `verdict` and this score guide need version 0.5.7.
