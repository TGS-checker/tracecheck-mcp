# TraceCheck with CrewAI

**New in 0.5.0:** pass a stable `agent_id` so TraceCheck can show your progress; copy-paste code in [integrations/](../../integrations/).

**Why:** let a crew check its own run (loops, unrecovered errors, unverified success) before it hands over the result. Free, no API key, no content needed.

Endpoint: `https://tracecheck-mcp.onrender.com/mcp` (streamable HTTP, no auth). The free host sleeps when idle; the first call can take up to ~60 s (CrewAI's MCP connection timeout is 10 s, so warm it up with a GET on `/health` or retry once).

## Option A: `mcps=[...]` on the agent (recommended by CrewAI)

Source: [CrewAI MCP DSL Integration](https://docs.crewai.com/edge/en/mcp/dsl-integration) (checked 2026-11-23).

```python
from crewai import Agent

agent = Agent(
    role="Research Assistant",
    goal="Answer the user's question with verified sources",
    backstory=BACKSTORY,  # include the self-check instruction below
    mcps=["https://tracecheck-mcp.onrender.com/mcp#evaluate_trace"],  # '#tool' loads only that tool
)
```

## Option B: `MCPServerAdapter` (manual connection management)

Source: [CrewAI Streamable HTTP transport](https://docs.crewai.com/edge/en/mcp/streamable-http) (checked 2026-11-23).

```python
# pip install "crewai-tools[mcp]"
from crewai import Agent
from crewai_tools import MCPServerAdapter

server_params = {"url": "https://tracecheck-mcp.onrender.com/mcp", "transport": "streamable-http"}

with MCPServerAdapter(server_params) as tools:
    agent = Agent(role="...", goal="...", backstory=BACKSTORY, tools=tools)
```

## Pattern: call `evaluate_trace` before reporting success

Put this in the agent's backstory or the task description:

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

Tip: use one stable `agent_name` per crew role (e.g. `"crew-researcher"`) so you can compare scores between runs.

Status: **verified against the docs linked above** (CrewAI itself was not installed for this check). The underlying MCP calls were tested live; see [self-check.md](../self-check.md).
