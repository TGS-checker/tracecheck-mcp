"""
TraceCheck MCP Server
A minimal agent-first service for independent evaluation of agent execution traces.
Helps agents improve their own processes by providing objective feedback.

v0.5.1: order and open-end codes (handoff_before_verification, unresolved_partial_observation,
write_before_complete_data, partial_outcome_without_handover), step type handoff, result fixes start with the
goal-specific check, error recovery not applicable without errors, progress compares score_delta per goal type only.
v0.5.0: issue codes + fix ids (codes are the contract), host reasons, optional agent_id / previous_rating /
previous_need_id, need_id, progress, benchmark + measured fix effectiveness from data/serving.json, content-free
event schema, POD scoring points (incomplete data, verify_kind, requirement_lowered, stakes, responsive sub-scores).
v0.4.3: host-principle fixes. POST /a2a/message:send returns HTTP 200 with a help message + example
on a plain-text first call (and scores a compact privacy-light trace if the text is one);
aliases /a2a/health, /a2a/.well-known/agent-card.json; GET/HEAD /a2a; evaluate_trace is
lenient on missing fields and returns a ready-to-use example; GET /.well-known/mcp.json and
/robots.txt. Scoring unchanged from v0.4.2.
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
from mcp.server.mcpserver.exceptions import ToolError
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

VERSION = "0.5.1"
SCORING_VERSION = "0.5.1"  # POD batch 2026-11-27: order/open-end codes, goal-specific fixes, recovery calibration
# stdout carries JSON-RPC in stdio mode, so call lines go to stdout only in HTTP mode (set in main()).
_CALL_STREAM = sys.stderr

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tracecheck")

# --- Scoring configuration ---------------------------------------------------------
# The scoring code is open; the tuned scoring knowledge (weights, caps, thresholds, word and
# status lists) is private. Load order: TRACECHECK_SCORING_CONFIG_PATH (a secret file), then
# TRACECHECK_SCORING_CONFIG (JSON string), then the public fallback in this repo. The public
# fallback is generic and coarse: it works and gives sensible scores, but it is not the tuned
# configuration that runs live. Only the source is ever logged or exposed, never values.
SCORING_CONFIG_FALLBACK_FILE = Path(__file__).with_name("scoring_config.fallback.json")
_SCORING_SECTIONS = ("weights", "caps", "efficiency", "steps", "text", "statuses", "words")


def _config_shape_errors(cfg, ref) -> list:
    """Key names (never values) of whole sections that are missing or wrong-typed. Such a config is unusable."""
    if not isinstance(cfg, dict):
        return ["<root>"]
    return [top for top in list(_SCORING_SECTIONS) + ["goal_categories", "verify_fit"] if not isinstance(cfg.get(top), dict)]


def _fill_missing_keys(cfg: dict, ref: dict) -> list:
    """Fill keys that a (older) private config lacks with the public fallback value, in place.

    This lets new code run on an older private config without breaking live scoring: only the new keys
    use coarse fallback values. Returns the filled key names (never values). New goal categories are put
    first, because they are more specific than the older ones."""
    filled = []
    for top in _SCORING_SECTIONS:
        for k, v in ref[top].items():
            if k not in cfg[top]:
                cfg[top][k] = v
                filled.append(f"{top}.{k}")
    new_cats = {k: v for k, v in ref["goal_categories"].items() if k not in cfg["goal_categories"]}
    if new_cats:
        cfg["goal_categories"] = {**new_cats, **cfg["goal_categories"]}
        filled += [f"goal_categories.{k}" for k in new_cats]
    for k, v in ref["verify_fit"].items():
        if k not in cfg["verify_fit"]:
            cfg["verify_fit"][k] = v
            filled.append(f"verify_fit.{k}")
    return filled


SCORING_CONFIG_FILLED: list = []  # key names filled from the fallback (complete private config -> empty)


def load_scoring_config(env=None) -> tuple[dict, str]:
    """Return (config, source) with source 'private' or 'fallback'. Never logs values."""
    env = os.environ if env is None else env
    fallback = json.loads(SCORING_CONFIG_FALLBACK_FILE.read_text(encoding="utf-8"))
    SCORING_CONFIG_FILLED.clear()
    candidates = []
    path = (env.get("TRACECHECK_SCORING_CONFIG_PATH") or "").strip()
    if path:
        try:
            candidates.append(("path", Path(path).read_text(encoding="utf-8")))
        except OSError as exc:
            # Diagnose without values: error kind and which file names nearby look like a scoring file.
            parent = Path(path).parent
            try:
                near = sorted(n.name for n in parent.iterdir() if "scor" in n.name.lower())[:5]
                n_files = len(list(parent.iterdir()))
            except OSError:
                near, n_files = [], -1
            logger.warning("scoring_config_error=path_unreadable kind=%s dir_exists=%s dir_files=%s similar=%s",
                           type(exc).__name__, parent.is_dir(), n_files, ",".join(repr(n) for n in near) or "-")
    raw = (env.get("TRACECHECK_SCORING_CONFIG") or "").strip()
    if raw:
        candidates.append(("env", raw))
    for kind, text in candidates:
        try:
            cfg = json.loads(text)
        except ValueError:
            logger.warning("scoring_config_error=%s_invalid_json", kind)
            continue
        missing = _config_shape_errors(cfg, fallback)
        if missing:
            logger.warning("scoring_config_error=%s_incomplete keys=%s", kind, ",".join(missing[:20]))
            continue
        filled = _fill_missing_keys(cfg, fallback)
        if filled:
            SCORING_CONFIG_FILLED.extend(filled)
            logger.warning("scoring_config_partial=%s filled_from_fallback=%d keys=%s", kind, len(filled),
                           ",".join(filled[:40]))
        return cfg, "private"
    return fallback, "fallback"


SC, SCORING_CONFIG_SOURCE = load_scoring_config()
logger.info("scoring_config=%s", SCORING_CONFIG_SOURCE)
_W, _CAPS, _EFF, _ST, _TX = SC["weights"], SC["caps"], SC["efficiency"], SC["steps"], SC["text"]

REPO_URL = "https://github.com/TGS-checker/tracecheck-mcp"
LIVE_URL = "https://tracecheck-mcp.onrender.com"

# Actionable, machine-readable help returned on wrong or rejected calls (host principle).
# Use-case signposts: only proven, live cases. Single source; docs/use-cases.md must match (tested).
# Never put scoring rules, thresholds or detection details here. Register: private knowledge base.
REGISTRY_DESCRIPTION = "Agent self-check: trace evaluation, 0-100 reliability score, loop detection, missing verification."  # = server.json

LIVE_USE_CASES = {
    "UC-001": "Your agent claims success without checking that the result actually holds? "
              "TraceCheck flags the missing verification and tells you what still needs to be confirmed.",
}
_UC_TRIGGER = {"UC-001": "missing_verification"}

_SAMPLE_ARGS = {
    "goal": "data lookup",
    "trace": "action:search ok > error > retry > verify passed",
    "outcome": "success",
}
HOW_TO_CHECK_TRACE = [
    "Call get_trace_schema for the exact input format and a copy-paste example.",
    "Send step types only (privacy-light), e.g. 'action:search ok > error > retry > verify passed'.",
    "Read the score (0-100), issues and suggestions; apply the fixes and evaluate again.",
]


def _service_base(request=None) -> str:
    if request is not None:
        return _public_base(request)
    return (os.environ.get("PUBLIC_URL") or LIVE_URL).rstrip("/")


def _help_links(base: str | None = None) -> dict:
    root = (base or LIVE_URL).rstrip("/")
    return {
        "mcp": f"{root}/mcp",
        "a2a_agent_card": f"{root}/.well-known/agent-card.json",
        "a2a": f"{root}/a2a",
        "llms_txt": f"{root}/llms.txt",
        "health": f"{root}/health",
    }


def _example_call() -> dict:
    """One compact working call: evaluate_trace with a privacy-light trace."""
    return {"name": "evaluate_trace", "arguments": dict(_SAMPLE_ARGS)}


def actionable_error(error: str, *, hint: str | None = None, request=None, base: str | None = None, **extra) -> dict:
    """Short machine-readable error: what went wrong, one example, docs. No duplicated blocks."""
    root = base or _service_base(request)
    body = {
        "error": error,
        "hint": hint or (
            "POST /mcp tools/call or POST /a2a/message:send. "
            "1) get_trace_schema 2) send step types only 3) read score and fixes."
        ),
        "example": _example_call(),
        "docs": f"{root}/llms.txt",
        "use_case": LIVE_USE_CASES["UC-001"],
    }
    # Extra keys are only added when they carry new information (no copies of example/docs/hint).
    for k, v in extra.items():
        if k not in body:
            body[k] = v
    return body
_ICON_RAW = "https://raw.githubusercontent.com/TGS-checker/tracecheck-mcp/main/assets"
ICONS = [
    Icon(src=f"{_ICON_RAW}/icon.png", mime_type="image/png", sizes=["256x256"]),
    Icon(src=f"{_ICON_RAW}/icon.svg", mime_type="image/svg+xml", sizes=["any"]),
]
# Read-only, deterministic, offline scoring: safe for agents to call freely.
# Annotation hints are written with literal camelCase keys so static directory
# scanners (M8ven, OpenAI) can find them in source. The SDK accepts these aliases
# and tools/list still serializes the same four booleans.

mcp = MCPServer(
    "TraceCheck",
    title="TraceCheck",
    website_url=REPO_URL,
    icons=ICONS,
    instructions=(
        "Agent self-check and trace evaluation: call evaluate_trace after a multi-step run to get a 0-100 reliability score, "
        "issues (loop detection, error handling, missing verification) and concrete fixes, before you report success. "
        "Your agent claims success without checking that the result actually holds? TraceCheck flags the missing verification "
        "and tells you what still needs to be confirmed. No content needed: send step types only. Optional previous_rating (1-5) "
        "and previous_need_id on a later call say how useful the last check was. Free, no API key. "
        "No goal, trace or outcome text is ever logged or stored."
    ),
    version=VERSION,
)


EVALUATE_TRACE_DESCRIPTION = """\
Agent self-check and trace evaluation: after a multi-step run, get a 0-100 reliability score, the issues found (loop detection, error handling, missing verification) and concrete fixes, before you report success.

Your agent claims success without checking that the result actually holds? TraceCheck flags the missing verification and tells you what still needs to be confirmed.

No content needed: send step types only (privacy-light). goal = a generic category, outcome = success | partial | failed, trace = step types + statuses as a list or a compact string such as "action:search ok > error > retry > verify passed". Types: action, observe, error, retry, fallback, verify, final, handoff. Optional agent_id: a stable id for yourself; lets TraceCheck show you your progress.

Optional: previous_rating (1-5) and previous_need_id - how useful was your last check, now that you know whether the fix helped? 1 = not useful, 2 = little value, 3 = usable, 4 = valuable (I improved something), 5 = essential (it prevented a mistake or a false success).

WHEN TO CALL: after you finish or give up on a multi-step tool task, before reporting "done", or after a failure you want to learn from. Free, no API key. Call get_trace_schema first if you are unsure of the format.

