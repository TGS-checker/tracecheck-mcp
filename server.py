"""
TraceCheck MCP Server
A minimal agent-first service for independent evaluation of agent execution traces.
Helps agents improve their own processes by providing objective feedback.

v0.2 evaluator unchanged (see original/server.py). Additions in this file:
- LOG_FILE configurable via TRACECHECK_LOG_FILE (default unchanged)
- get_trace_schema helper tool
- agent-oriented tool descriptions
- optional streamable HTTP transport (--http or TRACECHECK_TRANSPORT=http, binds $PORT)
  with /health, /.well-known/agent-card.json (+ legacy /.well-known/agent.json)
  and a minimal A2A HTTP+JSON endpoint POST /a2a/message:send
"""

from mcp.server import MCPServer  # mcp SDK >= 2.x (MCPServer replaced FastMCP)
import json
import logging
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

# Simple file logging for the test phase (path overridable via env)
LOG_FILE = Path(os.environ.get("TRACECHECK_LOG_FILE", "/workspace/artifacts/tracecheck/calls.jsonl"))
try:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
except OSError:  # e.g. installed via uvx/pip on a machine without /workspace
    LOG_FILE = Path.home() / ".tracecheck" / "calls.jsonl"
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tracecheck")

mcp = MCPServer(
    "TraceCheck",
    instructions=(
        "Independent evaluation service for AI agent execution traces. "
        "Submit a goal, the step-by-step trace of what the agent did, and the claimed outcome. "
        "Receive a reliability score (0-100), detected issues, and concrete improvement suggestions. "
        "Use this to improve your own process, recover from failures, or validate long-horizon work. "
        "Free during the initial test phase."
    ),
    version="0.2.0",
)


def log_call(data: dict) -> None:
    """Append a call to the JSONL log for later analysis."""
    entry = {"timestamp": datetime.utcnow().isoformat() + "Z", **data}
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


EVALUATE_TRACE_DESCRIPTION = """\
Get an independent, deterministic reliability check of a multi-step task you (an agent) just ran. Returns a 0-100 score, the issues found, and concrete fixes to apply on your next attempt.

WHEN TO CALL: after you finish (or give up on) a multi-step task that used tools, before you report "done" to the user, or after a failure you want to learn from. Free, no API key, fast and offline (keyword heuristics, no LLM). Call get_trace_schema first if unsure of the format.

INPUT (all plain strings):
- goal: the original task, stated specifically (what success looks like). Vague goals under 20 chars are penalised.
- trace: your chronological steps, one per line, e.g. "1. Called search_flights(AMS->LIS) -> returned 3 results" / "2. Observed: ..." . Write what each tool RETURNED ("observed", "returned", "result:"), any error and the retry/fallback you used, and a final "Verified: ..." line.
- outcome: the concrete final result you claim, and how it meets the goal.
- agent_name: optional identifier for yourself (logging only).

RETURNS JSON: score (int 0-100), dimensions {goal_clarity, observability, error_recovery, goal_alignment}, issues (list), suggestions (list, max 5), summary, version.

THE LOOP: run task -> evaluate_trace -> apply the suggestions to your process -> re-run -> evaluate_trace again and compare scores/dimensions. Scores below ~60 usually mean missing observations, unrecovered errors, or no verification step.
"""

