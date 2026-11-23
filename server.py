"""
TraceCheck MCP Server
A minimal agent-first service for independent evaluation of agent execution traces.
Helps agents improve their own processes by providing objective feedback.

v0.4.2: framework integration docs (docs/integrations/), linked from /llms.txt. Scoring unchanged.
v0.4.1: GET /llms.txt, descriptions in agent search language (verify my run, self-check, detect loops,
did I succeed), and a trailing bare 'final' step now counts as privacy-light (step type 'final').
v0.4.0 (privacy release): content-free logging (no goal/trace/outcome text is stored, only scores and
counts) and a privacy-light trace format: send step types + statuses only (list of {"type","tool","status"}
or 'action:search ok > error > retry > verify passed'), scored by score_steps(). Free-text path unchanged.
v0.3.1: tool titles + annotations (read-only, idempotent), per-argument schema descriptions, icons,
websiteUrl, GET / description, /icon.png + /icon.svg. Scoring unchanged from v0.3.0.
v0.3: evaluator adds loop/redundancy detection (efficiency dimension), per-error recovery check,
"evidence over claims" for success words, critical-flaw score caps, and richer call logging
(client name/version, user-agent, hashed IP, stdout TRACECHECK_CALL lines). v0.2 base: original/server.py.
Other additions:
- LOG_FILE configurable via TRACECHECK_LOG_FILE (default unchanged)
- get_trace_schema helper tool
- agent-oriented tool descriptions
- optional streamable HTTP transport (--http or TRACECHECK_TRANSPORT=http, binds $PORT)
  with /health, /.well-known/agent-card.json (+ legacy /.well-known/agent.json)
  and a minimal A2A HTTP+JSON endpoint POST /a2a/message:send (+ JSON-RPC message/send at POST /a2a)
"""

from mcp.server import MCPServer  # mcp SDK >= 2.x (MCPServer replaced FastMCP)
from mcp.server.mcpserver import Context
import hashlib
import json
import logging
import os
import re
import sys
import uuid
from typing import Annotated
from datetime import datetime, timezone
from pathlib import Path

from mcp.types import Icon, ToolAnnotations
from pydantic import Field

# Simple file logging for the test phase (path overridable via env)
LOG_FILE = Path(os.environ.get("TRACECHECK_LOG_FILE", "/workspace/artifacts/tracecheck/calls.jsonl"))
try:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
except OSError:  # e.g. installed via uvx/pip on a machine without /workspace
    LOG_FILE = Path.home() / ".tracecheck" / "calls.jsonl"
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

VERSION = "0.4.2"
# stdout carries JSON-RPC in stdio mode, so call lines go to stdout only in HTTP mode (set in main()).
_CALL_STREAM = sys.stderr

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tracecheck")

