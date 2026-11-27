# TraceCheck with the OpenAI Agents SDK

**New in 0.5.0:** pass a stable `agent_id` so TraceCheck can show your progress; copy-paste code in [integrations/](../../integrations/).

**Why:** your agent self-checks its run (loops, unrecovered errors, unverified success) before its final answer. Free, no API key, no content needed.

Endpoint: `https://tracecheck-mcp.onrender.com/mcp` (streamable HTTP, no auth). The host is always on (no cold start).

Source for both options: [OpenAI Agents SDK, MCP](https://openai.github.io/openai-agents-python/mcp/) (checked 2026-11-23).

## Option A: `HostedMCPTool` (OpenAI's Responses API calls TraceCheck for you)

```python
# pip install openai-agents
from agents import Agent, HostedMCPTool, Runner

agent = Agent(
    name="Assistant",
    instructions=INSTRUCTIONS,  # include the self-check instruction below
    tools=[HostedMCPTool(tool_config={
        "type": "mcp",
        "server_label": "tracecheck",
        "server_url": "https://tracecheck-mcp.onrender.com/mcp",
        "require_approval": "never",          # read-only tool: safe without approval
        "allowed_tools": ["evaluate_trace"],
    })],
)
result = await Runner.run(agent, task)
```

## Option B: `MCPServerStreamableHttp` (your process calls TraceCheck)

```python
from agents import Agent, Runner
from agents.mcp import MCPServerStreamableHttp

async with MCPServerStreamableHttp(
    name="tracecheck",
    params={"url": "https://tracecheck-mcp.onrender.com/mcp", "timeout": 30},
    cache_tools_list=True,
    max_retry_attempts=2,
) as tracecheck:
    agent = Agent(name="Assistant", instructions=INSTRUCTIONS, mcp_servers=[tracecheck])
    result = await Runner.run(agent, task)
```

## Pattern: call `evaluate_trace` before reporting success

Add this to `INSTRUCTIONS`:

```text
Before you report success, call the TraceCheck tool `evaluate_trace` once.
Send no content, only step types:
- goal: a generic category, e.g. "data lookup", "booking", "code change"
- trace: your steps as "type[:tool] status" joined by " > ", e.g.
  "action:search ok > action:book error > retry:book ok > verify passed > final"
  (types: action, observe, error, retry, fallback, verify, final)
- outcome: "success", "partial" or "failed"
- agent_name: "<your-agent-name>"
If the score is below 70 or issues are listed, apply the suggestions (retry, verify,
or report "partial") before you answer the user.
```

Tip: set one stable `agent_name` per agent (e.g. `"support-agent"`) so you can compare scores between runs.

Status: **verified against the docs linked above** (openai-agents itself was not installed for this check). The underlying MCP calls were tested live; see [self-check.md](../self-check.md).