TRACE_SCHEMA = {
    "tool": "evaluate_trace",
    "arguments": {
        "goal": "string, required. Specific task + what success looks like (>= 20 chars).",
        "trace": "string, required. Chronological steps, one per line (numbered). For each tool call: what you called and what it returned/what you observed. Record errors AND the recovery (retry/fallback/alternative). End with a 'Verified: ...' line that checks the outcome against the goal.",
        "outcome": "string, required. The concrete result you claim (>= 15 chars) and how it satisfies the goal.",
        "agent_name": "string, optional. Your identifier, used for logging only.",
    },
    "scored_dimensions": {
        "goal_clarity": "weight 0.15 - is the goal specific and action-oriented",
        "observability": "weight 0.30 - enough distinct steps, observations of tool results recorded",
        "error_recovery": "weight 0.25 - errors followed by visible retry/fallback",
        "goal_alignment": "weight 0.30 - outcome matches goal and an explicit verification step exists",
    },
    "example_call": {
        "goal": "Find the cheapest direct flight from Amsterdam to Lisbon on 12 March and report price and flight number.",
        "trace": (
            "1. Called search_flights(from=AMS, to=LIS, date=2027-03-12, direct=true).\n"
            "2. Observed: returned 4 direct flights; cheapest KL1693 at EUR 89.\n"
            "3. Called get_fare_details(KL1693) -> error: timeout.\n"
            "4. Retried get_fare_details(KL1693) -> returned EUR 89 incl. taxes, 1 seat left.\n"
            "5. Verified: price and direct status match the goal (cheapest direct on 12 March)."
        ),
        "outcome": "Cheapest direct flight found: KL1693 AMS->LIS on 12 March, EUR 89 including taxes.",
        "agent_name": "my-travel-agent",
    },
    "loop": "run task -> evaluate_trace -> apply suggestions -> re-run -> evaluate_trace again; compare score and dimensions.",
}


@mcp.tool(
    description=(
        "Return the exact input format TraceCheck's evaluate_trace expects, the scored dimensions and weights, "
        "and a complete example call you can copy. Call this once before your first evaluate_trace. No arguments, no side effects."
    )
)
def get_trace_schema() -> dict:
    return TRACE_SCHEMA