REPO_URL = "https://github.com/TGS-checker/tracecheck-mcp"
LIVE_URL = "https://tracecheck-mcp.onrender.com"
_ICON_RAW = "https://raw.githubusercontent.com/TGS-checker/tracecheck-mcp/main/assets"
ICONS = [
    Icon(src=f"{_ICON_RAW}/icon.png", mime_type="image/png", sizes=["256x256"]),
    Icon(src=f"{_ICON_RAW}/icon.svg", mime_type="image/svg+xml", sizes=["any"]),
]
# Read-only, deterministic, offline scoring: safe for agents to call freely.
_READ_ONLY = dict(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

mcp = MCPServer(
    "TraceCheck",
    title="TraceCheck",
    website_url=REPO_URL,
    icons=ICONS,
    instructions=(
        "Self-check for AI agents: verify your run before you report success. "
        "Call evaluate_trace with your goal, the steps you took and the outcome you claim; get a reliability score (0-100), "
        "the issues found (loops, unrecovered errors, success claimed without evidence, no verification) and concrete fixes. "
        "Use it to answer 'did I succeed?', evaluate a trace, detect loops, or improve your process between attempts. "
        "No content needed: send step types only (privacy-light format, e.g. 'action:search ok > error > retry > verify passed'). "
        "No goal/trace/outcome text is ever logged or stored. Free during the initial test phase."
    ),
    version=VERSION,
)


EVALUATE_TRACE_DESCRIPTION = """\
Verify my run / self-check: did I succeed? Get an independent, deterministic reliability score (0-100) for a multi-step task you (an agent) just ran, before reporting success. Evaluates the trace, detects loops, unrecovered errors and success claimed without evidence, and returns concrete fixes for your next attempt.

No content needed: send step types only. PRIVACY-LIGHT FORMAT (recommended when your trace holds user data): goal = a generic category (e.g. "data lookup"), outcome = a status ("success", "partial", "failed"), trace = step types + statuses only, either a list [{"type":"action","tool":"search","status":"ok"}, {"type":"error"}, {"type":"retry"}, {"type":"verify","status":"passed"}] or a compact string "action:search ok > error > retry > verify passed". Step types: action, observe, error, retry, fallback, verify, final. Use status "partial" for a paged/incomplete result you will continue (not a loop). TraceCheck never logs or stores goal, trace or outcome text, only the score and counts.

WHEN TO CALL: after you finish (or give up on) a multi-step task that used tools, before you report "done" to the user, or after a failure you want to learn from. Free, no API key, fast and offline (keyword heuristics, no LLM). Call get_trace_schema first if unsure of the format.

INPUT (free-text format, plain strings):
- goal: the original task, stated specifically (what success looks like). Vague goals under 20 chars are penalised.
- trace: your chronological steps, one per line, e.g. "1. Called search_flights(AMS->LIS) -> returned 3 results" / "2. Observed: ..." . Write what each tool RETURNED ("observed", "returned", "result:"), any error and the retry/fallback you used, and a final "Verified: ..." line.
- outcome: the concrete final result you claim, and how it meets the goal.
- agent_name: optional identifier for yourself (logging only).

RETURNS JSON: score (int 0-100), dimensions {goal_clarity, observability, error_recovery, goal_alignment, efficiency}, issues (list), suggestions (list, max 5), summary, version.

THE LOOP: run task -> evaluate_trace -> apply the suggestions to your process -> re-run -> evaluate_trace again and compare scores/dimensions. Scores below ~60 usually mean missing observations, unrecovered errors, repeated steps (loops), success claimed without evidence, or no verification step.
"""

TRACE_SCHEMA = {
    "tool": "evaluate_trace",
    "arguments": {
        "goal": "string, required. Specific task + what success looks like (>= 20 chars).",
        "trace": "string, required. Chronological steps, one per line (numbered). For each tool call: what you called and what it returned/what you observed. Record errors AND the recovery (retry/fallback/alternative). End with a 'Verified: ...' line that checks the outcome against the goal.",
        "outcome": "string, required. The concrete result you claim (>= 15 chars) and how it satisfies the goal.",
        "agent_name": "string, optional. Your identifier, used for logging only.",
    },
    "privacy_light_format": {
        "note": "No content needed: send step types only. Nothing you send is logged or stored (only score + counts).",
        "goal": "a generic category, e.g. 'data lookup', 'booking', 'code change'",
        "trace": "list of steps {type, tool?, status?} OR compact string 'type[:tool] [status] > ...'. "
                 "Types: action, observe, error, retry, fallback, verify, final. Statuses: ok, partial, empty, error/failed, passed.",
        "outcome": "a status: 'success', 'partial' or 'failed' (optionally a category)",
        "example_call": {
            "goal": "data lookup",
            "trace": [{"type": "action", "tool": "search", "status": "ok"},
                      {"type": "action", "tool": "fetch_details", "status": "error"},
                      {"type": "retry", "tool": "fetch_details", "status": "ok"},
                      {"type": "verify", "status": "passed"}],
            "outcome": "success",
            "agent_name": "my-agent",
        },
        "example_compact_trace": "action:search ok > action:fetch_details error > retry:fetch_details ok > verify passed",
    },
    "scored_dimensions": {
        "goal_clarity": "weight 0.10 - is the goal specific and action-oriented",
        "observability": "weight 0.25 - enough distinct (non-repeated) steps, observations of tool results recorded",
        "error_recovery": "weight 0.20 - every error is followed by a visible retry/fallback/fix or verification",
        "goal_alignment": "weight 0.30 - outcome matches goal, success claims backed by evidence, explicit verification step",
        "efficiency": "weight 0.15 - no loops or repeated identical/near-identical steps",
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
    title="Get TraceCheck input schema",
    annotations=ToolAnnotations(title="Get TraceCheck input schema", **_READ_ONLY),
    icons=ICONS,
    description=(
        "Return the exact input format TraceCheck's evaluate_trace expects, the scored dimensions and weights, "
        "and a complete example call you can copy. Call this once before your first evaluate_trace. No arguments, no side effects."
    )
)
def get_trace_schema() -> dict:
    return TRACE_SCHEMA


# --- v0.3 heuristics helpers -------------------------------------------------
_ERROR_WORDS = ["error", "failed", "failure", "exception", "timeout", "timed out", "404", "500", "could not",
                "unable", "denied", "not found", "invalid", "crash"]
_NEGATED_ERRORS = ["no error", "no errors", "0 errors", "zero errors", "without error", "without errors",
                   "error-free", "no failures", "0 failed", "0 failures", "not failed"]
_RECOVERY_WORDS = ["retry", "retried", "tried again", "re-ran", "reran", "rerun", "alternative", "fallback",
                   "instead", "recovered", "fixed", "workaround", "switched to", "resolved"]
_VERIFY_WORDS = ["verified", "verify", "checked", "confirmed against", "matches goal", "validated", "test passed",
                 "tests passed", "status 200", "200 ok", "assert", "double-checked", "cross-checked"]
_SUCCESS_WORDS = ["success", "completed", "done", "confirmed", "booked", "created", "sent", "found"]
_STEP_PREFIX = re.compile(r"^\s*(?:step\s*)?\d+\s*[.):-]\s*|^\s*[-*]\s*", re.I)


def _has_error(line_l: str) -> bool:
    for neg in _NEGATED_ERRORS:
        line_l = line_l.replace(neg, " ")
    return any(w in line_l for w in _ERROR_WORDS)


def _has(line_l: str, words) -> bool:
    return any(w in line_l for w in words)


def _tokens(line: str) -> list:
    return re.findall(r"[a-z0-9_]+", _STEP_PREFIX.sub("", line.lower()))


def _find_redundant(lines: list) -> tuple:
    """Return (redundant_count, most_repeated_step, repeat_count).
    A step is redundant if it repeats (exactly or near: token Jaccard >= 0.8, differing in more than just
    numbers) an earlier step. Repeating the step right after an error is a retry and is allowed (max 2 in a row)."""
    seen, redundant, counts, retry_run = [], 0, {}, 0
    prev_error = False
    for line in lines:
        toks = _tokens(line)
        if not toks:
            continue
        tset, key = set(toks), " ".join(toks)
        dup_of = None
        for prev_key, prev_set in seen:
            if key == prev_key:
                dup_of = prev_key
                break
            union = tset | prev_set
            diff = tset ^ prev_set
            if union and len(tset & prev_set) / len(union) >= 0.8 and any(not t.isdigit() for t in diff):
                dup_of = prev_key
                break
        if dup_of is not None:
            if prev_error and retry_run < 2:
                retry_run += 1  # legitimate retry after a failure
            else:
                redundant += 1
                counts[dup_of] = counts.get(dup_of, 1) + 1
        else:
            seen.append((key, tset))
            retry_run = 0
        prev_error = _has_error(line.lower())
    top = max(counts.items(), key=lambda kv: kv[1]) if counts else (None, 0)
    return redundant, top[0], top[1]


def _unresolved_errors(lines: list) -> tuple:
    """Return (error_count, unresolved_count). An error is resolved if its own line or any later line shows a
    recovery (retry/fallback/fix) or a verification."""
    errors, unresolved = 0, 0
    pending = 0
    for line in lines:
        l = line.lower()
        recovers = _has(l, _RECOVERY_WORDS) or _has(l, _VERIFY_WORDS)
        if _has_error(l):
            errors += 1
            if not _has(l, _RECOVERY_WORDS):
                pending += 1
            continue
        if recovers:
            pending = 0
    unresolved = pending
    return errors, unresolved


def _client_from_ctx(ctx) -> dict:
    """Best-effort client identity from the MCP request context (never raises)."""
    info = {}
    if ctx is None:
        return info
    try:
        rc = ctx.request_context
    except Exception:
        return info
    try:
        params = rc.session.client_params
        if params is not None and params.client_info is not None:
            info["client_name"] = params.client_info.name
            info["client_version"] = params.client_info.version
    except Exception:
        pass
    try:
        if not info.get("client_name") and rc.meta is not None:  # stateless clients may send clientInfo in _meta
            meta = rc.meta.model_dump() if hasattr(rc.meta, "model_dump") else dict(rc.meta)
            for k, v in meta.items():
                if "clientinfo" in k.lower().replace("_", "") and isinstance(v, dict):
                    info["client_name"], info["client_version"] = v.get("name"), v.get("version")
    except Exception:
        pass
    info.update(_client_from_request(getattr(rc, "request", None)))
    if not info.get("client_name"):
        info.update(_RECENT_CLIENTS.get((info.get("ip_hash"), info.get("user_agent")), {}))
    info["transport"] = "http" if getattr(rc, "request", None) is not None else "stdio"
    return info


def _client_from_request(request) -> dict:
    if request is None or not hasattr(request, "headers"):
        return {}
    out = {"user_agent": (request.headers.get("user-agent") or "")[:200] or None}
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if not ip and getattr(request, "client", None):
        ip = request.client.host
    if ip:
        salt = os.environ.get("TRACECHECK_IP_SALT", "tracecheck-v0")
        out["ip_hash"] = hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()[:16]
    return out


@mcp.tool(
    title="Evaluate agent execution trace",
    annotations=ToolAnnotations(title="Evaluate agent execution trace", **_READ_ONLY),
    icons=ICONS,
    description=EVALUATE_TRACE_DESCRIPTION,
)
def evaluate_trace(
    goal: Annotated[str, Field(description=(
        "The original task, stated specifically, including what success looks like "
        "(e.g. 'Find the cheapest direct AMS->LIS flight on 12 March and report price and flight number'). "
        "With a privacy-light trace a generic category is enough (e.g. 'data lookup'). Never logged or stored."))],
    trace: Annotated[str | list[dict], Field(description=(
        "No content needed: send step types only. Either (a) privacy-light: a list of steps like "
        "{'type':'action','tool':'search','status':'ok'}, {'type':'error'}, {'type':'retry'}, {'type':'verify','status':'passed'} "
        "or the compact string 'action:search ok > error > retry > verify passed'; or (b) free text: chronological "
        "steps, one per line, with what each tool returned, every error AND the retry/fallback, and a final "
        "'Verified: ...' line. Never logged or stored."))],
    outcome: Annotated[str, Field(description=(
        "The concrete final result you claim and how it satisfies the goal; back success claims with evidence. "
        "With a privacy-light trace just send a status: 'success', 'partial' or 'failed'. Never logged or stored."))],
    agent_name: Annotated[str, Field(description=(
        "Optional identifier for yourself or your agent (e.g. 'my-travel-agent'). Used for logging and "
        "usage counts only; it does not affect the score."))] = "unknown",
    ctx: Context | None = None,
) -> dict:
    """MCP tool wrapper: score the trace, then log the call with client info."""
    result = score_trace(goal, trace, outcome)
    record_call(trace, agent_name, result, _client_from_ctx(ctx))
    return result


# --- v0.4 privacy-light format: step types + statuses only --------------------
_STEP_TYPES = {
    "action": "action", "call": "action", "tool": "action", "act": "action",
    "observe": "observe", "observation": "observe", "result": "observe",
    "error": "error", "fail": "error", "failed": "error", "failure": "error", "exception": "error",
    "retry": "retry", "fallback": "fallback", "alternative": "fallback", "fix": "fallback",
    "verify": "verify", "verification": "verify", "check": "verify", "test": "verify", "validate": "verify",
    # v0.4.1: a closing/hand-off step (e.g. '... > final'). Neutral marker: not an action, error or verification.
    "final": "final", "done": "final", "finish": "final", "end": "final", "answer": "final", "report": "final",
    "respond": "final", "reply": "final",
}
_ERROR_STATUSES = {"error", "failed", "fail", "failure", "timeout", "exception", "denied", "invalid", "notfound",
                   "not_found", "4xx", "5xx", "500", "404", "crash"}
_PASS_STATUSES = {"passed", "pass", "ok", "success", "true", "matched", "match", "200"}
_CONTINUE_STATUSES = {"partial", "more", "has_more", "next", "paged", "continue"}
_COMPACT_SEG = re.compile(r"^(?:\d+\s*[.)]\s*)?([a-z_]+)(?::([\w.\-/]+))?(?:\s*[:=]?\s*([\w\-]+))?$")


def _parse_steps(trace) -> list | None:
    """Return a list of {type, tool, status} if `trace` is in the privacy-light format, else None."""
    if isinstance(trace, str):
        t = trace.strip()
        if t.startswith("["):
            try:
                trace = json.loads(t)
            except ValueError:
                return None
        else:
            segs = [x for x in re.split(r"\s*(?:->|=>|>|\n|;|\|)\s*", t.lower()) if x.strip()]
            steps = []
            for seg in segs:
                m = _COMPACT_SEG.match(seg.strip())
                if not m or m.group(1) not in _STEP_TYPES:
                    return None
                steps.append({"type": m.group(1), "tool": m.group(2), "status": m.group(3)})
            trace = steps
    if not isinstance(trace, list) or not trace:
        return None
    out = []
    for st in trace:
        if isinstance(st, str):
            sub = _parse_steps(st)
            if not sub:
                return None
            out.extend(sub)
            continue
        if not isinstance(st, dict):
            return None
        typ = _STEP_TYPES.get(str(st.get("type", "")).strip().lower())
        if typ is None:
            return None
        tool = st.get("tool") or st.get("name")
        status = st.get("status")
        out.append({"type": typ, "tool": str(tool).strip().lower()[:60] if tool else None,
                    "status": str(status).strip().lower().replace(" ", "_")[:30] if status is not None else None})
    return out


def _is_error_step(st: dict) -> bool:
    if st["type"] == "final":  # 'final failed' reports the outcome; it is not a new error to recover from
        return False
    return st["type"] == "error" or (st["status"] in _ERROR_STATUSES)


def score_steps(goal: str, steps: list, outcome: str) -> dict:
    """Score a privacy-light trace (step types + statuses only). Same dimensions/weights/caps as score_trace."""
    issues, suggestions, dimensions, caps = [], [], {}, []
    goal_l, outcome_l = (goal or "").lower().strip(), (outcome or "").lower().strip()
    n = len(steps)

    # 1. Goal clarity: a generic category is enough here (content is intentionally absent)
    if len(goal_l) >= 3:
        dimensions["goal_clarity"] = 80
    else:
        dimensions["goal_clarity"] = 40
        issues.append("Goal category is missing.")
        suggestions.append("Send a generic goal category (e.g. 'data lookup', 'booking', 'code change').")

    # 2. Efficiency: the same tool called again without an error/retry in between or a 'partial' status before it
    last_status, redundant, counts, prev_error = {}, 0, {}, False
    for st in steps:
        tool = st["tool"]
        if st["type"] in ("action", "retry", "fallback") and tool:
            if tool in last_status and st["type"] == "action" and not prev_error \
                    and last_status[tool] not in _CONTINUE_STATUSES and last_status[tool] not in _ERROR_STATUSES:
                redundant += 1
                counts[tool] = counts.get(tool, 1) + 1
            last_status[tool] = st["status"] or "ok"
        prev_error = _is_error_step(st)
    distinct = n - redundant

    # 3. Observability: enough distinct steps, and a status recorded on actions
    obs = 50
    if distinct < 3:
        obs -= 25
        issues.append("Trace has too few distinct steps; hard to assess process quality.")
        suggestions.append("Send one step per tool call, plus error/retry/verify steps where they happened.")
    if distinct >= 5:
        obs += 20
    actions = [s for s in steps if s["type"] == "action"]
    with_status = sum(1 for s in actions if s["status"]) + sum(1 for s in steps if s["type"] == "observe")
    if actions and with_status >= 0.8 * len(actions):
        obs += 15
    else:
        issues.append("Most actions carry no status (ok/partial/empty/error), so their results are not observable.")
        suggestions.append("Add a status to every action step, e.g. {'type':'action','tool':'search','status':'ok'}.")
    dimensions["observability"] = max(0, min(100, obs))

    eff = 100 if distinct >= 3 else 60
    if redundant:
        eff -= min(80, 15 * redundant)
        top, top_n = max(counts.items(), key=lambda kv: kv[1])
        if redundant >= 2 or redundant / n >= 0.3:
            issues.append(f"Loop detected: {redundant} repeated call(s) of the same tool without an error or new "
                          f"information in between (e.g. '{top}' x{top_n}).")
            suggestions.append("Before repeating a call, check whether its last result changes your plan; change the "
                               "tool/parameters or stop. Mark paged results with status 'partial' so they are not a loop.")
        else:
            issues.append(f"Redundant step: tool '{top}' was called again after a successful result.")
            suggestions.append("Reuse the earlier result instead of repeating a successful call.")
        if redundant >= 3 and redundant / n >= 0.4:
            caps.append((45, "most of the trace is a repeat loop"))
    dimensions["efficiency"] = max(0, eff)

    # 4. Error recovery: each error must be followed by retry/fallback, a successful action, or a passed verify
    # (a later successful action only resolves errors of that same tool; moving on to another tool does not).
    errors, pending, last_tool = 0, [], None
    for st in steps:
        if _is_error_step(st):
            errors += 1
            pending.append(st["tool"] or last_tool)
        elif st["type"] in ("retry", "fallback") or (st["type"] == "verify" and st["status"] in _PASS_STATUSES):
            pending = []
        elif st["type"] in ("action", "observe") and st["tool"]:
            pending = [t for t in pending if t != st["tool"]]
        if st["tool"]:
            last_tool = st["tool"]
    unresolved = len(pending)
    err = 75
    if unresolved:
        err -= 35 + 15 * (unresolved - 1)
        issues.append(f"{unresolved} error(s) in the trace are not followed by any recovery (retry, fallback, fix) or verification.")
        suggestions.append("When a step fails, add a retry/fallback step (and its status) before continuing.")
    elif errors:
        err += 10
    dimensions["error_recovery"] = max(0, min(100, err))

    # 5. Alignment: outcome status vs. what the steps show
    align = 60
    verifies = [s for s in steps if s["type"] == "verify"]
    passed = any(s["status"] in _PASS_STATUSES or s["status"] is None for s in verifies)
    failed_verify = any(s["status"] in _ERROR_STATUSES or s["status"] in ("not_passed", "mismatch", "false") for s in verifies)
    claims_success = any(w in outcome_l for w in ("success", "succeeded", "ok", "done", "complete", "passed")) \
        and not any(w in outcome_l for w in ("partial", "fail", "unsuccessful", "not "))
    if not outcome_l:
        align -= 30
        issues.append("Outcome status is missing.")
        suggestions.append("Send an outcome status: 'success', 'partial' or 'failed'.")
    else:
        align += 15  # an explicit outcome status maps directly onto the goal category
    if claims_success and (unresolved or (failed_verify and not passed)):
        align -= 35
        issues.append("Outcome claims success, but the steps show unresolved errors or a failed verification.")
        suggestions.append("Only report success when the last verification passed; otherwise report 'partial' or 'failed'.")
        caps.append((40, "success claimed over unresolved errors"))
    elif claims_success:
        align += 10
    if passed:
        align += 15
    else:
        align -= 10
        issues.append("No passed verification step against the goal is visible.")
        suggestions.append("End with a verify step, e.g. {'type':'verify','status':'passed'}.")
    dimensions["goal_alignment"] = max(0, min(100, align))
    return _finish(dimensions, issues, suggestions, caps, privacy_mode=True)


def score_trace(goal: str, trace, outcome: str) -> dict:
    """Dispatch: privacy-light step format -> score_steps, free text -> heuristic text scoring."""
    steps = _parse_steps(trace)
    if steps is not None:
        return score_steps(goal, steps, outcome)
    if isinstance(trace, list):
        trace = "\n".join(str(x) for x in trace)
    return score_text_trace(goal, trace, outcome)


def score_text_trace(goal: str, trace: str, outcome: str) -> dict:
    """
    Evaluate an AI agent's execution trace and return a reliability score plus improvement suggestions.
    Pure function (no logging). Returns score, issues, suggestions, summary, dimensions, version.
    """
    # Heuristic multi-dimension analysis (fully offline). Later replaceable by LLM-as-judge.
    issues = []
    suggestions = []
    dimensions = {}
    caps = []  # (max_score, reason) for critical flaws

    goal_l = goal.lower().strip()
    trace_l = trace.lower()
    outcome_l = outcome.lower().strip()
    lines = [l.strip() for l in trace.splitlines() if l.strip()]

    # 1. Goal clarity (unchanged)
    goal_score = 80
    if len(goal.strip()) < 20:
        goal_score -= 30
        issues.append("Goal is too short or vague to evaluate against.")
        suggestions.append("Restate the goal more specifically (what success looks like, constraints, expected output).")
    if not any(w in goal_l for w in ["find", "book", "create", "analyze", "get", "write", "send", "check", "calculate", "research"]):
        goal_score -= 10
    dimensions["goal_clarity"] = max(0, goal_score)

    # 2. Efficiency: loops / redundant steps (new in v0.3)
    redundant, top_step, top_count = _find_redundant(lines)

    # 3. Trace structure & observability (redundant steps don't count as distinct steps)
    step_like = sum(1 for l in lines if any(l.lower().startswith(p) for p in ("1.", "2.", "-", "*", "step", "called", "observed", "tool")))
    step_count = max(len(lines), step_like, trace.count(". ") + 1) - redundant
    obs_score = 50
    if step_count < 3:
        obs_score -= 25
        issues.append("Trace has too few distinct steps; hard to assess process quality.")
        suggestions.append("Break the work into numbered or clearly separated steps with an observation after each major action.")
    if step_count >= 5:
        obs_score += 20
    if any(w in trace_l for w in ["observed", "result:", "returned", "output:", "saw", "found", "tests passed", "status 2"]):
        obs_score += 15
    else:
        issues.append("Trace lacks explicit observations of tool results or intermediate outcomes.")
        suggestions.append("After every tool call or action, record what was actually returned or observed.")
    if len(trace) < 80:
        obs_score -= 20
        issues.append("Trace is too short to verify completeness or reasoning.")
        suggestions.append("Include more intermediate reasoning and results, not just the final claim.")
    dimensions["observability"] = max(0, min(100, obs_score))

    eff_score = 100 if step_count >= 3 else 60  # can't credit efficiency on a near-empty trace
    if redundant:
        eff_score -= min(80, 15 * redundant)
        ratio = redundant / max(1, len(lines))
        example = f" (e.g. '{top_step[:80]}' x{top_count})" if top_step else ""
        if redundant >= 2 or ratio >= 0.3:
            issues.append(f"Loop detected: {redundant} step(s) repeat earlier steps without new information{example}.")
            suggestions.append("Before repeating a call, check whether its last result changes your plan; if the same call "
                               "returned the same thing, change the query/tool/parameters or stop and report what is missing.")
        else:
            issues.append(f"Redundant step: {redundant} step repeats an earlier step{example}.")
            suggestions.append("Remove repeated identical calls; reuse the earlier result instead.")
        if redundant >= 3 and ratio >= 0.4:
            caps.append((45, "most of the trace is a repeat loop"))
    dimensions["efficiency"] = max(0, eff_score)

    # 4. Error handling & recovery (per-error: is it followed by a retry/fix/verification?)
    error_count, unresolved = _unresolved_errors(lines)
    err_score = 75
    if unresolved:
        err_score -= 35 + 15 * (unresolved - 1)
        issues.append(f"{unresolved} error(s)/failure(s) in the trace are not followed by any recovery (retry, fallback, fix) or verification.")
        suggestions.append("When a step fails, explicitly try a recovery (retry, alternative tool, or adjusted approach) and record it; "
                           "do not continue with steps that depend on the failed result.")
    elif error_count:
        err_score += 10
    dimensions["error_recovery"] = max(0, min(100, err_score))

    # 5. Goal–outcome alignment: evidence over claims
    align_score = 60
    goal_words = set(w for w in goal_l.split() if len(w) > 4)
    outcome_words = set(w for w in outcome_l.split() if len(w) > 4)
    if len(goal_words & outcome_words) >= 2:
        align_score += 15
    claims_success = any(w in outcome_l for w in _SUCCESS_WORDS)
    if claims_success and unresolved:
        align_score -= 35
        issues.append("Outcome claims success, but the trace contains unresolved errors: the claim is not supported by evidence.")
        suggestions.append("Only report success for steps whose results you observed; state explicitly what failed and what is still open.")
        caps.append((40, "success claimed over unresolved errors"))
    elif claims_success:
        align_score += 10
    if not outcome or len(outcome.strip()) < 15:
        align_score -= 30
        issues.append("Outcome is missing or too vague to link back to the goal.")
        suggestions.append("Describe the concrete result and explicitly state how it satisfies the original goal.")
    if any(w in trace_l for w in _VERIFY_WORDS):
        align_score += 15
    else:
        align_score -= 10
        issues.append("No explicit verification step against the original goal is visible.")
        suggestions.append("Add a final check: restate the goal and confirm the outcome actually meets it (with evidence, e.g. a re-read, test result or status code).")
    dimensions["goal_alignment"] = max(0, min(100, align_score))

    return _finish(dimensions, issues, suggestions, caps, privacy_mode=False)


def _finish(dimensions: dict, issues: list, suggestions: list, caps: list, privacy_mode: bool) -> dict:
    # 6. Overall score (weighted), then critical-flaw caps
    weights = {"goal_clarity": 0.10, "observability": 0.25, "error_recovery": 0.20, "goal_alignment": 0.30, "efficiency": 0.15}
    score = int(max(0, min(100, round(sum(dimensions[k] * weights[k] for k in weights)))))
    for cap, _reason in caps:
        score = min(score, cap)

    if score >= 85 and not issues:
        issues.append("No major structural issues detected.")
        suggestions.append("Consider adding an explicit verification step and more detailed observations to make the process more robust.")
    elif not suggestions:
        suggestions.append("Increase the number of observable intermediate steps and add a final goal-verification check.")

    issues_out = issues[:5]
    suggestions_out = suggestions[:5]
    summary = (
        f"TraceCheck score: {score}/100. "
        f"Dimensions — observability {dimensions['observability']}, "
        f"error recovery {dimensions['error_recovery']}, "
        f"goal alignment {dimensions['goal_alignment']}, "
        f"efficiency {dimensions['efficiency']}. "
        f"{len(issues_out)} issue(s) noted."
        + (f" Capped: {'; '.join(r for _, r in caps)}." if caps else "")
    )
    return {
        "score": score,
        "issues": issues_out,
        "suggestions": suggestions_out,
        "summary": summary,
        "dimensions": dimensions,
        "privacy_mode": privacy_mode,
        "version": VERSION,
    }


_LOG_CLIENT_FIELDS = ("client_name", "client_version", "transport", "user_agent", "ip_hash")


def record_call(trace, agent_name: str, result: dict, client: dict | None = None) -> None:
    """Log one evaluation: JSONL file + one `TRACECHECK_CALL {json}` line on stdout (shows in Render logs).
    Content-free by design (v0.4): no goal, trace or outcome text, only scores and counts."""
    client = client or {}
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "version": VERSION,
        "agent_name": str(agent_name)[:80],
        "score": result["score"],
        "dimensions": result["dimensions"],
        "issue_count": len(result["issues"]),
        "trace_length": len(trace),  # characters for free text, number of steps for a list
        "privacy_mode": bool(result.get("privacy_mode")),
        **{k: client.get(k) for k in _LOG_CLIENT_FIELDS if client.get(k) is not None},
    }
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.warning(f"could not write call log: {e}")
    print("TRACECHECK_CALL " + json.dumps(entry, ensure_ascii=False), file=_CALL_STREAM, flush=True)
    logger.info(f"Evaluated trace from {agent_name}: score={result['score']}")


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
    return JSONResponse({"status": "ok", "service": "tracecheck", "version": VERSION})


ASSETS_DIR = Path(__file__).with_name("assets")


async def _asset(name: str, media_type: str):
    from starlette.responses import Response
    f = ASSETS_DIR / name
    if not f.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return Response(f.read_bytes(), media_type=media_type, headers={"Cache-Control": "public, max-age=86400"})


@mcp.custom_route("/icon.png", methods=["GET"])
async def icon_png(request: Request):
    return await _asset("icon.png", "image/png")


@mcp.custom_route("/icon.svg", methods=["GET"])
async def icon_svg(request: Request):
    return await _asset("icon.svg", "image/svg+xml")


LLMS_TXT_FILE = Path(__file__).with_name("llms.txt")


@mcp.custom_route("/llms.txt", methods=["GET"])
async def llms_txt(request: Request):
    """llms.txt convention: a plain-markdown summary for agents (what TraceCheck is, when to call it, endpoints)."""
    from starlette.responses import PlainTextResponse
    if not LLMS_TXT_FILE.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    text = LLMS_TXT_FILE.read_text(encoding="utf-8").replace(LIVE_URL, _public_base(request))
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8",
                             headers={"Cache-Control": "public, max-age=3600"})