RETURNS: score, issues (with code), suggestions (with fix_id), summary, need_id, scoring_version. Apply the fixes and evaluate again. No goal, trace or outcome text is logged or stored.
"""

_PRIVACY_LIGHT_EXAMPLE = {
    "goal": "data lookup",
    "trace": [{"type": "action", "tool": "search", "status": "ok"},
              {"type": "action", "tool": "fetch_details", "status": "error"},
              {"type": "retry", "tool": "fetch_details", "status": "ok"},
              {"type": "verify", "status": "passed"}],
    "outcome": "success",
    "agent_name": "my-agent",
}
_PRIVACY_LIGHT_COMPACT = "action:search ok > action:fetch_details error > retry:fetch_details ok > verify passed"

TRACE_SCHEMA = {
    "tool": "evaluate_trace",
    "recommended_format": "privacy_light",
    "scoring_version": SCORING_VERSION,
    "arguments": {
        "goal": "string, required. Default (privacy-light): a generic category, e.g. 'data lookup'.",
        "trace": "required. Default: step types only — a list [{type, tool?, status?, label? or target_hash?}] or compact "
                 "'action:search ok > error > retry > verify passed'. Types: action, observe, error, retry, "
                 "fallback, verify, final, handoff. Statuses: ok, partial, empty, error/failed, passed. "
                 "Optional per-step label or target_hash helps TraceCheck tell related steps apart. "
                 "Status 'partial' is for a paged or incomplete result you will continue. Content fields are optional.",
        "outcome": "string, required. Default: a status ('success', 'partial' or 'failed').",
        "agent_name": "string, optional. Display name for your agent.",
        "agent_id": "string, optional (1-64 chars). Stable id for yourself; lets TraceCheck show you your progress.",
        "previous_rating": "int 1-5, optional. How useful was your last check? 1 = not useful, 2 = little value, "
                           "3 = usable, 4 = valuable (improved something), 5 = essential (prevented an error or a false success claim).",
        "previous_need_id": "string, optional. The need_id from your last response, sent with previous_rating.",
    },
    "example_call": _PRIVACY_LIGHT_EXAMPLE,  # default, shown first: privacy-light, no content
    "example_compact_trace": _PRIVACY_LIGHT_COMPACT,
    "privacy_light_format": {
        "note": "Default and recommended. No content needed: send step types only. Nothing you send is logged or stored (only score + counts).",
        "goal": "a generic category, e.g. 'data lookup', 'booking', 'code change'",
        "trace": "list of steps {type, tool?, status?, label? or target_hash?} OR compact string 'type[:tool] [status] > ...'. "
                 "Types: action, observe, error, retry, fallback, verify, final, handoff. Statuses: ok, partial, empty, error/failed, passed. "
                 "label/target_hash (optional) helps TraceCheck tell related steps apart.",
        "outcome": "a status: 'success', 'partial' or 'failed' (optionally a category)",
        "example_call": _PRIVACY_LIGHT_EXAMPLE,
        "example_compact_trace": _PRIVACY_LIGHT_COMPACT,
    },
    "content_fields": "Optional. Prefer step types only; free-text content is not required.",
    "returned_fields": {
        "score": "int 0-100, overall reliability",
        "dimensions": "goal_clarity, observability, error_recovery, goal_alignment, efficiency (each 0-100)",
        "issues": "list of {code, text}",
        "suggestions": "list of {id (fix_id), text}",
        "summary": "one line",
        "need_id": "short id for this check; send it back as previous_need_id",
        "scoring_version": "version of the scoring rules",
    },
    "loop": "run task -> evaluate_trace -> apply suggestions -> re-run -> evaluate_trace again; compare score and dimensions.",
}


@mcp.tool(
    title="Get TraceCheck input schema",
    annotations=ToolAnnotations(
        title="Get TraceCheck input schema",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
    icons=ICONS,
    description=(
        "Return the exact input format TraceCheck's evaluate_trace expects, "
        "and a complete example call you can copy. Call this once before your first evaluate_trace. No arguments, no side effects."
    )
)
def get_trace_schema() -> dict:
    return TRACE_SCHEMA


# --- v0.3 heuristics helpers -------------------------------------------------
_ERROR_WORDS = list(SC["words"]["error"])
_NEGATED_ERRORS = list(SC["words"]["negated_errors"])
_RECOVERY_WORDS = list(SC["words"]["recovery"])
_VERIFY_WORDS = list(SC["words"]["verify"])
_SUCCESS_WORDS = list(SC["words"]["success_text"])
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
    A step is redundant if it repeats an earlier step exactly or nearly. Repeating a step right after an error
    counts as a retry, within a limit."""
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
            if union and len(tset & prev_set) / len(union) >= _TX["near_dup_similarity"] and any(not t.isdigit() for t in diff):
                dup_of = prev_key
                break
        if dup_of is not None:
            if prev_error and retry_run < _TX["max_retry_run"]:
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
    salt = os.environ.get("TRACECHECK_SALT") or os.environ.get("TRACECHECK_IP_SALT")
    if ip and salt:  # without a configured salt, IPs are never hashed or logged
        out["ip_hash"] = hashlib.sha256(f"{salt}:{ip}:{out.get('user_agent') or ''}".encode()).hexdigest()[:16]
    return out


# --- 0.5.0 agent layer: pseudonymous agent_id, need_id, progress, rating, serving knowledge ---------
from collections import OrderedDict  # noqa: E402

SERVING_FILE = Path(os.environ.get("TRACECHECK_SERVING_FILE", str(Path(__file__).with_name("data") / "serving.json")))
_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9._:@/-]{1,64}$")
_NEED_ID_RE = re.compile(r"^nd_[0-9a-f]{10}$")
RATING_SCALE = {1: "not useful", 2: "little value", 3: "usable", 4: "valuable: improved something",
                5: "essential: prevented an error or a false success claim"}
GOAL_CATEGORIES = {k: tuple(v) for k, v in SC["goal_categories"].items()}
_MEMORY_MAX = 5000
_AGENT_LAST: "OrderedDict[str, dict]" = OrderedDict()
_NEED_MEMORY: "OrderedDict[str, dict]" = OrderedDict()
_SERVING_CACHE: dict = {"mtime": None, "data": {}}


def goal_category(goal: str) -> str:
    """Map a goal onto a fixed category list. Goal text itself is never stored."""
    g = (goal or "").lower()
    for cat, words in GOAL_CATEGORIES.items():
        if any(w in g for w in words):
            return cat
    return "other"


def client_family(user_agent: str | None, client_name: str | None = None) -> str:
    """Coarse family only (e.g. 'python-httpx', 'claude-code'), never the full user agent."""
    src = (client_name or user_agent or "").strip().lower()
    if not src:
        return "unknown"
    fam = re.split(r"[/\s;(]", src, maxsplit=1)[0]
    return re.sub(r"[^a-z0-9._-]", "", fam)[:40] or "unknown"


def derive_agent_id(explicit: str | None, agent_name: str | None, client: dict) -> tuple[str, str]:
    """Stable pseudonymous key (never a raw IP). Returns (agent_id, source)."""
    if explicit:
        basis, src = f"id:{explicit}", "agent_id"
    elif agent_name and agent_name != "unknown":
        basis, src = f"name:{agent_name}|{client_family(client.get('user_agent'), client.get('client_name'))}", "agent_name"
    elif client.get("client_name"):
        basis, src = f"client:{client.get('client_name')}/{client.get('client_version') or ''}", "client_info"
    elif client.get("ip_hash"):
        basis, src = f"net:{client['ip_hash']}", "salted_network_hash"
    else:
        basis, src = f"family:{client_family(client.get('user_agent'))}", "client_family"
    return "ag_" + hashlib.sha256(basis.encode()).hexdigest()[:12], src


def _remember(store: OrderedDict, key: str, value: dict) -> None:
    store[key] = value
    store.move_to_end(key)
    while len(store) > _MEMORY_MAX:
        store.popitem(last=False)


