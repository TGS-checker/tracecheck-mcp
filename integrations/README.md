# Integrations (copy-paste)

Each example passes a stable `agent_id`, so TraceCheck can show the agent its progress between runs.

| framework | file | longer guide |
|---|---|---|
| any Python agent (stdlib only) | [tracecheck_client.py](tracecheck_client.py) | [SPEC.md](../SPEC.md) |
| LangGraph / LangChain | [langgraph_selfcheck.py](langgraph_selfcheck.py) | [docs/integrations/langchain.md](../docs/integrations/langchain.md) |
| CrewAI | [crewai_selfcheck.py](crewai_selfcheck.py) | [docs/integrations/crewai.md](../docs/integrations/crewai.md) |
| OpenAI Agents SDK | [openai_agents_selfcheck.py](openai_agents_selfcheck.py) | [docs/integrations/openai-agents.md](../docs/integrations/openai-agents.md) |
| Claude Code / Desktop / Cursor | [claude_code_cursor.md](claude_code_cursor.md) | [docs/integrations/claude.md](../docs/integrations/claude.md) |

The framework files only wrap `tracecheck_client.evaluate`; the client never raises, so a self-check cannot break your run.
