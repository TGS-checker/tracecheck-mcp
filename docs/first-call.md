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
# pip install mcp
import asyncio, json
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    async with streamablehttp_client("https://tracecheck-mcp.onrender.com/mcp") as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            out = await s.call_tool("evaluate_trace", {
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

## What comes back

`score` (0-100), `issues` (with `code`), `suggestions` (with `fix_id`), `summary`, `need_id`, `benchmark`, `scoring_version`, and a `privacy` line. Apply the suggestions and call again with `previous_need_id` to see your progress. Not sure about the format? Call `get_trace_schema` first.

Status: the curl, Python and A2A examples were run against the live endpoint on 2026-11-28.