def load_serving() -> dict:
    """Public, aggregated knowledge (fix rankings, net benchmarks). Missing or broken file -> empty."""
    try:
        mtime = (str(SERVING_FILE), SERVING_FILE.stat().st_mtime_ns, SERVING_FILE.stat().st_size)
    except OSError:
        return {}
    if _SERVING_CACHE["mtime"] != mtime:
        try:
            _SERVING_CACHE["data"] = json.loads(SERVING_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _SERVING_CACHE["data"] = {}
        _SERVING_CACHE["mtime"] = mtime
    return _SERVING_CACHE["data"] if isinstance(_SERVING_CACHE["data"], dict) else {}


def _clean_optional(agent_id, previous_rating, previous_need_id) -> tuple[dict, list]:
    """Validate optional fields. Bad values are ignored with a short note, never a rejection."""
    out, notes = {}, []
    if agent_id not in (None, ""):
        if isinstance(agent_id, str) and _AGENT_ID_RE.match(agent_id.strip()):
            out["agent_id"] = agent_id.strip()
        else:
            notes.append("agent_id ignored: use 1-64 characters from A-Z a-z 0-9 . _ : @ / -")
    if previous_rating not in (None, ""):
        try:
            r = int(str(previous_rating).strip())
        except ValueError:
            r = 0
        if 1 <= r <= 5:
            out["previous_rating"] = r
        else:
            notes.append("previous_rating ignored: send an integer 1-5")
    if previous_need_id not in (None, ""):
        if isinstance(previous_need_id, str) and _NEED_ID_RE.match(previous_need_id.strip()):
            out["previous_need_id"] = previous_need_id.strip()
        else:
            notes.append("previous_need_id ignored: send the need_id from your last response (nd_ + 10 hex)")
    return out, notes


def _confidence(n: int) -> str:
    return "high" if n >= 100 else "medium" if n >= 30 else "low"


def enrich_result(result: dict, *, goal: str, agent_id: str, optional: dict, notes: list) -> dict:
    """Add need_id, ranked fixes, net benchmark, progress and the rating request. Falls back cleanly."""
    codes = [i["code"] for i in result["issues"]]
    need_id = "nd_" + uuid.uuid4().hex[:10]
    category = goal_category(goal)
    serving = load_serving()
    fix_stats = serving.get("fixes", {}) if isinstance(serving.get("fixes"), dict) else {}
    for sug in result["suggestions"]:
        st = fix_stats.get(sug["id"])
        if isinstance(st, dict) and st.get("n"):
            sug["effectiveness"] = {"applied_n": st.get("n"), "avg_score_delta": st.get("avg_score_delta"),
                                    "confidence": _confidence(int(st.get("n") or 0))}
        else:
            sug["effectiveness"] = {"applied_n": 0, "note": "default fix, not yet measured"}
    rank = {f: (fix_stats.get(f) or {}).get("avg_score_delta") or 0 for f in (s["id"] for s in result["suggestions"])}
    result["suggestions"].sort(key=lambda s: -(rank.get(s["id"]) or 0))
    bench = (serving.get("benchmarks") or {}).get(category) if isinstance(serving.get("benchmarks"), dict) else None
    if isinstance(bench, dict) and bench.get("n"):
        b = {"goal_category": category, "median": bench.get("median"), "n": bench.get("n"),
             "confidence": _confidence(int(bench["n"]))}
        dec = bench.get("deciles")
        if isinstance(dec, list) and len(dec) == 9:
            b["your_percentile"] = 10 * sum(1 for x in dec if isinstance(x, (int, float)) and x <= result["score"])
        result["benchmark"] = b
    else:
        result["benchmark"] = {"goal_category": category, "n": 0, "note": "not enough comparable runs yet"}
    # Progress: only from this server's short-lived memory, keyed by need_id or agent_id (no stored profiles).
    prev, lost = None, False
    if optional.get("previous_need_id"):
        prev = _NEED_MEMORY.get(optional["previous_need_id"])
        lost = prev is None
    if prev is None:
        prev = _AGENT_LAST.get(agent_id)
    if prev and prev.get("agent_id") == agent_id:
        same = prev.get("category") == category
        progress = {
            "previous_need_id": prev.get("need_id"),
            "previous_score": prev["score"],
            "same_goal_category": same,
            "score_delta": (result["score"] - prev["score"]) if same else None,
            "resolved": [c for c in prev["codes"] if c not in codes],
            "still_open": [c for c in codes if c in prev["codes"]],
            "new": [c for c in codes if c not in prev["codes"]],
        }
        if not same:
            progress["not_compared"] = (f"score_delta not compared: the previous check was for goal category "
                                        f"'{prev.get('category')}', this one is '{category}'. Compare runs of the same goal type.")
        result["progress"] = progress
    elif lost:
        result["progress"] = {"note": "previous_need_id not found: checks are kept in memory for a limited time. "
                                      "Send this response's need_id next time."}
    extra_cases = [t for uc, t in LIVE_USE_CASES.items() if _UC_TRIGGER.get(uc) not in codes]
    if extra_cases:
        result["also_useful_for"] = extra_cases[0]
    result["need_id"] = need_id
    result["agent_id"] = agent_id
    result["rating_request"] = ("Optional: on your next evaluate_trace call add previous_rating (1-5) and "
                                f"previous_need_id='{need_id}'. " +
                                ", ".join(f"{k} = {v}" for k, v in RATING_SCALE.items()) + ".")
    if "previous_rating" in optional:
        result["rating_received"] = optional["previous_rating"]
    if notes:
        result["notes"] = notes
    mem = {"agent_id": agent_id, "score": result["score"], "codes": codes, "category": category, "need_id": need_id}
    _remember(_NEED_MEMORY, need_id, mem)
    _remember(_AGENT_LAST, agent_id, mem)
    return result


LIMITS = {"max_steps": 200, "max_trace_chars": 20000, "max_goal_chars": 500, "max_outcome_chars": 500,
          "max_body_bytes": 262144}
RATE_LIMIT_PER_MIN = int(os.environ.get("TRACECHECK_RATE_LIMIT_PER_MIN", "60"))
GLOBAL_LIMIT_PER_MIN = int(os.environ.get("TRACECHECK_GLOBAL_LIMIT_PER_MIN", "1200"))
_RATE: "OrderedDict[str, list]" = OrderedDict()


def check_limits(goal, trace, outcome) -> dict | None:
    """Input size limits. Too large -> a helpful error that says the limit and how to fit in it."""
    problems = []
    if isinstance(trace, list) and len(trace) > LIMITS["max_steps"]:
        problems.append(f"trace has {len(trace)} steps (max {LIMITS['max_steps']})")
    size = len(trace) if isinstance(trace, str) else len(json.dumps(trace, default=str)) if isinstance(trace, list) else 0
    if size > LIMITS["max_trace_chars"]:
        problems.append(f"trace is {size} characters (max {LIMITS['max_trace_chars']})")
    if isinstance(goal, str) and len(goal) > LIMITS["max_goal_chars"]:
        problems.append(f"goal is {len(goal)} characters (max {LIMITS['max_goal_chars']})")
    if isinstance(outcome, str) and len(outcome) > LIMITS["max_outcome_chars"]:
        problems.append(f"outcome is {len(outcome)} characters (max {LIMITS['max_outcome_chars']})")
    if not problems:
        return None
    return actionable_error("Input too large: " + "; ".join(problems) + ".",
                            hint="Send step types only (no content): one short step per tool call, a goal category "
                                 "and an outcome status. Long runs: send the last 200 steps.",
                            reason="input_too_large", limits={k: v for k, v in LIMITS.items() if k != "max_body_bytes"})


def rate_limited(key: str, now: float | None = None) -> int:
    """Sliding one-minute window per agent key, in memory only. Returns seconds to wait (0 = ok)."""
    import time
    now = now or time.monotonic()
    hits = [t for t in _RATE.get(key, []) if now - t < 60]
    if len(hits) >= RATE_LIMIT_PER_MIN:
        _RATE[key] = hits
        return max(1, int(60 - (now - hits[0])) + 1)
    hits.append(now)
    _RATE[key] = hits
    _RATE.move_to_end(key)
    while len(_RATE) > _MEMORY_MAX:
        _RATE.popitem(last=False)
    return 0


def serve_evaluation(goal, trace, outcome, *, agent_name="unknown", agent_id=None, previous_rating=None,
                     previous_need_id=None, client: dict | None = None) -> dict:
    """One path for MCP and A2A: score, enrich with knowledge, log one content-free event."""
    client = client or {}
    too_big = check_limits(goal, trace, outcome)
    if too_big:
        return too_big
    result = score_trace(goal, trace, outcome)
    if result.get("error"):
        return result
    optional, notes = _clean_optional(agent_id, previous_rating, previous_need_id)
    aid, aid_src = derive_agent_id(optional.get("agent_id"), str(agent_name or "unknown")[:80], client)
    wait = rate_limited(aid)
    if wait:
        return actionable_error(f"Too many calls from this agent. Try again in {wait} seconds.",
                                hint=f"Limit: {RATE_LIMIT_PER_MIN} evaluate calls per minute per agent. "
                                     "Batch your checks: one call per finished task is enough.",
                                reason="rate_limited", retry_after_seconds=wait)
    prev_known = aid in _AGENT_LAST
    result = enrich_result(result, goal=goal, agent_id=aid, optional=optional, notes=notes)
    record_call(trace, agent_name, result, client, extra={
        "agent_id": aid, "agent_id_source": aid_src, "goal_category": goal_category(goal),
        "previous_rating": optional.get("previous_rating"), "previous_need_id": optional.get("previous_need_id"),
        "returning_in_memory": prev_known,
    })
    return result


@mcp.tool(
    title="Evaluate agent execution trace",
    annotations=ToolAnnotations(
        title="Evaluate agent execution trace",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
    icons=ICONS,
    description=EVALUATE_TRACE_DESCRIPTION,
)
def evaluate_trace(
    goal: Annotated[str, Field(description=(
        "The original task, stated specifically, including what success looks like "
        "(e.g. 'Find the cheapest direct AMS->LIS flight on 12 March and report price and flight number'). "
        "With a privacy-light trace a generic category is enough (e.g. 'data lookup'). Never logged or stored."))] = "",
    trace: Annotated[str | list[dict], Field(description=(
        "No content needed: send step types only. Either (a) privacy-light: a list of steps like "
        "{'type':'action','tool':'search','status':'ok'}, {'type':'error'}, {'type':'retry'}, {'type':'verify','status':'passed'} "
        "or the compact string 'action:search ok > error > retry > verify passed'; or (b) free text: chronological "
        "steps, one per line, with what each tool returned, every error AND the retry/fallback, and a final "
        "'Verified: ...' line. Never logged or stored."))] = "",
    outcome: Annotated[str, Field(description=(
        "The concrete final result you claim and how it satisfies the goal; back success claims with evidence. "
        "With a privacy-light trace just send a status: 'success', 'partial' or 'failed'. Never logged or stored."))] = "",
    agent_name: Annotated[str, Field(description=(
        "Optional display name for your agent (e.g. 'my-travel-agent'). It does not affect the score."))] = "unknown",
    agent_id: Annotated[str | None, Field(description=(
        "Optional stable id for yourself (1-64 chars). Lets TraceCheck show you your progress between runs."))] = None,
    previous_rating: Annotated[int | str | None, Field(description=(
        "Optional: how useful was your last check, now that you know whether the fix helped? 1 = not useful, "
        "2 = little value, 3 = usable, 4 = valuable (improved something), 5 = essential (prevented an error "
        "or a false success claim)."))] = None,
    previous_need_id: Annotated[str | None, Field(description=(
        "Optional: the need_id from your last TraceCheck response, sent with previous_rating."))] = None,
    ctx: Context | None = None,
) -> dict:
    """MCP tool wrapper: score the trace, then log the call with client info.

    Missing fields are accepted (no protocol-level rejection). A compact privacy-light
    trace alone is scored with safe defaults; otherwise a helpful error with a
    ready-to-use example is returned so a second call can succeed.
    """
    filled = _lenient_args(goal, trace, outcome)
    if filled is None:
        err = _missing_args_error(goal, trace, outcome)
        if ctx is not None:
            raise ToolError(json.dumps(err))
        return err
    goal, trace, outcome = filled
    result = serve_evaluation(goal, trace, outcome, agent_name=agent_name, agent_id=agent_id,
                              previous_rating=previous_rating, previous_need_id=previous_need_id,
                              client=_client_from_ctx(ctx))
    if result.get("error"):
        if ctx is not None:
            raise ToolError(json.dumps(result))
        return result
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
    # 0.5.1: handing work or a decision to a person or another agent (approval request, escalation).
    "handoff": "handoff", "handover": "handoff", "hand_off": "handoff", "escalate": "handoff",
}
_ERROR_STATUSES = set(SC["statuses"]["error"])
_PASS_STATUSES = set(SC["statuses"]["pass"])
_CONTINUE_STATUSES = set(SC["statuses"]["continue"])
_FAILED_VERIFY_STATUSES = set(SC["statuses"]["failed_verify"])
_INCOMPLETE_STATUSES = set(SC["statuses"]["incomplete"])
_COMPACT_SEG = re.compile(r"^(?:\d+\s*[.)]\s*)?([a-z_]+)(?::([\w.\-/]+))?(?:\s*[:=]?\s*([\w\-]+))?$")


# --- 0.5.0 codes: the contract is the code, the sentence is the explanation --------
# Short, unambiguous English. Never describe how a code is detected (see SPEC.md).
ISSUES = {
    "goal_missing": ("The goal category is missing.", "fix_add_goal_category"),
    "goal_vague": ("The goal is too vague to evaluate the run against.", "fix_clarify_goal"),
    "too_few_steps": ("The trace has too few distinct steps to judge the process.", "fix_add_steps"),
    "missing_step_status": ("Most action steps have no status, so their results are not observable.", "fix_add_step_status"),
    "missing_observations": ("The trace does not record what tool calls actually returned.", "fix_record_observations"),
    "trace_too_short": ("The trace is too short to assess completeness.", "fix_expand_trace"),
    "loop_detected": ("The same call was repeated without new progress.", "fix_break_loop"),
    "redundant_step": ("A step was repeated once without new progress.", "fix_remove_redundant_step"),
    "unrecovered_error": ("One or more errors were not followed by a recovery or a verification.", "fix_recover_error"),
    "outcome_missing": ("The outcome status is missing.", "fix_add_outcome_status"),
    "outcome_vague": ("The outcome is missing or too vague to link back to the goal.", "fix_clarify_outcome"),
    "ungrounded_success": ("The outcome claims success, but the steps show unresolved errors or a failed verification.",
                           "fix_report_honest_outcome"),
    "missing_verification": ("No passed verification against the goal is visible.", "fix_add_verification"),
    "conclusion_on_incomplete_data": ("Success was claimed after a step still returned partial or empty data.",
                                      "fix_report_incomplete"),
    "requirement_lowered": ("A fallback succeeded only by dropping or replacing a requirement of the goal.",
                            "fix_restore_or_confirm_requirement"),
    "verify_kind_mismatch": ("A verification is present, but its kind does not fit the goal.", "fix_choose_verify_kind"),
    # 0.5.1: order and open ends
    "handoff_before_verification": ("A hand-off, approval request or final report came before the result was verified.",
                                    "fix_verify_before_handoff"),
    "unresolved_partial_observation": ("Success was claimed while an observation with partial or empty data was never resolved.",
                                       "fix_resolve_open_observation"),
    "write_before_complete_data": ("Results were written while the data they depend on was still incomplete.",
                                   "fix_wait_for_complete_data"),
    "partial_outcome_without_handover": ("The outcome is partial, but no hand-over or scheduling of the remaining work is visible.",
                                         "fix_hand_over_remaining_work"),
}
FIXES = {
    "fix_add_goal_category": "Send a generic goal category, such as 'data lookup', 'booking' or 'code change'.",
    "fix_clarify_goal": "Restate the goal so success, constraints and the expected output are clear.",
    "fix_add_steps": "Send one step per tool call, and include error, retry or verify steps when they happened.",
    "fix_add_step_status": "Add a status to every action step, for example {'type':'action','tool':'search','status':'ok'}.",
    "fix_record_observations": "After each tool call or action, record what was returned or observed.",
    "fix_expand_trace": "Add the intermediate steps and results, not only the final claim.",
    "fix_break_loop": "Before repeating a call, reuse the earlier result or change the tool or parameters. "
                      "If you are done, stop and report the current result.",
    "fix_remove_redundant_step": "Reuse the earlier result instead of repeating the same call.",
    "fix_recover_error": "When a step fails, add a retry or fallback (with its status) before you continue. "
                         "Do not rely on a failed result in later steps.",
    "fix_add_outcome_status": "Send an outcome status: success, partial or failed.",
    "fix_clarify_outcome": "Describe the concrete result and state how it satisfies the goal.",
    "fix_report_honest_outcome": "Only report success when that check passed; otherwise report partial or failed and state what is still open.",
    "fix_add_verification": "Record that check as a verify step, for example {'type':'verify','status':'passed'}, before you report success.",
    "fix_specify_verification": "Keep your verify step, and state which result you confirmed against the goal.",
    "fix_report_incomplete": "Until that check passes on complete data, report partial or failed, even when a retry returned some data.",
    "fix_restore_or_confirm_requirement": "Restore the original requirement, or get confirmation that the lower option is acceptable, "
                                          "and say which requirement changed.",
    "fix_choose_verify_kind": "Use a verification kind that fits this goal instead of another generic verify step, "
                              "and state what you confirmed.",
    "fix_verify_before_handoff": "Do this before you hand off, ask for approval or report, or say in the hand-off which check is still open.",
    "fix_resolve_open_observation": "Resolve the partial or empty data point first (re-check it or get the missing value), "
                                    "or report partial and name the open point.",
    "fix_wait_for_complete_data": "Write results only after the data is complete; if you wrote early, re-check or rewrite them once the data is in.",
    "fix_hand_over_remaining_work": "Name the remaining work and hand it over or schedule it (a handoff step), so it is not lost.",
}


def _flag(issues: list, fixes: list, code: str, fix_id: str | None = None, **attrs) -> None:
    """Record one issue by code plus its fix_id (deduplicated). Attributes such as stakes ride along."""
    if code not in ISSUES:
        raise KeyError(f"unknown issue code {code}")
    if not any(i["code"] == code for i in issues):
        issues.append({"code": code, **attrs})
    fid = fix_id or ISSUES[code][1]
    if fid not in fixes:
        fixes.append(fid)


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
        evidence = st.get("label") or st.get("target_hash") or st.get("targetHash")
        evidence = str(evidence).strip()[:80] if evidence else None
        step = {"type": typ, "tool": str(tool).strip().lower()[:60] if tool else None,
                "status": str(status).strip().lower().replace(" ", "_")[:30] if status is not None else None,
                "label": evidence or None}
        stakes = str(st.get("stakes") or "").strip().lower()
        if stakes in ("low", "normal", "high"):
            step["stakes"] = stakes
        vk = str(st.get("verify_kind") or st.get("verifyKind") or "").strip().lower()
        if vk in VERIFY_KINDS:
            step["verify_kind"] = vk
        if st.get("requirement_lowered") is True or str(st.get("requirement_lowered")).lower() == "true":
            step["requirement_lowered"] = True
        out.append(step)
    return out


def _is_error_step(st: dict) -> bool:
    if st["type"] in ("final", "handoff"):  # 'final failed' reports the outcome; it is not a new error to recover from
        return False
    return st["type"] == "error" or (st["status"] in _ERROR_STATUSES)


def _count_identical_repeats(steps: list) -> tuple[int, dict]:
    """Return (redundant_count, counts_by_tool).

    In privacy-light mode, different successful shell/tool calls cannot be told
    apart, so same-tool same-status *ok* steps are not flagged without evidence.
    Evidence is an optional per-step `label` or `target_hash`: when both sides
    have the same value and status, that is a real repeat. Identical failing
    calls (same tool, error status, no new progress) still count as a loop
    even without a label. Pagination (status 'partial') is never a loop.
    Observes, status changes, verify/final, or a different tool are progress.
    """
    last_status: dict = {}
    last_label: dict = {}
    progress_since: dict = {}
    redundant = 0
    counts: dict = {}
    for st in steps:
        typ, tool, status = st["type"], st["tool"], st.get("status")
        label = st.get("label")
        if typ == "observe":
            if tool and tool in last_status:
                progress_since[tool] = True
            else:
                for t in last_status:
                    progress_since[t] = True
            continue
        if typ in ("verify", "final", "handoff"):
            for t in last_status:
                progress_since[t] = True
            continue
        if typ in ("action", "retry", "fallback") and tool:
            for t in last_status:
                if t != tool:
                    progress_since[t] = True
            status_norm = status or "ok"
            prev = last_status.get(tool)
            prev_label = last_label.get(tool)
            paged = prev in _CONTINUE_STATUSES and status_norm in _CONTINUE_STATUSES
            same = prev is not None and prev == status_norm and not progress_since.get(tool, False) and not paged
            if same:
                evidenced = bool(prev_label and label and prev_label == label)
                unlabeled_error = (not prev_label and not label and status_norm in _ERROR_STATUSES)
                if evidenced or unlabeled_error:
                    redundant += 1
                    counts[tool] = counts.get(tool, 1) + 1
            last_status[tool] = status_norm
            last_label[tool] = label
            progress_since[tool] = False
        # A bare 'error' step restates a failure; it is not progress.
    return redundant, counts


VERIFY_KINDS = ("api", "script", "visual", "human", "end_to_end", "test")
GOAL_VERIFY_FIT = {k: set(v) for k, v in SC["verify_fit"].items()}
WHAT_TO_VERIFY = {
    "code_change": "Run the tests or an end-to-end check on the changed code.",
    "data_lookup": "Confirm the returned data is complete and matches the query.",
    "booking": "Confirm the booking exists with the requested date and details.",
    "purchase": "Confirm the payment or order status with the provider.",
    "messaging": "Confirm the message reached the right recipient.",
    "file_ops": "Open the resulting file and confirm its content and format.",
    "content": "Open the produced output and confirm it matches the requested content and format.",
    "research": "Check the key claims against their sources.",
    "scheduled_work": "Confirm each scheduled item ran, and list which items are still open or deferred.",
    "media": "Play or open the rendered output and check timing, audio and format against the brief.",
    "other": "Name the concrete result the goal asked for and confirm it exists and is complete.",
}
# Fixes about the result itself start with the goal-specific check (what_to_verify), never with a generic step.
GOAL_FIXES = ("fix_add_verification", "fix_specify_verification", "fix_choose_verify_kind", "fix_report_honest_outcome",
              "fix_report_incomplete", "fix_restore_or_confirm_requirement", "fix_verify_before_handoff",
              "fix_resolve_open_observation", "fix_wait_for_complete_data", "fix_hand_over_remaining_work")


def _incomplete_before_claim(steps: list) -> bool:
    """True when a tool's last result is still partial or empty and no later verification passed."""
    last: dict = {}
    for idx, st in enumerate(steps):
        if st["type"] in ("action", "retry", "fallback") and st["tool"]:
            last[st["tool"]] = (st["status"] or "ok", idx)
    for status, idx in last.values():
        if status in _INCOMPLETE_STATUSES:
            later = steps[idx + 1:]
            if not any(s["type"] == "verify" and s["status"] in _PASS_STATUSES for s in later):
                return True
    return False


def _tool_has(st: dict, key: str) -> bool:
    return bool(st.get("tool")) and any(w in st["tool"] for w in SC["words"][key])


def _is_handoff(st: dict) -> bool:
    return st["type"] == "handoff" or (st["type"] in ("action", "final") and _tool_has(st, "handoff_tools"))


def _outcome_kind(outcome_l: str, claims_success: bool) -> str:
    if claims_success:
        return "success"
    if any(w in outcome_l for w in SC["words"]["partial_outcome"]):
        return "partial"
    if any(w in outcome_l for w in SC["words"]["failed_outcome"]):
        return "failed"
    return "other"


def _order_and_open_ends(steps: list, outcome_kind: str) -> list:
    """0.5.1 order and open-end issues on a privacy-light trace. Returns issue codes in a fixed order."""
    found = []
    ok = lambda st: (st.get("status") or "ok") in _PASS_STATUSES  # noqa: E731
    passed_at = [i for i, st in enumerate(steps) if st["type"] == "verify" and (st["status"] in _PASS_STATUSES or st["status"] is None)]
    verify_at = [i for i, st in enumerate(steps) if st["type"] == "verify"]
    if outcome_kind != "failed":
        early_handoff = any(_is_handoff(st) and (not passed_at or i < passed_at[0]) for i, st in enumerate(steps))
        early_final = bool(verify_at) and any(st["type"] == "final" and i < verify_at[-1] for i, st in enumerate(steps))
        if early_handoff or early_final:
            found.append("handoff_before_verification")
    if outcome_kind == "success":
        for i, st in enumerate(steps):
            if st["type"] == "observe" and st["status"] in _INCOMPLETE_STATUSES:
                later = steps[i + 1:]
                if not any((s2["type"] == "observe" and s2["status"] in _PASS_STATUSES)
                           or (s2["type"] == "verify" and s2["status"] in _PASS_STATUSES) for s2 in later):
                    found.append("unresolved_partial_observation")
                    break
    open_data: set = set()
    for st in steps:
        typ, tool, status = st["type"], st.get("tool"), st.get("status")
        if typ in ("action", "retry", "fallback") and tool and _tool_has(st, "write_tools") and open_data - {tool}:
            found.append("write_before_complete_data")
            break
        if typ == "observe":
            if status in _INCOMPLETE_STATUSES:
                open_data.add("<observe>")
            elif status in _PASS_STATUSES:
                open_data.discard("<observe>")
        elif typ == "verify" and status in _PASS_STATUSES:
            open_data.clear()
        elif typ in ("action", "retry", "fallback") and tool:
            if status in _INCOMPLETE_STATUSES:
                open_data.add(tool)
            elif ok(st):
                open_data.discard(tool)
                if typ == "fallback":
                    open_data -= {t for t in open_data if t != "<observe>"}
    if outcome_kind == "partial" and not any(_is_handoff(st) or _tool_has(st, "handover_tools") for st in steps):
        found.append("partial_outcome_without_handover")
    return found


def score_steps(goal: str, steps: list, outcome: str) -> dict:
    """Score a privacy-light trace (step types + statuses only). Same dimensions/weights/caps as score_trace."""
    issues, suggestions, dimensions, caps = [], [], {}, []
    goal_l, outcome_l = (goal or "").lower().strip(), (outcome or "").lower().strip()
    n = len(steps)

    # 1. Goal clarity: a generic category is enough here (content is intentionally absent)
    category = goal_category(goal_l)
    if len(goal_l) >= _ST["goal_min_len"]:
        dimensions["goal_clarity"] = (_ST["goal_base"] + (_ST["goal_category_bonus"] if category != "other" else 0)
                                      + (_ST["goal_two_words_bonus"] if len(goal_l.split()) >= 2 else 0))
    else:
        dimensions["goal_clarity"] = _ST["goal_missing_score"]
        _flag(issues, suggestions, "goal_missing")

    # 2. Efficiency: see _count_identical_repeats (ok repeats need a label/target_hash).
    redundant, counts = _count_identical_repeats(steps)
    distinct = n - redundant

    # 3. Observability: enough distinct steps, and a status recorded on actions
    obs = _ST["obs_base"] + _ST["obs_per_step"] * min(distinct, _ST["obs_step_cap"])
    if distinct < _ST["obs_min_steps"]:
        obs = _ST["obs_few_score"]
        _flag(issues, suggestions, "too_few_steps")
    if any(s["type"] == "observe" for s in steps):
        obs += _ST["obs_observe_bonus"]
    elif distinct <= _ST["obs_bare_max_steps"]:
        obs -= _ST["obs_no_observe_penalty"]  # a bare trace without any observation says little
    actions = [s for s in steps if s["type"] == "action"]
    with_status = sum(1 for s in actions if s["status"]) + sum(1 for s in steps if s["type"] == "observe")
    if actions and with_status >= _ST["obs_status_share"] * len(actions):
        obs += _ST["obs_status_bonus"]
    else:
        _flag(issues, suggestions, "missing_step_status")
    dimensions["observability"] = max(0, min(100, obs))

    eff = _EFF["full"] if distinct >= _EFF["min_steps"] else _EFF["few_steps_score"]
    eff -= min(_EFF["max_error_penalty"], _EFF["per_error"] * sum(1 for s in steps if _is_error_step(s)))
    if redundant:
        eff -= min(_EFF["max_redundant_penalty"], _EFF["per_redundant"] * redundant)
        if redundant >= _EFF["loop_min_redundant"] or redundant / n >= _EFF["loop_min_ratio"]:
            _flag(issues, suggestions, "loop_detected")
        else:
            _flag(issues, suggestions, "redundant_step")
        if redundant >= _CAPS["loop_min_redundant"] and redundant / n >= _CAPS["loop_min_ratio"]:
            caps.append((_CAPS["loop"], "most of the trace is a repeat loop"))
    dimensions["efficiency"] = max(0, eff)

    # 4. Error recovery: each error must be followed by retry/fallback, a successful action, or a passed verify
    # (a later successful action only resolves errors of that same tool; moving on to another tool does not).
    # 0.5.1: an empty/incomplete result is a gap; a retry or fallback should close it. No errors and no gaps:
    # error recovery is not applicable and does not count in the overall score.
    errors, pending, last_tool, lowered = 0, [], None, False
    gaps: dict = {}  # tool -> "open" | "retried" | "closed"
    for st in steps:
        typ, status = st["type"], st["status"] or "ok"
        if _is_error_step(st):
            errors += 1
            pending.append((st["tool"] or last_tool, st.get("stakes", "normal")))
        elif st.get("requirement_lowered"):
            lowered = True  # a fallback that drops a requirement is not a recovery
            pending = [p for p in pending if p[0] != st["tool"]] if st["tool"] else pending[:-1]
        elif typ in ("retry", "fallback") or (typ == "verify" and st["status"] in _PASS_STATUSES):
            pending = []
        elif typ in ("action", "observe") and st["tool"]:
            pending = [p for p in pending if p[0] != st["tool"]]
        if typ in ("action", "retry", "fallback") and st["tool"]:
            t = st["tool"]
            incomplete = status in _INCOMPLETE_STATUSES and (status not in _CONTINUE_STATUSES or typ != "action")
            if incomplete:
                gaps[t] = "retried" if (typ in ("retry", "fallback") and t in gaps) else gaps.get(t, "open")
            elif t in gaps and gaps[t] != "closed" and status in _PASS_STATUSES:
                gaps[t] = "closed"
            if typ == "fallback" and status in _PASS_STATUSES and not st.get("requirement_lowered"):
                for g in gaps:
                    if gaps[g] != "closed":
                        gaps[g] = "closed"
        if st["tool"]:
            last_tool = st["tool"]
    unresolved = len(pending)
    high_stakes = any(p[1] == "high" for p in pending)
    unclosed = sum(1 for v in gaps.values() if v == "retried")
    closed = sum(1 for v in gaps.values() if v == "closed")
    not_applicable = []
    err = _ST["err_base"]
    if not errors and not gaps and not lowered:
        err = _ST["err_none"]
        not_applicable.append("error_recovery")
    elif unresolved:
        err -= _ST["err_unresolved_penalty"] + _ST["err_unresolved_extra"] * (unresolved - 1)
        if high_stakes:
            err -= _ST["err_high_stakes_penalty"]
        elif all(p[1] == "low" for p in pending):
            err += _ST["err_low_stakes_bonus"]
        _flag(issues, suggestions, "unrecovered_error", **({"stakes": "high"} if high_stakes else {}))
    elif (errors or closed) and not lowered and not unclosed:
        err += _ST["err_recovered_bonus"] + (_ST["err_gap_closed_bonus"] if closed else 0)
    if unclosed:
        err -= _ST["err_gap_unclosed_penalty"] * unclosed  # a retry that did not close the gap
    if lowered:
        err -= _ST["err_lowered_penalty"]
        _flag(issues, suggestions, "requirement_lowered")
    dimensions["error_recovery"] = max(0, min(100, err))

    # 5. Alignment: outcome status vs. what the steps show
    align = _ST["align_base"]
    verifies = [s for s in steps if s["type"] == "verify"]
    passed = any(s["status"] in _PASS_STATUSES or s["status"] is None for s in verifies)
    failed_verify = any(s["status"] in _ERROR_STATUSES or s["status"] in _FAILED_VERIFY_STATUSES for s in verifies)
    claims_success = any(w in outcome_l for w in SC["words"]["success_outcome"]) \
        and not any(w in outcome_l for w in SC["words"]["outcome_negation"])
    if not outcome_l:
        align -= _ST["align_outcome_missing_penalty"]
        _flag(issues, suggestions, "outcome_missing")
    else:
        align += _ST["align_outcome_present_bonus"]
    incomplete = _incomplete_before_claim(steps)
    if claims_success and (unresolved or (failed_verify and not passed)):
        align -= _ST["align_ungrounded_penalty"] + (_ST["align_ungrounded_high_stakes_extra"] if high_stakes else 0)
        _flag(issues, suggestions, "ungrounded_success")
        caps.append((_CAPS["ungrounded_success"], "success claimed over unresolved errors"))
        if high_stakes:
            caps.append((_CAPS["ungrounded_high_stakes"], "success claimed over an unresolved high-stakes failure"))
    elif claims_success and incomplete:
        align -= _ST["align_incomplete_penalty"]
        _flag(issues, suggestions, "conclusion_on_incomplete_data")
        caps.append((_CAPS["incomplete_data"], "success claimed on incomplete data"))
    elif claims_success:
        align += _ST["align_success_bonus"]
    if claims_success and lowered:
        align -= _ST["align_lowered_success_penalty"]
    what = WHAT_TO_VERIFY.get(category, WHAT_TO_VERIFY["other"])
    order_codes = _order_and_open_ends(steps, _outcome_kind(outcome_l, claims_success))
    kinds = [s.get("verify_kind") for s in verifies if s.get("verify_kind")]
    fits = GOAL_VERIFY_FIT.get(category)
    if passed and kinds and fits and not any(k in fits for k in kinds):
        align += _ST["align_kind_mismatch_bonus"]  # a verification of a kind that does not fit counts for less
        _flag(issues, suggestions, "verify_kind_mismatch", what_to_verify=what)
        if "fix_specify_verification" not in suggestions:
            suggestions.append("fix_specify_verification")
    elif passed:
        align += _ST["align_verify_passed_bonus"]
    elif verifies:
        align -= _ST["align_verify_failed_penalty"]
        _flag(issues, suggestions, "missing_verification", fix_id="fix_specify_verification", what_to_verify=what)
    else:
        align -= _ST["align_no_verify_penalty"]
        _flag(issues, suggestions, "missing_verification", what_to_verify=what)
    align -= _apply_order_codes(order_codes, issues, suggestions, caps, what, _ST)
    dimensions["goal_alignment"] = max(0, min(100, align))
    return _finish(dimensions, issues, suggestions, caps, privacy_mode=True, category=category,
                   not_applicable=not_applicable)


_ORDER_PENALTY_KEYS = {"handoff_before_verification": "align_handoff_penalty",
                       "unresolved_partial_observation": "align_open_observation_penalty",
                       "write_before_complete_data": "align_write_early_penalty",
                       "partial_outcome_without_handover": "align_partial_no_handover_penalty"}
_ORDER_CAP_KEYS = {"handoff_before_verification": "handoff_before_verification",
                   "unresolved_partial_observation": "open_observation"}


def _apply_order_codes(codes: list, issues: list, suggestions: list, caps: list, what: str, table: dict) -> int:
    """Flag 0.5.1 order/open-end codes; return the goal-alignment penalty."""
    penalty = 0
    for code in codes:
        _flag(issues, suggestions, code, what_to_verify=what)
        penalty += table[_ORDER_PENALTY_KEYS[code]]
        if code in _ORDER_CAP_KEYS:
            caps.append((_CAPS[_ORDER_CAP_KEYS[code]], code))
    return penalty


def score_trace(goal: str, trace, outcome: str) -> dict:
    """Dispatch: privacy-light step format -> score_steps, free text -> heuristic text scoring."""
    if not isinstance(goal, str) or not isinstance(outcome, str) or not isinstance(trace, (str, list)):
        return actionable_error(
            "Malformed input: goal and outcome must be strings, "
            "trace must be a string or a list of steps.",
            hint="Call get_trace_schema, then send step types only as in example.",
            reason="malformed_input",
        )
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
    goal_score = _TX["goal_base"]
    if len(goal.strip()) < _TX["goal_min_len"]:
        goal_score -= _TX["goal_vague_penalty"]
        _flag(issues, suggestions, "goal_vague")
    if not any(w in goal_l for w in SC["words"]["goal_verbs"]):
        goal_score -= _TX["goal_no_verb_penalty"]
    dimensions["goal_clarity"] = max(0, goal_score)

    # 2. Efficiency: loops / redundant steps (new in v0.3)
    redundant, top_step, top_count = _find_redundant(lines)

    # 3. Trace structure & observability (redundant steps don't count as distinct steps)
    step_like = sum(1 for l in lines if any(l.lower().startswith(p) for p in SC["words"]["step_prefixes"]))
    step_count = max(len(lines), step_like, trace.count(". ") + 1) - redundant
    obs_score = _TX["obs_base"]
    if step_count < _TX["obs_few_steps"]:
        obs_score -= _TX["obs_few_penalty"]
        _flag(issues, suggestions, "too_few_steps")
    if step_count >= _TX["obs_many_steps"]:
        obs_score += _TX["obs_many_bonus"]
    if any(w in trace_l for w in SC["words"]["observation"]):
        obs_score += _TX["obs_observed_bonus"]
    else:
        _flag(issues, suggestions, "missing_observations")
    if len(trace) < _TX["short_trace_chars"]:
        obs_score -= _TX["short_trace_penalty"]
        _flag(issues, suggestions, "trace_too_short")
    dimensions["observability"] = max(0, min(100, obs_score))

    eff_score = _EFF["full"] if step_count >= _EFF["min_steps"] else _EFF["few_steps_score"]
    if redundant:
        eff_score -= min(_EFF["max_redundant_penalty"], _EFF["per_redundant"] * redundant)
        ratio = redundant / max(1, len(lines))
        if redundant >= _EFF["loop_min_redundant"] or ratio >= _EFF["loop_min_ratio"]:
            _flag(issues, suggestions, "loop_detected")
        else:
            _flag(issues, suggestions, "redundant_step")
        if redundant >= _CAPS["loop_min_redundant"] and ratio >= _CAPS["loop_min_ratio"]:
            caps.append((_CAPS["loop"], "most of the trace is a repeat loop"))
    dimensions["efficiency"] = max(0, eff_score)

    # 4. Error handling & recovery (per-error: is it followed by a retry/fix/verification?)
    error_count, unresolved = _unresolved_errors(lines)
    words = SC["words"]
    # 0.5.1: a retry or fallback that still reports incomplete data did not close the gap.
    unclosed = sum(1 for l in lines if _has(l.lower(), _RECOVERY_WORDS) and _has(l.lower(), words["incomplete_text"]))
    not_applicable = []
    err_score = _TX["err_base"]
    if not error_count and not unclosed:
        err_score = _TX["err_none"]
        not_applicable.append("error_recovery")
    elif unresolved:
        err_score -= _TX["err_unresolved_penalty"] + _TX["err_unresolved_extra"] * (unresolved - 1)
        _flag(issues, suggestions, "unrecovered_error")
    elif error_count and not unclosed:
        err_score += _TX["err_recovered_bonus"]
    err_score -= _TX["err_gap_unclosed_penalty"] * unclosed
    dimensions["error_recovery"] = max(0, min(100, err_score))

    # 5. Goal–outcome alignment: evidence over claims
    align_score = _TX["align_base"]
    goal_words = set(w for w in goal_l.split() if len(w) > _TX["align_word_len"])
    outcome_words = set(w for w in outcome_l.split() if len(w) > _TX["align_word_len"])
    if len(goal_words & outcome_words) >= _TX["align_overlap_min"]:
        align_score += _TX["align_overlap_bonus"]
    claims_success = any(w in outcome_l for w in _SUCCESS_WORDS)
    if claims_success and unresolved:
        align_score -= _TX["align_ungrounded_penalty"]
        _flag(issues, suggestions, "ungrounded_success")
        caps.append((_CAPS["ungrounded_success"], "success claimed over unresolved errors"))
    elif claims_success:
        align_score += _TX["align_success_bonus"]
    if not outcome or len(outcome.strip()) < _TX["outcome_min_len"]:
        align_score -= _TX["outcome_vague_penalty"]
        _flag(issues, suggestions, "outcome_vague")
    category = goal_category(goal_l)
    what = WHAT_TO_VERIFY.get(category, WHAT_TO_VERIFY["other"])
    if any(w in trace_l for w in _VERIFY_WORDS):
        align_score += _TX["align_verify_bonus"]
    else:
        align_score -= _TX["align_no_verify_penalty"]
        _flag(issues, suggestions, "missing_verification", what_to_verify=what)
    kind = ("partial" if _has(outcome_l, words["partial_outcome"]) else "success" if claims_success
            else "failed" if _has(outcome_l, words["failed_outcome"]) else "other")
    align_score -= _apply_order_codes(_text_order_and_open_ends(lines, kind), issues, suggestions, caps, what, _TX)
    dimensions["goal_alignment"] = max(0, min(100, align_score))

    return _finish(dimensions, issues, suggestions, caps, privacy_mode=False, category=category,
                   not_applicable=not_applicable)


def _text_order_and_open_ends(lines: list, outcome_kind: str) -> list:
    """0.5.1 order and open-end issues on a free-text trace (same codes as the step format)."""
    w = SC["words"]
    low = [l.lower() for l in lines]
    verify_at = [i for i, l in enumerate(low) if _has(l, _VERIFY_WORDS)]
    found = []
    if outcome_kind != "failed":
        early_handoff = any(_has(l, w["handoff_text"]) and (not verify_at or i < verify_at[0]) for i, l in enumerate(low))
        early_final = bool(verify_at) and any(_has(l, w["final_text"]) and i < verify_at[-1] for i, l in enumerate(low))
        if early_handoff or early_final:
            found.append("handoff_before_verification")
    if outcome_kind == "success":
        for i, l in enumerate(low):
            if _has(l, w["observation"]) and _has(l, w["incomplete_text"]):
                if not any(_has(l2, _VERIFY_WORDS) or _has(l2, w["resolution_text"]) for l2 in low[i + 1:]):
                    found.append("unresolved_partial_observation")
                    break
    open_data = False
    for l in low:
        if _has(l, w["write_text"]) and open_data:
            found.append("write_before_complete_data")
            break
        if _has(l, w["incomplete_text"]):
            open_data = True
        elif _has(l, w["resolution_text"]) or _has(l, _VERIFY_WORDS):
            open_data = False
    if outcome_kind == "partial" and not any(_has(l, w["handover_text"]) or _has(l, w["handoff_text"]) for l in low):
        found.append("partial_outcome_without_handover")
    return found


def _finish(dimensions: dict, issues: list, suggestions: list, caps: list, privacy_mode: bool,
            category: str = "other", not_applicable: list | None = None) -> dict:
    # 6. Overall score (weighted over the applicable dimensions), then critical-flaw caps
    not_applicable = not_applicable or []
    weights = {k: v for k, v in _W.items() if k not in not_applicable}
    total = sum(weights.values()) or 1.0
    score = int(max(0, min(100, round(sum(dimensions[k] * weights[k] for k in weights) / total))))
    for cap, _reason in caps:
        score = min(score, cap)

    issues_out = [{"code": i["code"], "text": ISSUES[i["code"]][0],
                   **{k: v for k, v in i.items() if k != "code"}} for i in issues[:5]]
    what = WHAT_TO_VERIFY.get(category, WHAT_TO_VERIFY["other"])
    # Result fixes start with what to check for this goal type, then the general fix.
    suggestions_out = [{"id": f, "text": (f"{what} {FIXES[f]}" if f in GOAL_FIXES else FIXES[f]),
                        **({"what_to_verify": what} if f in GOAL_FIXES else {})}
                       for f in suggestions[:5]]
    summary = (
        f"TraceCheck score: {score}/100. "
        f"Dimensions: observability {dimensions['observability']}, "
        f"error recovery {dimensions['error_recovery']}, "
        f"goal alignment {dimensions['goal_alignment']}, "
        f"efficiency {dimensions['efficiency']}. "
        + (f"{len(issues)} issue(s): {', '.join(i['code'] for i in issues)}." if issues else "No issues found.")
    )
    return {
        "score": score,
        "issues": issues_out,
        "suggestions": suggestions_out,
        "issues_truncated": len(issues) > 5,
        "summary": summary,
        "dimensions": dimensions,
        **({"not_applicable": not_applicable} if not_applicable else {}),
        "privacy_mode": privacy_mode,
        "version": VERSION,
        "scoring_version": SCORING_VERSION,
    }


_LOG_CLIENT_FIELDS = ("client_name", "client_version", "transport", "user_agent", "ip_hash")
EVENT_SCHEMA_VERSION = 1
_OWN_TEST_MARKERS = ("tracecheck-selftest", "tracecheck-smoke", "pod-fixture")
_REVIEWER_MARKERS = ("pod-geldverdiener",)
_CHECKER_MARKERS = ("grid-station-review", "sealkeeper")
_CRAWLER_MARKERS = ("bot", "crawler", "spider", "scanner", "glama", "m8ven")


def agent_kind(agent_name: str | None, user_agent: str | None) -> str:
    n, ua = (agent_name or "").lower(), (user_agent or "").lower()
    if any(m in n or m in ua for m in _OWN_TEST_MARKERS):
        return "own_test"
    if any(m in n for m in _REVIEWER_MARKERS):
        return "reviewer"
    if any(m in n or m in ua for m in _CHECKER_MARKERS):
        return "checker"
    if any(m in ua for m in _CRAWLER_MARKERS) and not n:
        return "crawler"
    return "agent" if (n and n != "unknown") or ua else "unknown"


def _step_shape(trace) -> dict:
    """Type-level counts only; no content, no tool names."""
    steps = _parse_steps(trace)
    if steps is None:
        return {"arg_shape": "free_text"}
    types = [s["type"] for s in steps]
    return {"arg_shape": "privacy_light", "step_count": len(types),
            "error_count": sum(1 for s in steps if _is_error_step(s)),
            "retry_count": types.count("retry") + types.count("fallback"),
            "has_verify": "verify" in types,
            "step_pattern": ">".join(t[:2] for t in types[:20])}


def record_call(trace, agent_name: str, result: dict, client: dict | None = None, extra: dict | None = None) -> None:
    """Log one call event in the shared data-plan schema: JSONL file + one `TRACECHECK_CALL {json}` line.
    Content-free by design: no goal, trace or outcome text, no tool names, no raw IP."""
    client = client or {}
    extra = extra or {}
    progress = result.get("progress") or {}
    entry = {
        "event_id": uuid.uuid4().hex[:16],
        "event_type": "call",
        "ts_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "timestamp": None,
        "schema_version": EVENT_SCHEMA_VERSION,
        "server_version": VERSION,
        "version": VERSION,
        "scoring_version": SCORING_VERSION,
        "agent_id": extra.get("agent_id"),
        "agent_id_source": extra.get("agent_id_source"),
        "agent_kind": agent_kind(agent_name, client.get("user_agent")),
        "agent_name": str(agent_name)[:80],
        "client_family": client_family(client.get("user_agent"), client.get("client_name")),
        "goal_category": extra.get("goal_category"),
        **_step_shape(trace),
        "score": result["score"],
        "dimensions": result["dimensions"],
        "issue_count": len(result["issues"]),
        "issue_codes": [i["code"] for i in result["issues"]],
        "fix_ids": [f["id"] for f in result["suggestions"]],
        "need_id": result.get("need_id"),
        "knowledge_match": any((f.get("effectiveness") or {}).get("applied_n") for f in result["suggestions"]),
        "benchmark_given": bool((result.get("benchmark") or {}).get("n")),
        "prev_score_same_agent_need": progress.get("previous_score"),
        "score_delta": progress.get("score_delta"),
        "issues_resolved": progress.get("resolved"),
        "explicit_feedback": extra.get("previous_rating"),
        "previous_need_id": extra.get("previous_need_id"),
        "returning": extra.get("returning_in_memory"),
        "trace_length": len(trace),  # characters for free text, number of steps for a list
        "privacy_mode": bool(result.get("privacy_mode")),
        **{k: client.get(k) for k in _LOG_CLIENT_FIELDS if client.get(k) is not None},
    }
    entry["timestamp"] = entry["ts_utc"]
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.warning(f"could not write call log: {e}")
    print("TRACECHECK_CALL " + json.dumps(entry, ensure_ascii=False), file=_CALL_STREAM, flush=True)
    logger.info(f"Evaluated trace: agent_id={entry['agent_id']} score={result['score']}")



def log_miss(reason: str, *, path: str | None = None, method: str | None = None, tool: str | None = None,
             user_agent: str | None = None) -> None:
    """One content-free TRACECHECK_MISS line: what an agent looked for and did not find (feeds alias suggestions)."""
    entry = {"event_type": "miss", "ts_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
             "server_version": VERSION, "scoring_version": SCORING_VERSION, "reason": reason,
             "path": path, "method": method, "tool": tool, "client_family": client_family(user_agent)}
    print("TRACECHECK_MISS " + json.dumps({k: v for k, v in entry.items() if v is not None}), file=_CALL_STREAM, flush=True)


def _did_you_mean(name: str, known: list[str]) -> str | None:
    import difflib
    hits = difflib.get_close_matches(name or "", known, n=1, cutoff=0.5)
    return hits[0] if hits else None


def _install_unknown_tool_hint() -> None:
    """Turn an unknown tools/call into a short hint listing valid tools (and a did-you-mean)."""
    original = mcp.call_tool

    async def call_tool(name: str, arguments: dict, context=None):
        try:
            return await original(name, arguments, context)
        except ToolError as exc:
            if "Unknown tool" not in str(exc):
                raise
            known = [info.name for info in mcp._tool_manager.list_tools()]
            suggestion = _did_you_mean(name, known)
            hint = "Valid tools: " + ", ".join(known) + "."
            if suggestion:
                hint += f" Did you mean {suggestion}?"
            log_miss("unknown_tool", tool=str(name)[:40])
            raise ToolError(json.dumps(actionable_error(
                f"Unknown tool: {str(name)[:40]}.",
                hint=hint,
                reason="unknown_tool",
            ))) from exc

    mcp.call_tool = call_tool  # type: ignore[method-assign]


_install_unknown_tool_hint()


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


def _help_json(request: Request, error: str, status: int = 404, *, hint: str | None = None, **extra) -> JSONResponse:
    from starlette.responses import PlainTextResponse
    payload = actionable_error(error, hint=hint, request=request, **extra)
    accept = (request.headers.get("accept") or "").lower()
    if "text/plain" in accept and "application/json" not in accept:
        lines = [
            payload["error"],
            payload["hint"],
            "Example: " + json.dumps(payload["example"]),
            "Docs: " + payload["docs"],
        ]
        return PlainTextResponse("\n".join(lines) + "\n", status_code=status)
    return JSONResponse(payload, status_code=status)


def _task_not_found_response(request: Request, task_id: str, rpc_id=None) -> JSONResponse:
    help_body = actionable_error(
        "Task not found.",
        hint="TraceCheck does not persist tasks. Send a new evaluate request to /a2a/message:send or POST /a2a (JSON-RPC message/send).",
        request=request,
        taskId=task_id,
        error_name="TaskNotFound",
        code=-32001,
        reason="task_not_found",
    )
    return JSONResponse(
        {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32001, "message": "TaskNotFound", "data": help_body}},
        status_code=404,
    )


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
    return JSONResponse({"status": "ok", "service": "tracecheck", "version": VERSION, "scoring_version": SCORING_VERSION,
                         "scoring_config": SCORING_CONFIG_SOURCE,
                         "scoring_config_complete": SCORING_CONFIG_SOURCE == "private" and not SCORING_CONFIG_FILLED})


