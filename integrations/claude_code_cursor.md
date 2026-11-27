# Claude Code / Claude Desktop / Cursor

1. Add the server (no API key):

```bash
claude mcp add --transport http tracecheck https://tracecheck-mcp.onrender.com/mcp
```

Cursor (`~/.cursor/mcp.json`):

```json
{ "mcpServers": { "tracecheck": { "url": "https://tracecheck-mcp.onrender.com/mcp" } } }
```

2. Add to `CLAUDE.md` or a Cursor rule (use your own stable id):

```text
After each task, send your trace (step types only) with your agent_id to TraceCheck's evaluate_trace and apply the suggested fixes before reporting success.
Use agent_id "my-coding-agent". On the next call, add previous_rating (1-5) and the previous need_id.
```

More detail: [docs/integrations/claude.md](../docs/integrations/claude.md).