@mcp.custom_route("/", methods=["GET"])
async def root(request: Request):
    base = _public_base(request)
    return JSONResponse({
        "name": "TraceCheck",
        "version": VERSION,
        "description": "Self-check for AI agents: verify your run before reporting success. Scores an agent's execution "
                       "trace (goal, steps, outcome) 0-100 for reliability, detects loops and unrecovered errors, and "
                       "returns concrete fixes. Deterministic, offline heuristics. Free, no API key. "
                       "No content needed: send step types only (privacy-light format); no goal/trace/outcome text "
                       "is logged or stored.",
        "tools": ["evaluate_trace", "get_trace_schema"],
        "links": {
            "mcp": f"{base}/mcp",
            "a2a_agent_card": f"{base}/.well-known/agent-card.json",
            "a2a": f"{base}/a2a",
            "health": f"{base}/health",
            "llms_txt": f"{base}/llms.txt",
            "icon": f"{base}/icon.png",
            "source": REPO_URL,
            "docs": f"{REPO_URL}#readme",
            "registry_name": "io.github.TGS-checker/tracecheck",
        },
    })


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


def _valid_args(args: dict) -> bool:
    return isinstance(args.get("goal"), str) and isinstance(args.get("outcome"), str) \
        and isinstance(args.get("trace"), (str, list))