@mcp.custom_route("/a2a/health", methods=["GET"])
async def a2a_health(request: Request):
    return await health(request)


@mcp.custom_route("/a2a/.well-known/agent-card.json", methods=["GET"])
async def a2a_agent_card(request: Request):
    return await agent_card(request)


@mcp.custom_route("/a2a", methods=["GET", "HEAD"])
async def a2a_discover(request: Request):
    from starlette.responses import Response
    if request.method == "HEAD":
        return Response(status_code=200, media_type="application/json")
    return JSONResponse(_agent_card(request))


@mcp.custom_route("/.well-known/mcp.json", methods=["GET"])
async def mcp_well_known(request: Request):
    base = _public_base(request)
    return JSONResponse({
        "name": "io.github.TGS-checker/tracecheck",
        "title": "TraceCheck",
        "description": REGISTRY_DESCRIPTION,
        "version": VERSION,
        "transport": "streamable-http",
        "url": f"{base}/mcp",
        "tools": ["evaluate_trace", "get_trace_schema"],
        "documentation": f"{REPO_URL}#readme",
        "llms_txt": f"{base}/llms.txt",
    })


@mcp.custom_route("/robots.txt", methods=["GET"])
async def robots_txt(request: Request):
    from starlette.responses import PlainTextResponse
    return PlainTextResponse(
        "User-agent: *\nAllow: /\n",
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "public, max-age=86400"},
    )


