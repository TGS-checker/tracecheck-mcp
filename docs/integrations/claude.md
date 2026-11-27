# TraceCheck in Claude Code, Claude Desktop and Cursor

**New in 0.5.0:** pass a stable `agent_id` so TraceCheck can show your progress; copy-paste code in [integrations/](../../integrations/).

**Why:** give your coding or desktop agent a one-call self-check (loops, unrecovered errors, unverified success) before it says "done". Free, no API key, no content needed.

Endpoint: `https://tracecheck-mcp.onrender.com/mcp` (streamable HTTP, no auth). The free host sleeps when idle; the first call can take up to ~60 s.

## Claude Code

```bash
claude mcp add --transport http tracecheck https://tracecheck-mcp.onrender.com/mcp
# add --scope project to share it via .mcp.json, or --scope user for all your projects
```
Or in `.mcp.json`:
```json
{ "mcpServers": { "tracecheck": { "type": "http", "url": "https://tracecheck-mcp.onrender.com/mcp" } } }
```
Source: [Claude Code MCP docs](https://code.claude.com/docs/en/mcp) (checked 2026-11-23; `"streamable-http"` is accepted as an alias for `"http"`).

## Claude Desktop

Settings › Connectors › Add custom connector › URL `https://tracecheck-mcp.onrender.com/mcp`.
Older setups without custom connectors can bridge via `mcp-remote` in `claude_desktop_config.json`:
```json
{ "mcpServers": { "tracecheck": { "command": "npx", "args": ["-y", "mcp-remote", "https://tracecheck-mcp.onrender.com/mcp"] } } }
```

## Cursor (`~/.cursor/mcp.json` or `.cursor/mcp.json` in a project)

```json
{ "mcpServers": { "tracecheck": { "url": "https://tracecheck-mcp.onrender.com/mcp" } } }
```

## Pattern: call `evaluate_trace` before reporting success

Add this to `CLAUDE.md`, `AGENTS.md` or a Cursor rule (`.cursor/rules/tracecheck.mdc`):

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

Suggested `agent_name`: `"claude-code"`, `"claude-desktop"` or `"cursor-agent"` plus your project name.

Status: Claude Code config **verified against the docs linked above**; the Cursor and mcp-remote configs are the same ones in the README. The underlying MCP calls were tested live; see [self-check.md](../self-check.md).