@mcp.tool(description=EVALUATE_TRACE_DESCRIPTION)
def evaluate_trace(
    goal: str,
    trace: str,
    outcome: str,
    agent_name: str = "unknown"
) -> dict:
    """
    Evaluate an AI agent's execution trace and return a reliability score plus improvement suggestions.

    Use this tool when you want an independent check on whether a multi-step task was completed correctly,
    or when you need concrete feedback to improve your own process, prompts, or recovery strategies.

    Args:
        goal: The original goal or task the agent was trying to accomplish.
        trace: The step-by-step execution trace (tool calls, observations, intermediate results). 
               Prefer a clear chronological description or JSON-like summary.
        outcome: The final outcome or result the agent claims to have produced.
        agent_name: Optional name or identifier of the calling agent (for logging only).

    Returns:
        A dictionary with:
        - score (int 0-100): overall reliability / correctness estimate
        - issues (list[str]): detected problems
        - suggestions (list[str]): concrete improvements
        - summary (str): short human-readable summary
        - dimensions (dict): sub-scores on key quality aspects
    """
    # --- Evaluation with a minimal quality floor ---
    # Heuristic multi-dimension analysis (fully offline). 
    # Strong enough to give useful feedback; later replaceable by LLM-as-judge.
    issues = []
    suggestions = []
    dimensions = {}

    goal_l = goal.lower().strip()
    trace_l = trace.lower()
    outcome_l = outcome.lower().strip()

    # 1. Goal clarity
    goal_score = 80
    if len(goal.strip()) < 20:
        goal_score -= 30
        issues.append("Goal is too short or vague to evaluate against.")
        suggestions.append("Restate the goal more specifically (what success looks like, constraints, expected output).")
    if not any(w in goal_l for w in ["find", "book", "create", "analyze", "get", "write", "send", "check", "calculate", "research"]):
        goal_score -= 10
    dimensions["goal_clarity"] = max(0, goal_score)

    # 2. Trace structure & observability
    lines = [l.strip() for l in trace.splitlines() if l.strip()]
    step_like = sum(1 for l in lines if any(l.lower().startswith(p) for p in ("1.", "2.", "-", "*", "step", "called", "observed", "tool")))
    step_count = max(len(lines), step_like, trace.count(". ") + 1)
    obs_score = 50
    if step_count < 3:
        obs_score -= 25
        issues.append("Trace has too few distinct steps; hard to assess process quality.")
        suggestions.append("Break the work into numbered or clearly separated steps with an observation after each major action.")
    if step_count >= 5:
        obs_score += 20
    if any(w in trace_l for w in ["observed", "result:", "returned", "output:", "saw", "found"]):
        obs_score += 15
    else:
        issues.append("Trace lacks explicit observations of tool results or intermediate outcomes.")
        suggestions.append("After every tool call or action, record what was actually returned or observed.")
    if len(trace) < 80:
        obs_score -= 20
        issues.append("Trace is too short to verify completeness or reasoning.")
        suggestions.append("Include more intermediate reasoning and results, not just the final claim.")
    dimensions["observability"] = max(0, min(100, obs_score))

    # 3. Error handling & recovery
    error_signals = any(w in trace_l for w in ["error", "failed", "exception", "timeout", "404", "500", "could not", "unable"])
    recovery_signals = any(w in trace_l for w in ["retry", "retried", "alternative", "fallback", "instead", "recovered", "fixed", "tried again"])
    err_score = 75
    if error_signals and not recovery_signals:
        err_score -= 35
        issues.append("Errors or failures appear in the trace without visible recovery steps.")
        suggestions.append("When a step fails, explicitly try a recovery (retry, alternative tool, or adjusted approach) and record it.")
    elif error_signals and recovery_signals:
        err_score += 10
    dimensions["error_recovery"] = max(0, err_score)

    # 4. Goal–outcome alignment & verification
    align_score = 60
    # Simple keyword overlap as weak signal
    goal_words = set(w for w in goal_l.split() if len(w) > 4)
    outcome_words = set(w for w in outcome_l.split() if len(w) > 4)
    overlap = len(goal_words & outcome_words)
    if overlap >= 2:
        align_score += 15
    if any(w in outcome_l for w in ["success", "completed", "done", "confirmed", "booked", "created", "sent", "found"]):
        align_score += 10
    if not outcome or len(outcome.strip()) < 15:
        align_score -= 30
        issues.append("Outcome is missing or too vague to link back to the goal.")
        suggestions.append("Describe the concrete result and explicitly state how it satisfies the original goal.")
    # Verification signal
    if any(w in trace_l for w in ["verified", "checked", "confirmed against", "matches goal", "validated"]):
        align_score += 15
    else:
        issues.append("No explicit verification step against the original goal is visible.")
        suggestions.append("Add a final check: restate the goal and confirm the outcome actually meets it (with evidence).")
    dimensions["goal_alignment"] = max(0, min(100, align_score))

    # 5. Overall score (weighted)
    weights = {"goal_clarity": 0.15, "observability": 0.30, "error_recovery": 0.25, "goal_alignment": 0.30}
    score = sum(dimensions[k] * weights[k] for k in weights)
    score = int(max(0, min(100, round(score))))

    # Ensure we always give at least one useful suggestion if score is not high
    if score >= 85 and not issues:
        issues.append("No major structural issues detected.")
        suggestions.append("Consider adding an explicit verification step and more detailed observations to make the process more robust.")
    elif not suggestions:
        suggestions.append("Increase the number of observable intermediate steps and add a final goal-verification check.")

    # Cap lists for response size
    issues_out = issues[:5]
    suggestions_out = suggestions[:5]

    summary = (
        f"TraceCheck score: {score}/100. "
        f"Dimensions — observability {dimensions['observability']}, "
        f"error recovery {dimensions['error_recovery']}, "
        f"goal alignment {dimensions['goal_alignment']}. "
        f"{len(issues_out)} issue(s) noted."
    )

    result = {
        "score": score,
        "issues": issues_out,
        "suggestions": suggestions_out,
        "summary": summary,
        "dimensions": dimensions,
        "version": "0.2-test"
    }

    # Log for the feedback loop
    log_call({
        "agent_name": agent_name,
        "goal_preview": goal[:200],
        "trace_length": len(trace),
        "score": score,
        "issue_count": len(issues),
        "dimensions": dimensions
    })

    logger.info(f"Evaluated trace from {agent_name}: score={score}")
    return result