ASSETS_DIR = Path(__file__).with_name("assets")


async def _asset(name: str, media_type: str):
    from starlette.responses import Response
    f = ASSETS_DIR / name
    if not f.is_file():
        return JSONResponse(actionable_error(f"Asset not available: {name}.", hint="Use /icon.png, /icon.svg or /llms.txt.",
                                             reason="asset_not_found"), status_code=404)
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
        return JSONResponse(actionable_error("Document not available: llms.txt.", hint="Use /.well-known/agent-card.json or the README.",
                                             request=request, reason="asset_not_found"), status_code=404)
    text = LLMS_TXT_FILE.read_text(encoding="utf-8").replace(LIVE_URL, _public_base(request))
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8",
                             headers={"Cache-Control": "public, max-age=3600"})


@mcp.custom_route("/", methods=["GET"])
async def root(request: Request):
    base = _public_base(request)
    return JSONResponse({
        "name": "TraceCheck",
        "version": VERSION,
        "description": "Agent self-check and trace evaluation: verify your run before reporting success. Scores an "
                       "agent's execution trace 0-100 for reliability, with loop detection, error handling and "
                       "missing-verification flags, and returns concrete fixes. Deterministic, offline, free, no API key. "
                       "No content needed: send step types only; nothing you send is logged or stored.",
        "use_cases": list(LIVE_USE_CASES.values()),
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


_DEFAULT_GOAL = "unspecified agent run"


def _has_value(value) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value)
    return value is not None and value != ""


def _infer_outcome(trace) -> str:
    """Best-effort outcome from a privacy-light trace when the caller omitted one."""
    steps = _parse_steps(trace) or []
    if not steps:
        return "unknown"
    last = steps[-1]
    typ = (last.get("type") or "").lower()
    status = (last.get("status") or "").lower()
    if typ == "error" or status in _ERROR_STATUSES:
        return "failed"
    if status in _CONTINUE_STATUSES:
        return "partial"
    if typ == "verify" and status in _PASS_STATUSES:
        return "success"
    if typ in ("verify", "final") or status in _PASS_STATUSES:
        return "success"
    return "partial"


def _lenient_args(goal, trace, outcome):
    """Return (goal, trace, outcome) if we can safely score, else None.

    A compact or structured privacy-light trace is enough: missing goal/outcome
    are filled with safe defaults. Free-text traces still need all three fields.
    """
    has_trace = _has_value(trace)
    has_goal = isinstance(goal, str) and bool(goal.strip())
    has_outcome = isinstance(outcome, str) and bool(outcome.strip())
    if has_trace and has_goal and has_outcome:
        return goal, trace, outcome
    if has_trace and _parse_steps(trace):
        return (
            goal.strip() if has_goal else _DEFAULT_GOAL,
            trace,
            outcome.strip() if has_outcome else _infer_outcome(trace),
        )
    return None


def _missing_args_error(goal, trace, outcome) -> dict:
    missing = [n for n, v in (("goal", goal), ("trace", trace), ("outcome", outcome)) if not _has_value(v)]
    if not missing:
        missing = ["goal", "trace", "outcome"]
    return actionable_error(
        "Missing required field(s): " + ", ".join(missing) + ".",
        hint="Call get_trace_schema, then send step types only as in example (goal, trace, outcome).",
        reason="missing_fields",
    )


def _extract_args(message: dict) -> dict:
    """Pull goal/trace/outcome from an A2A message: a data part, JSON text, or a compact trace string."""
    if not isinstance(message, dict):
        return {}
    text_candidates = []
    for part in message.get("parts", []) or []:
        if not isinstance(part, dict):
            continue
        if isinstance(part.get("data"), dict):
            return part["data"]
        text = part.get("text")
        if text:
            text_candidates.append(text)
            try:
                obj = json.loads(text)
                if isinstance(obj, dict):
                    return obj
            except (ValueError, TypeError):
                pass
    for text in text_candidates:
        if isinstance(text, str) and _parse_steps(text):
            return {"trace": text}
    return {}


def _complete_args(args: dict | None) -> dict | None:
    args = args if isinstance(args, dict) else {}
    filled = _lenient_args(args.get("goal", ""), args.get("trace", ""), args.get("outcome", ""))
    if filled is None:
        return None
    goal, trace, outcome = filled
    out = dict(args)
    out["goal"], out["trace"], out["outcome"] = goal, trace, outcome
    return out


A2A_HELP = ("TraceCheck scores agent execution traces. Send one message part "
            "{\"data\": {\"goal\": str, \"trace\": str, \"outcome\": str, \"agent_name\": str?}} "
            "(or the same object as JSON text). trace = chronological steps, one per line, with what each tool "
            "returned, errors with their recovery, and a final 'Verified:' line. No content needed: send step "
            "types only, e.g. {\"goal\": \"data lookup\", \"trace\": \"action:search ok > error > retry > verify passed\", "
            "\"outcome\": \"success\"}. Nothing you send is logged or stored.")


def _a2a_help_parts() -> list:
    example = TRACE_SCHEMA["privacy_light_format"]["example_call"]
    compact = TRACE_SCHEMA["privacy_light_format"]["example_compact_trace"]
    text = (
        A2A_HELP
        + " Example evaluate request: "
        + json.dumps({"data": example})
        + f" Compact trace: '{compact}'."
    )
    return [
        {"text": text, "mediaType": "text/plain"},
        {"data": {"schema": TRACE_SCHEMA, "example_call": example,
                  "example_compact_trace": compact}, "mediaType": "application/json"},
    ]


def _a2a_http_message(request_message, parts) -> dict:
    message = request_message if isinstance(request_message, dict) else {}
    return {
        "message": {
            "messageId": str(uuid.uuid4()),
            "contextId": message.get("contextId") or str(uuid.uuid4()),
            "role": "ROLE_AGENT",
            "parts": parts,
        }
    }



async def _a2a_http_from_parsed(request: Request, raw: bytes, body):
    """A2A v1.0 HTTP+JSON SendMessage -> evaluate_trace (or a help reply, never 400)."""
    message = {}
    args = {}
    if body is None:
        text = raw.decode("utf-8", "replace").strip()
        if text:
            args = {"trace": text}
    elif isinstance(body, dict):
        message = body.get("message") or {}
        if not message and (body.get("parts") or body.get("goal") or body.get("trace")):
            if "parts" in body:
                message = body
            else:
                args = body
        if message:
            extracted = _extract_args(message)
            if extracted:
                args = extracted
        if not args and isinstance(body.get("params"), dict):
            message = body["params"].get("message") or message
            extracted = _extract_args(message)
            if extracted:
                args = extracted
    completed = _complete_args(args)
    if completed:
        result = serve_evaluation(completed["goal"], completed["trace"], completed["outcome"],
                                  agent_name=completed.get("agent_name", "unknown") or "unknown",
                                  agent_id=completed.get("agent_id") or completed.get("session_id"),
                                  previous_rating=completed.get("previous_rating"),
                                  previous_need_id=completed.get("previous_need_id"),
                                  client={**_client_from_request(request), "transport": "a2a"})
        parts = [
            {"data": result, "mediaType": "application/json"},
            {"text": result.get("summary") or result.get("error", ""), "mediaType": "text/plain"},
        ]
        return JSONResponse(_a2a_http_message(message, parts), media_type="application/a2a+json")
    return JSONResponse(_a2a_http_message(message, _a2a_help_parts()), media_type="application/a2a+json")


_A2A_TASK_METHODS = {"tasks/get", "tasks/cancel", "tasks/resubscribe", "GetTask", "CancelTask", "TaskResubscription"}


async def _a2a_jsonrpc_from_body(request: Request, body):
    if not isinstance(body, dict):
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request",
                             "data": actionable_error("Invalid Request: JSON-RPC body must be an object.", request=request,
                                                     reason="invalid_request")}})
    rpc_id = body.get("id")
    method = body.get("method")
    if method in _A2A_TASK_METHODS:
        params = body.get("params") or {}
        task_id = params.get("id") or params.get("taskId") or params.get("name") or "unknown"
        return _task_not_found_response(request, str(task_id), rpc_id=rpc_id)
    if method not in ("message/send", "SendMessage"):
        return JSONResponse({"jsonrpc": "2.0", "id": rpc_id,
                             "error": {"code": -32601,
                                       "message": f"Method not found: {method}. Supported: message/send, SendMessage",
                                       "data": actionable_error(
                                           f"Method not found: {method}.",
                                           hint="Use JSON-RPC method message/send (or SendMessage) at POST /a2a, or POST /a2a/message:send.",
                                           request=request, reason="method_not_found")}})
    v03 = method == "message/send"
    message = (body.get("params") or {}).get("message") or {}
    completed = _complete_args(_extract_args(message))
    if completed:
        result = serve_evaluation(completed["goal"], completed["trace"], completed["outcome"],
                                  agent_name=completed.get("agent_name", "unknown") or "unknown",
                                  agent_id=completed.get("agent_id") or completed.get("session_id"),
                                  previous_rating=completed.get("previous_rating"),
                                  previous_need_id=completed.get("previous_need_id"),
                                  client={**_client_from_request(request), "transport": "a2a-jsonrpc"})
        parts = [{"kind": "data", "data": result}, {"kind": "text", "text": result.get("summary") or result.get("error", "")}]
    else:
        parts = [{"kind": "text", "text": p["text"]} if "text" in p else {"kind": "data", "data": p["data"]}
                 for p in _a2a_help_parts()]
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