@mcp.custom_route("/a2a/message:send", methods=["POST"])
async def a2a_send_message(request: Request):
    """A2A v1.0 HTTP+JSON SendMessage -> evaluate_trace. Returns a Message with a data part."""
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    message = body.get("message", {}) if isinstance(body, dict) else {}
    args = _extract_args(message)
    if not _valid_args(args):
        return JSONResponse(
            {"error": "Send a message part {\"data\": {\"goal\": str, \"trace\": str | list, \"outcome\": str, \"agent_name\": str?}}. "
                      "No content needed: send step types only, e.g. trace 'action:search ok > error > retry > verify passed'.",
             "schema": TRACE_SCHEMA},
            status_code=400,
        )
    result = score_trace(args["goal"], args["trace"], args["outcome"])
    record_call(args["trace"], args.get("agent_name", "unknown") or "unknown", result,
                {**_client_from_request(request), "transport": "a2a"})
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


A2A_HELP = ("TraceCheck scores agent execution traces. Send one message part "
            "{\"data\": {\"goal\": str, \"trace\": str, \"outcome\": str, \"agent_name\": str?}} "
            "(or the same object as JSON text). trace = chronological steps, one per line, with what each tool "
            "returned, errors with their recovery, and a final 'Verified:' line. No content needed: send step "
            "types only, e.g. {\"goal\": \"data lookup\", \"trace\": \"action:search ok > error > retry > verify passed\", "
            "\"outcome\": \"success\"}. Nothing you send is logged or stored.")