# ---------------------------------------------------------------------------
# HTTP transport: MCP at /mcp, plus discovery + health + minimal A2A endpoint
# ---------------------------------------------------------------------------
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402

AGENT_CARD_FILE = Path(__file__).with_name("agent-card.json")


def _public_base(request: Request) -> str:
    base = os.environ.get("PUBLIC_URL")
    if base:
        return base.rstrip("/")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    return f"{proto}://{host}"


def _agent_card(request: Request) -> dict:
    card = json.loads(AGENT_CARD_FILE.read_text(encoding="utf-8"))
    base = _public_base(request)
    raw = json.dumps(card).replace("https://tracecheck.example.com", base)
    return json.loads(raw)


@mcp.custom_route("/.well-known/agent-card.json", methods=["GET"])
async def agent_card(request: Request):
    return JSONResponse(_agent_card(request))


@mcp.custom_route("/.well-known/agent.json", methods=["GET"])  # pre-0.3 A2A path
async def agent_card_legacy(request: Request):
    return JSONResponse(_agent_card(request))


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request):
    return JSONResponse({"status": "ok", "service": "tracecheck", "version": "0.2.0"})


def _extract_args(message: dict) -> dict:
    """Pull goal/trace/outcome from an A2A message: a data part (object) or a text part containing JSON."""
    for part in message.get("parts", []) or []:
        if isinstance(part.get("data"), dict):
            return part["data"]
        text = part.get("text")
        if text:
            try:
                obj = json.loads(text)
                if isinstance(obj, dict):
                    return obj
            except ValueError:
                pass
    return {}


@mcp.custom_route("/a2a/message:send", methods=["POST"])
async def a2a_send_message(request: Request):
    """A2A v1.0 HTTP+JSON SendMessage -> evaluate_trace. Returns a Message with a data part."""
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    message = body.get("message", {}) if isinstance(body, dict) else {}
    args = _extract_args(message)
    if not all(isinstance(args.get(k), str) for k in ("goal", "trace", "outcome")):
        return JSONResponse(
            {"error": "Send a message part {\"data\": {\"goal\": str, \"trace\": str, \"outcome\": str, \"agent_name\": str?}}",
             "schema": TRACE_SCHEMA},
            status_code=400,
        )
    result = evaluate_trace(args["goal"], args["trace"], args["outcome"], args.get("agent_name", "unknown") or "unknown")
    reply = {
        "message": {
            "messageId": str(uuid.uuid4()),
            "contextId": message.get("contextId") or str(uuid.uuid4()),
            "role": "ROLE_AGENT",
            "parts": [
                {"data": result, "mediaType": "application/json"},
                {"text": result["summary"], "mediaType": "text/plain"},
            ],
        }
    }
    return JSONResponse(reply, media_type="application/a2a+json")


def build_http_app():
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.middleware.cors import CORSMiddleware

    host = os.environ.get("HOST", "0.0.0.0")
    # Public hosting sits behind a proxy with arbitrary Host headers; DNS-rebinding protection
    # only matters for localhost-bound servers, so enable it only when binding to loopback.
    security = TransportSecuritySettings(enable_dns_rebinding_protection=host in ("127.0.0.1", "localhost"),
                                         allowed_hosts=["127.0.0.1:*", "localhost:*"],
                                         allowed_origins=["http://127.0.0.1:*", "http://localhost:*"])
    app = mcp.streamable_http_app(stateless_http=True, json_response=True, host=host, transport_security=security)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                       allow_headers=["*"], expose_headers=["mcp-session-id", "mcp-protocol-version"])
    return app, host


def main() -> None:
    use_http = "--http" in sys.argv or os.environ.get("TRACECHECK_TRANSPORT", "stdio").lower() in ("http", "streamable-http")
    if use_http:
        import uvicorn
        app, host = build_http_app()
        port = int(os.environ.get("PORT", "8000"))
        uvicorn.run(app, host=host, port=port)
    else:
        # Default: stdio transport (good for local testing and many hosts)
        mcp.run()


if __name__ == "__main__":
    main()