async def a2a_dispatch(request: Request, *, prefer_jsonrpc: bool = False):
    """Route a POST to JSON-RPC or HTTP+JSON A2A based on the body (and optional preference)."""
    raw = await request.body()
    try:
        body = json.loads(raw) if raw else {}
    except ValueError:
        if prefer_jsonrpc:
            return JSONResponse({"jsonrpc": "2.0", "id": None,
                                 "error": {"code": -32700, "message": "Parse error",
                                           "data": actionable_error("Parse error: body is not JSON.",
                                                                    hint="POST JSON-RPC {\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"message/send\",\"params\":{\"message\":...}} to /a2a.",
                                                                    request=request, reason="parse_error")}})
        return await _a2a_http_from_parsed(request, raw, None)
    if prefer_jsonrpc or (isinstance(body, dict) and (
        body.get("jsonrpc") or body.get("method") in ({"message/send", "SendMessage"} | _A2A_TASK_METHODS)
    )):
        return await _a2a_jsonrpc_from_body(request, body)
    return await _a2a_http_from_parsed(request, raw, body)


@mcp.custom_route("/a2a/message:send", methods=["POST"])
async def a2a_send_message(request: Request):
    """A2A v1.0 HTTP+JSON SendMessage -> evaluate_trace. Returns a Message with a data part.

    A plain-text or otherwise unknown first call gets HTTP 200 with a help message
    and a ready-to-use example (never 400). A compact privacy-light trace in the
    text is scored immediately.
    """
    return await a2a_dispatch(request, prefer_jsonrpc=False)