@mcp.custom_route("/a2a", methods=["POST"])
async def a2a_jsonrpc(request: Request):
    """A2A JSON-RPC binding (v0.3 `message/send`, v1.0 `SendMessage`) -> evaluate_trace.
    Messages without a trace get a help reply (not an error), so generic A2A clients get a usable answer."""
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
    if not isinstance(body, dict):
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}})
    rpc_id = body.get("id")
    method = body.get("method")
    if method not in ("message/send", "SendMessage"):
        return JSONResponse({"jsonrpc": "2.0", "id": rpc_id,
                             "error": {"code": -32601, "message": f"Method not found: {method}. Supported: message/send"}})
    v03 = method == "message/send"
    message = (body.get("params") or {}).get("message") or {}
    args = _extract_args(message)
    if _valid_args(args):
        result = score_trace(args["goal"], args["trace"], args["outcome"])
        record_call(args["trace"], args.get("agent_name", "unknown") or "unknown", result,
                    {**_client_from_request(request), "transport": "a2a-jsonrpc"})
        parts = [{"kind": "data", "data": result}, {"kind": "text", "text": result["summary"]}]
    else:
        parts = [{"kind": "text", "text": A2A_HELP}, {"kind": "data", "data": {"schema": TRACE_SCHEMA}}]
    if not v03:
        parts = [{k: v for k, v in p.items() if k != "kind"} for p in parts]
    reply = {"messageId": str(uuid.uuid4()), "contextId": message.get("contextId") or str(uuid.uuid4()),
             "role": "agent" if v03 else "ROLE_AGENT", "parts": parts}
    if v03:
        reply["kind"] = "message"
        result_obj = reply
    else:
        result_obj = {"message": reply}
    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": result_obj})


