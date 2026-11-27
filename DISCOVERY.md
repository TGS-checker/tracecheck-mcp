# DISCOVERY: the smallest path to the first real external agent calls

Researched 22 Nov 2026. These platforms change often, so re-check before acting.

## What changed since the original plan
- **Smithery can no longer be used for publishing.** On 31 Oct 2026 Smithery shut down its proxy, hosted server URLs, accounts, API keys and its CLI/SDKs. smithery.ai is now a curated, read-only directory (powered by Arcade) with no submission flow (smithery.ai/future; mcpfinder.dev re-checked this on 13 Nov 2026). That's why there is **no `smithery.yaml`** here.
- **mcp Python SDK 2.x**: `FastMCP` was renamed to `MCPServer` (`from mcp.server import MCPServer`). The original import is correct for 2.x, but the original `mcp[cli]>=1.0.0` pin let 1.x install, and 1.x would crash on that import. Pinned to `mcp>=2.3,<3`.
- **A2A v1.0**: the card lives at `/.well-known/agent-card.json` (`/agent.json` was v0.2 and earlier). `supportedInterfaces[]` replaces `url`/`preferredTransport`, and each entry must be a real A2A endpoint. The original card pointed `HTTP+JSON` at `/mcp`, which is not an A2A endpoint. There is now a minimal A2A `POST /a2a/message:send`, and the card points to `/a2a`.

## Options compared
| Option | Cost | Who it reaches | Effort / user steps | Verdict |
|---|---|---|---|---|
| Local stdio (`pip`/`uvx` from PyPI) | free | only agents whose humans install it | PyPI account + publish | Later. Not discoverable on its own |
| **Render free web service (Docker)** | **free**, no card needed | any agent with a URL | GitHub + Render account | **Host for the first path.** 750 h/month, sleeps after 15 min idle (about 1 min cold start), filesystem is ephemeral |
| Hugging Face Spaces (Docker) | Docker Spaces now need PRO (about $9/mo) | | | No longer free for Docker |
| Fly.io | no free tier for new accounts, card required | | | No |
| Railway | $5 trial, then $1/mo credit | | | Fine as a backup if Render is a problem |
| Cloudflare Workers (Python) | free | | would need a rewrite (no uvicorn/MCP SDK as is) | Not the smallest step |
| Smithery | – | – | shut down | Not possible |
| **Official MCP Registry** (registry.modelcontextprotocol.io) | free | upstream feed for MCPfinder, PulseMCP (when it reopens) and client marketplaces | GitHub login via `mcp-publisher`. Remote-only entries need **no** PyPI package | **Do it.** Preview status (data resets possible) |
| **Glama** (glama.ai/mcp/servers) | free | largest index (about 125k servers), also feeds MCPfinder | "Add Server" with a public GitHub repo. Builds the Dockerfile in a sandbox, then "Make Release" | **Do it, same day.** Hosted endpoints can also be added as a "connector" |
| mcp.so | free (review) or $39 | humans browsing | form with repo URL | Optional, later |
| PulseMCP | – | – | submissions paused, will import from the Official Registry | Comes for free via the Registry |
| A2A directories | free | small | – | The card on the live URL is enough for now |

## Recommendation: ONE first path
**Public GitHub repo → Render free → Official MCP Registry (remote) + Glama.** Cost €0. No PyPI and no credit card.

1. **[user] Create a GitHub repo** `tracecheck-mcp` (public, MIT) and push the contents of this folder except `.venv/`, `original/` and `calls.jsonl` (`.gitignore` covers them). First replace `GITHUB_USERNAME` in `server.json`, `glama.json` and `README.md`.
2. **[user] Render**: sign up with GitHub → New → Blueprint → pick the repo (`render.yaml` sets up a free Docker web service with `/health`). Note the URL (e.g. `https://tracecheck-xxxx.onrender.com`) and set `PUBLIC_URL` to it.
3. **Smoke test** (I can do this once the URL exists):
   `curl $URL/health`, `curl $URL/.well-known/agent-card.json`, then an MCP `initialize` + `tools/list` against `$URL/mcp`.
4. Update the `remotes[0].url` in `server.json` to `$URL/mcp`, then commit.
5. **[user, publishes] Official MCP Registry**:
   ```bash
   curl -L "https://github.com/modelcontextprotocol/registry/releases/latest/download/mcp-publisher_$(uname -s | tr '[:upper:]' '[:lower:]')_$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/').tar.gz" | tar xz mcp-publisher
   ./mcp-publisher login github     # device flow in the browser
   ./mcp-publisher publish          # reads ./server.json
   ```
   With GitHub auth the name has to start with `io.github.<username>/`. The description limit is 100 characters (the current one is 88).
6. **[user, publishes] Glama**: glama.ai/mcp/servers → "Add Server" → repo URL, then claim it (`glama.json` names you as maintainer). In Admin → Dockerfile, click Deploy → Make Release. Glama only lists tools after a release.
7. **Measure**: Render's free filesystem is wiped on sleep or redeploy, so `calls.jsonl` there is lost. On Render, look in the Logs tab for the lines `Evaluated trace from <agent>: score=<n>` (stdout). Directory crawlers (Glama checks, registry validators) will also call it, so count by `agent_name` and user-agent.

Expected time: about 1 hour of user clicks. First calls come from Glama's own health and inspection checks right away. Real third-party calls depend on discovery via MCPfinder and the registry, which takes days to weeks.

## What the user must do personally
GitHub account and repo (public), Render account, `mcp-publisher login github` + `publish`, Glama sign-in, Add Server and Make Release. Choose the license (MIT is a placeholder).

## Later (not needed for the first calls)
- PyPI `tracecheck-mcp` (the name is free as of today) so `uvx tracecheck-mcp` works. Add `packages[]` to `server.json` (the `mcp-name:` token is already in the README).
- Persistent logging (free Postgres on Render expires after 30 days; a cheap option is an external log sink).
- Paid always-on hosting (Render Starter, $7/mo): live since 2026-11-27, no cold starts; the keepalive workflow was removed.