@mcp.custom_route("/a2a", methods=["POST"])
async def a2a_jsonrpc(request: Request):
    """A2A JSON-RPC binding (v0.3 `message/send`, v1.0 `SendMessage`) -> evaluate_trace.
    Messages without a trace get a help reply (not an error), so generic A2A clients get a usable answer."""
    return await a2a_dispatch(request, prefer_jsonrpc=True)


@mcp.custom_route("/v1/message:send", methods=["POST"])
@mcp.custom_route("/.well-known/agent-card.json", methods=["POST"])
@mcp.custom_route("/.well-known/agent.json", methods=["POST"])
@mcp.custom_route("/.well-known/agent-card.json/{rest:path}", methods=["POST"])
@mcp.custom_route("/.well-known/agent.json/{rest:path}", methods=["POST"])
async def a2a_alias_post(request: Request):
    """Agents that POST to the card URL, a suffix of it, or /v1/message:send are routed to A2A."""
    return await a2a_dispatch(request)


@mcp.custom_route("/a2a/tasks/{task_id:path}", methods=["GET", "POST", "DELETE"])
async def a2a_task(request: Request):
    return _task_not_found_response(request, request.path_params.get("task_id") or "unknown")



@mcp.custom_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def unknown_path(request: Request):
    """Host principle: never a bare 404. Point agents at the real endpoints and a working example."""
    path = request.url.path[:80]
    log_miss("unknown_path", path=path, method=request.method, user_agent=request.headers.get("user-agent"))
    return _help_json(
        request,
        f"Unknown path: {path}",
        404,
        hint="Use POST /mcp for MCP, POST /a2a/message:send or POST /a2a for A2A, or GET /.well-known/agent-card.json, /llms.txt, /health.",
        reason="unknown_path",
        path=path,
    )


