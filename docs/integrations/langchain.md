# TraceCheck with LangChain / LangGraph

**New in 0.5.0:** pass a stable `agent_id` so TraceCheck can show your progress; copy-paste code in [integrations/](../../integrations/).

**Why:** your agent gets a 0-100 self-check (loops, unrecovered errors, unverified success) before it tells the user "done". Free, no API key, no content needed.

Endpoint: `https://tracecheck-mcp.onrender.com/mcp` (streamable HTTP, no auth). The free host sleeps when idle; the first call can take up to ~60 s.

## Option A: built-in `langchain.mcp` (current, beta, `langchain[mcp]>=1.4.0`)

LangChain now ships MCP support in `langchain.mcp`; `MCPAdapter` replaces `MultiServerMCPClient` and infers the transport from the URL.
Source: [Migrate from langchain-mcp-adapters](https://docs.langchain.com/oss/python/migrate/langchain-mcp-adapters) (checked 2026-11-23).

```python
# pip install "langchain[mcp]"
from langchain.mcp import MCPAdapter
from langchain.agents import create_agent

config = {"mcpServers": {"tracecheck": {"url": "https://tracecheck-mcp.onrender.com/mcp"}}}

async with MCPAdapter(config) as adapter:
    tools = await adapter.list_tools()          # evaluate_trace + get_trace_schema (names may get a "tracecheck_" prefix)
    agent = create_agent("openai:gpt-5", tools=tools + my_tools, system_prompt=SYSTEM_PROMPT)
    result = await agent.ainvoke({"messages": [{"role": "user", "content": task}]})
```

## Option B: `langchain-mcp-adapters` (`MultiServerMCPClient`, still common in existing projects)

```python
# pip install langchain-mcp-adapters
from langchain_mcp_adapters.client import MultiServerMCPClient

client = MultiServerMCPClient({
    "tracecheck": {"transport": "streamable_http", "url": "https://tracecheck-mcp.onrender.com/mcp"},
})
tools = await client.get_tools()
```
Source: [langchain-mcp-adapters README](https://github.com/langchain-ai/langchain-mcp-adapters) (newer releases also accept `"transport": "http"`).

## Pattern: call `evaluate_trace` before reporting success

Add this to your system prompt (`SYSTEM_PROMPT` above):

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

Want it deterministic instead of prompt-driven? Call the tool yourself after the run (no LLM involved):

```python
check = next(t for t in tools if t.name.endswith("evaluate_trace"))
report = await check.ainvoke({
    "goal": "data lookup",
    "trace": "action:search ok > action:fetch error > retry:fetch ok > verify passed > final",
    "outcome": "success",
    "agent_name": "my-langgraph-agent",   # shows up in our anonymous usage count; pick a stable name
})
```

Status: **verified against the docs linked above** (LangChain itself was not installed for this check). The underlying MCP calls were tested live; see [self-check.md](../self-check.md).