# Stateless HTTP: clientInfo arrives only on the `initialize` POST (or, for 2026+ clients, in each request's
# _meta). This middleware remembers it per (hashed IP, user-agent) so the later tools/call can be attributed.
_RECENT_CLIENTS: dict = {}


def _remember_client(key: tuple, info: dict) -> None:
    if len(_RECENT_CLIENTS) > 2000:
        _RECENT_CLIENTS.clear()
    _RECENT_CLIENTS[key] = {"client_name": info.get("name"), "client_version": info.get("version")}


class ClientInfoSniffer:
    """Pure-ASGI middleware: peeks at POST /mcp JSON bodies for clientInfo, then replays the body unchanged."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST" or not scope.get("path", "").startswith("/mcp"):
            return await self.app(scope, receive, send)
        chunks, more = [], True
        while more:
            msg = await receive()
            chunks.append(msg.get("body", b""))
            more = msg.get("more_body", False)
        body = b"".join(chunks)
        try:
            payload = json.loads(body) if body else None
            msgs = payload if isinstance(payload, list) else [payload]
            for m in msgs:
                params = (m or {}).get("params") or {}
                info = params.get("clientInfo") or (params.get("_meta") or {}).get("io.modelcontextprotocol/clientInfo")
                if isinstance(info, dict):
                    from starlette.requests import Request as _Req
                    c = _client_from_request(_Req(scope))
                    _remember_client((c.get("ip_hash"), c.get("user_agent")), info)
        except Exception:
            pass
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


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
    app.add_middleware(ClientInfoSniffer)
    return app, host


def main() -> None:
    use_http = "--http" in sys.argv or os.environ.get("TRACECHECK_TRANSPORT", "stdio").lower() in ("http", "streamable-http")
    if use_http:
        global _CALL_STREAM
        _CALL_STREAM = sys.stdout
        import uvicorn
        app, host = build_http_app()
        port = int(os.environ.get("PORT", "8000"))
        uvicorn.run(app, host=host, port=port)
    else:
        # Default: stdio transport (good for local testing and many hosts)
        mcp.run()


if __name__ == "__main__":
    main()