# Stateless HTTP: clientInfo arrives only on the `initialize` POST (or, for 2026+ clients, in each request's
# _meta). This middleware remembers it per (hashed IP, user-agent) so the later tools/call can be attributed.
_RECENT_CLIENTS: dict = {}


def _remember_client(key: tuple, info: dict) -> None:
    if len(_RECENT_CLIENTS) > 2000:
        _RECENT_CLIENTS.clear()
    _RECENT_CLIENTS[key] = {"client_name": info.get("name"), "client_version": info.get("version")}



def _mcp_reject_reason(status: int, raw: bytes, scope: dict) -> str:
    text = raw.decode("utf-8", "replace") if raw else ""
    low = text.lower()
    headers = {k.decode("latin1").lower() if isinstance(k, (bytes, bytearray)) else str(k).lower():
               v.decode("latin1") if isinstance(v, (bytes, bytearray)) else str(v)
               for k, v in (scope.get("headers") or [])}
    accept = headers.get("accept", "")
    if status == 406 or "not acceptable" in low:
        return "not_acceptable"
    if status == 415 or "unsupported media type" in low:
        return "unsupported_media_type"
    if status == 405:
        return "method_not_allowed"
    if "missing session" in low:
        return "missing_session"
    if status == 404 or "session not found" in low:
        return "session_not_found"
    if "parse error" in low or (status == 400 and raw and not text.lstrip().startswith("{") and not text.lstrip().startswith("[")):
        return "invalid_json"
    if status == 400:
        return "bad_request"
    return f"http_{status}"


def _scope_base(scope: dict) -> str:
    public = os.environ.get("PUBLIC_URL")
    if public:
        return public.rstrip("/")
    headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in (scope.get("headers") or [])}
    proto = headers.get("x-forwarded-proto") or scope.get("scheme") or "https"
    host = headers.get("x-forwarded-host") or headers.get("host") or "tracecheck-mcp.onrender.com"
    return f"{proto}://{host}"


def _enrich_mcp_reject_body(status: int, raw: bytes, scope: dict, reason: str) -> bytes:
    help_body = actionable_error(
        f"MCP request rejected ({status}, {reason}).",
        hint="POST JSON-RPC to /mcp with Accept: application/json, text/event-stream. Start with initialize, then tools/call evaluate_trace.",
        base=_scope_base(scope),
        reason=reason,
        status=status,
    )
    if not raw:
        return json.dumps(help_body).encode()
    try:
        payload = json.loads(raw)
    except ValueError:
        help_body["error"] = f"MCP request rejected ({status}, {reason})."
        return json.dumps(help_body).encode()
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        msg = payload["error"].get("message")
        if msg:
            help_body["error"] = f"MCP request rejected ({status}, {reason}): {msg}"
        existing = payload["error"].get("data")
        if existing not in (None, ""):
            help_body["sdk_data"] = existing
        payload["error"]["data"] = help_body
        return json.dumps(payload).encode()
    return json.dumps(help_body).encode()


def _log_mcp_reject(scope: dict, status: int, reason: str) -> None:
    headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in (scope.get("headers") or [])}
    # Reconstruct a minimal request-like object for the hashed client fields only.
    class _Hdr:
        def __init__(self, h):
            self.headers = h
            self.client = None
    client = _client_from_request(_Hdr(headers))
    entry = {
        "status": status,
        "reason": reason,
        "method": scope.get("method"),
        "path": scope.get("path"),
        "accept": (headers.get("accept") or "")[:120] or None,
        "has_session": bool(headers.get("mcp-session-id")),
        **{k: client.get(k) for k in ("ip_hash", "user_agent") if client.get(k)},
    }
    print("TRACECHECK_MCP_REJECT " + json.dumps(entry, ensure_ascii=False), file=_CALL_STREAM, flush=True)
    logger.info("MCP request rejected: status=%s reason=%s", status, reason)


def _set_header(headers: list, name: bytes, value: bytes) -> list:
    out = [(k, v) for k, v in headers if k.lower() != name.lower()]
    out.append((name, value))
    return out



class McpGetHelp:
    """GET /mcp always returns 405 immediately. We offer no server-initiated SSE stream."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] == "http"
            and scope.get("method") == "GET"
            and _is_mcp_endpoint(scope.get("path", ""))
        ):
            help_body = actionable_error(
                "GET /mcp is not supported. Use POST.",
                hint="POST JSON-RPC (initialize, then tools/call) to /mcp. For a document, GET /llms.txt.",
                base=_scope_base(scope),
                reason="method_not_allowed",
            )
            raw = json.dumps(help_body).encode()
            await send({
                "type": "http.response.start",
                "status": 405,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(raw)).encode()),
                    (b"allow", b"POST"),
                ],
            })
            await send({"type": "http.response.body", "body": raw, "more_body": False})
            return
        await self.app(scope, receive, send)


def _is_mcp_endpoint(path: str) -> bool:
    """True only for /mcp and /mcp/..., not lookalikes such as /mcpx."""
    return path == "/mcp" or path.startswith("/mcp/")


class McpRejectLogger:
    """Log a short reason code for /mcp 4xx (no request content) and attach actionable help to the body."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not _is_mcp_endpoint(scope.get("path", "")):
            return await self.app(scope, receive, send)
        status = None
        headers = []
        chunks = []
        pending = False

        async def wrapper(message):
            nonlocal status, headers, pending
            typ = message["type"]
            if typ == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers") or [])
                if status and status >= 400:
                    pending = True
                    return
                await send(message)
                return
            if typ == "http.response.body" and pending:
                chunks.append(message.get("body", b""))
                if message.get("more_body"):
                    return
                raw = b"".join(chunks)
                reason = _mcp_reject_reason(status, raw, scope)
                try:
                    _log_mcp_reject(scope, status, reason)
                except Exception:
                    logger.warning("could not log MCP reject", exc_info=True)
                try:
                    new_body = _enrich_mcp_reject_body(status, raw, scope, reason)
                except Exception:
                    new_body = raw
                headers = _set_header(headers, b"content-type", b"application/json")
                headers = _set_header(headers, b"content-length", str(len(new_body)).encode())
                await send({"type": "http.response.start", "status": status, "headers": headers})
                await send({"type": "http.response.body", "body": new_body, "more_body": False})
                pending = False
                return
            await send(message)

        await self.app(scope, receive, wrapper)


class ClientInfoSniffer:
    """Pure-ASGI middleware: peeks at POST /mcp JSON bodies for clientInfo, then replays the body unchanged."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST" or not _is_mcp_endpoint(scope.get("path", "")):
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


class BodyLimitAndGlobalRate:
    """Reject oversized bodies (413) and global floods (429) with helpful JSON. In memory, no IPs."""

    def __init__(self, app):
        self.app = app
        self.window: list = []

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        import time
        now = time.monotonic()
        self.window = [t for t in self.window if now - t < 60]
        reason = None
        if len(self.window) >= GLOBAL_LIMIT_PER_MIN:
            status, reason, msg = 429, "rate_limited", "Service is busy. Try again in a minute."
        else:
            self.window.append(now)
            headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in (scope.get("headers") or [])}
            try:
                length = int(headers.get("content-length") or 0)
            except ValueError:
                length = 0
            if length > LIMITS["max_body_bytes"]:
                status, reason = 413, "body_too_large"
                msg = f"Request body is {length} bytes (max {LIMITS['max_body_bytes']})."
        if reason:
            body = actionable_error(msg, hint="Send step types only (no content), one short step per tool call.",
                                    base=_scope_base(scope), reason=reason)
            raw = json.dumps(body).encode()
            hdrs = [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode())]
            if status == 429:
                hdrs.append((b"retry-after", b"60"))
            await send({"type": "http.response.start", "status": status, "headers": hdrs})
            await send({"type": "http.response.body", "body": raw})
            return
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body") or b"")
                if received > LIMITS["max_body_bytes"]:
                    raise ValueError("request body too large")
            return message
        return await self.app(scope, limited_receive, send)


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
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "HEAD", "POST", "DELETE", "OPTIONS"],
                       allow_headers=["*"], expose_headers=["mcp-session-id", "mcp-protocol-version"])
    app.add_middleware(ClientInfoSniffer)
    app.add_middleware(McpRejectLogger)
    app.add_middleware(McpGetHelp)
    app.add_middleware(BodyLimitAndGlobalRate)
    from starlette.exceptions import HTTPException

    async def _http_exception_handler(request, exc):
        if _is_mcp_endpoint(request.url.path):
            # /mcp 4xx are handled by McpRejectLogger; keep the SDK response if Starlette raises.
            from starlette.responses import Response
            return Response(status_code=exc.status_code, headers=getattr(exc, "headers", None) or {})
        hint = "Use POST /mcp for MCP, POST /a2a/message:send or POST /a2a for A2A, or GET /.well-known/agent-card.json, /llms.txt, /health."
        if exc.status_code == 405:
            hint = "This path exists but the method is not allowed. " + hint
        resp = _help_json(request, str(exc.detail) if getattr(exc, "detail", None) else f"HTTP {exc.status_code}",
                          exc.status_code, hint=hint,
                          reason={405: "method_not_allowed", 404: "unknown_path"}.get(exc.status_code, "bad_request"))
        if exc.status_code == 405:
            resp.headers["Allow"] = "GET, POST, HEAD, OPTIONS"
        return resp

    app.add_exception_handler(HTTPException, _http_exception_handler)
    app.add_exception_handler(404, _http_exception_handler)
    app.add_exception_handler(405, _http_exception_handler)
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
