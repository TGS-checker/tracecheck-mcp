#!/usr/bin/env python3
"""TraceCheck learning loop: one event store in, two knowledge tiers out.

  learn.py --ingest-log FILE        append TRACECHECK_CALL / TRACECHECK_MCP_REJECT / TRACECHECK_MISS lines
  learn.py --ingest-feedback FILE   append reviewer feedback rows (staging CSV) as event_type=feedback
  learn.py                          daily analysis -> knowledge-full.json (private) + serving.json (public)
  learn.py --weekly                 daily analysis plus funnel, calibration, need clusters, signpost proposals

Paths (env or flags): TRACECHECK_STORE, TRACECHECK_KNOWLEDGE, TRACECHECK_SERVING.
The event store and knowledge-full.json are PRIVATE and must never be committed to the public repo.
serving.json is PUBLIC: aggregated, above a minimum n, no agent ids, no profiles, no raw events.
Stdlib only, no network.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
COLUMNS = [
    "event_id", "event_type", "ts_utc", "schema_version", "server_version", "scoring_version",
    "agent_id", "agent_kind", "client_family", "session_id", "visit_no", "first_seen", "secs_since_prev",
    "origin_route", "referer_host", "entry_path",
    "protocol", "endpoint", "method", "http_status", "reason_code", "latency_ms", "cold_start", "recovered_after_error",
    "arg_shape", "goal_category", "step_pattern", "step_count", "error_count", "retry_count", "has_verify",
    "outcome_claimed", "score", "issue_codes", "fix_ids", "confidence", "knowledge_match", "need_id",
    "benchmark_given", "prev_score_same_agent_need", "score_delta", "issues_resolved", "fix_applied",
    "explicit_feedback", "returning", "payload",
]
NOT_NET = {"own_test", "crawler", "reviewer", "checker", "unknown"}
MIN_N_BENCHMARK = 5
MIN_N_FIX = 3
MIN_AGENTS = 3
MAX_CALLS_PER_AGENT = 5
DEFAULT_STORE = "/workspace/SecondBrain/data/tracecheck-stats.csv"
DEFAULT_KNOWLEDGE = "/workspace/SecondBrain/data/knowledge-full.json"
DEFAULT_SERVING = str(Path(__file__).resolve().parent.parent / "data" / "serving.json")
FORBIDDEN_PUBLIC_KEYS = {"agent_id", "agent_name", "profile", "profiles", "ip_hash", "event_id", "user_agent",
                         "session_id", "need_id", "ts_utc", "raw", "events", "client_name"}


def _id(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _agent_from_legacy(d: dict) -> str:
    basis = d.get("agent_id") or (f"name:{d.get('agent_name')}" if d.get("agent_name") not in (None, "", "unknown")
                                   else f"client:{d.get('client_name') or ''}/{d.get('client_version') or ''}")
    return d.get("agent_id") or "ag_" + hashlib.sha256(str(basis).encode()).hexdigest()[:12]


_LEGACY_KIND = {"tracecheck-smoketest": "own_test", "selfcheck-doc-test": "own_test", "readme-demo": "own_test",
                "pod-geldverdiener": "reviewer", "grok-bot": "own_test", "grid-station-review": "checker",
                "sealkeeperbot": "checker", "tracecheck-live-check": "own_test", "tracecheck-smoke": "own_test",
                "tracecheck-selftest": "own_test", "pod-fixture": "own_test"}


def _kind(d: dict) -> str:
    name = str(d.get("agent_name") or "").lower()
    client = str(d.get("client_name") or "").lower() + " " + str(d.get("user_agent") or "").lower()
    for k, v in _LEGACY_KIND.items():
        if k in name or k in client:
            return v
    return d.get("agent_kind") or ("agent" if name and name != "unknown" else "unknown")


def event_from_log(line: str) -> dict | None:
    line = line.strip()
    for prefix, etype in (("TRACECHECK_CALL", "call"), ("TRACECHECK_MCP_REJECT", "error"), ("TRACECHECK_MISS", "miss")):
        idx = line.find(prefix + " ")
        if idx >= 0:
            try:
                d = json.loads(line[idx + len(prefix) + 1:])
            except ValueError:
                return None
            break
    else:
        try:
            d = json.loads(line)
            etype = d.get("event_type") or "call"
        except ValueError:
            return None
    if not isinstance(d, dict):
        return None
    ts = d.get("ts_utc") or d.get("timestamp") or ""
    ev = {c: "" for c in COLUMNS}
    ev.update({
        "event_id": d.get("event_id") or _id(etype, ts, json.dumps(d, sort_keys=True)),
        "event_type": etype, "ts_utc": ts, "schema_version": SCHEMA_VERSION,
        "server_version": d.get("server_version") or d.get("version") or "",
        "scoring_version": d.get("scoring_version") or "",
        "agent_id": _agent_from_legacy(d), "agent_kind": _kind(d),
        "client_family": d.get("client_family") or str(d.get("client_name") or d.get("user_agent") or "").split("/")[0][:40],
        "protocol": d.get("transport") or "",
        "endpoint": (d.get("path") or "")[:60], "method": d.get("method") or "",
        "http_status": d.get("status") or "", "reason_code": d.get("reason") or "",
        "arg_shape": d.get("arg_shape") or ("privacy_light" if d.get("privacy_mode") else ("free_text" if etype == "call" else "")),
    })
    for k in ("goal_category", "step_pattern", "step_count", "error_count", "retry_count", "has_verify", "score",
              "need_id", "knowledge_match", "benchmark_given", "prev_score_same_agent_need", "score_delta",
              "explicit_feedback", "returning"):
        if d.get(k) is not None:
            ev[k] = d[k]
    for k in ("issue_codes", "fix_ids", "issues_resolved"):
        if d.get(k):
            ev[k] = "|".join(d[k])
    if d.get("previous_need_id"):
        ev["payload"] = json.dumps({"previous_need_id": d["previous_need_id"]})
    return ev


def read_store(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def append_events(path: Path, events: list[dict]) -> int:
    existing = {e["event_id"] for e in read_store(path)}
    new = [e for e in events if e and e["event_id"] not in existing]
    seen = set()
    new = [e for e in new if not (e["event_id"] in seen or seen.add(e["event_id"]))]
    write_header = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        if write_header:
            w.writeheader()
        for e in new:
            w.writerow(e)
    return len(new)


def feedback_events(csv_path: Path) -> list[dict]:
    out = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        for n, row in enumerate(csv.DictReader(f)):
            ev = {c: "" for c in COLUMNS}
            date = row.get("date") or ""
            ev.update({
                "event_id": _id("feedback", row.get("batch_id"), row.get("trace_id") or f"row{n}", n),
                "event_type": "feedback", "ts_utc": date, "schema_version": SCHEMA_VERSION,
                "server_version": "", "scoring_version": row.get("scoring_version") or "",
                "agent_id": row.get("source") or "pod-geldverdiener", "agent_kind": "reviewer",
                "score": row.get("our_score") or "", "issue_codes": row.get("issue_codes_found") or "",
                "explicit_feedback": row.get("value_to_submitter") or "",
            })
            payload = {k: v for k, v in row.items() if v not in (None, "") and k not in ("date", "source", "our_score",
                                                                                          "issue_codes_found", "scoring_version")}
            ev["payload"] = json.dumps(payload, ensure_ascii=False)
            out.append(ev)
    return out


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _codes(v) -> list[str]:
    return [c for c in str(v or "").split("|") if c]


def _pct(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0
    k = (len(sorted_vals) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return round(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo), 1)


def analyse(events: list[dict], weekly: bool = False) -> tuple[dict, dict]:
    calls = sorted([e for e in events if e["event_type"] == "call" and _num(e.get("score")) is not None],
                   key=lambda e: e["ts_utc"])
    net = [e for e in calls if e.get("agent_kind") not in NOT_NET]
    by_agent = defaultdict(list)
    for e in calls:
        by_agent[e["agent_id"]].append(e)

    # Fix effect: issue present in run n, gone in run n+1 of the same agent (same scoring_version).
    fix_eff = defaultdict(list)
    fix_eff_all = defaultdict(list)
    for aid, runs in by_agent.items():
        for a, b in zip(runs, runs[1:]):
            if a.get("scoring_version") != b.get("scoring_version"):
                continue
            if (a.get("goal_category") or "other") != (b.get("goal_category") or "other"):
                continue  # 0.5.1: never compare score deltas across different goal types
            gone = set(_codes(a["issue_codes"])) - set(_codes(b["issue_codes"]))
            delta = _num(b["score"]) - _num(a["score"])
            for fid in _codes(a["fix_ids"]):
                target = fix_eff if a.get("agent_kind") not in NOT_NET else fix_eff_all
                target[fid].append({"delta": delta, "resolved": bool(gone), "agent": aid})
    fixes_public = {}
    fixes_private = {}
    for fid, rows in {**fix_eff_all, **fix_eff}.items():
        fixes_private[fid] = {"n": len(rows), "avg_score_delta": round(statistics.mean(r["delta"] for r in rows), 1),
                              "resolved_rate": round(sum(r["resolved"] for r in rows) / len(rows), 2),
                              "net": fid in fix_eff}
    for fid, rows in fix_eff.items():
        agents = {r["agent"] for r in rows}
        if len(rows) >= MIN_N_FIX and len(agents) >= 2:
            fixes_public[fid] = {"n": len(rows), "avg_score_delta": round(statistics.mean(r["delta"] for r in rows), 1)}

    # Net benchmarks per goal category: dedup per agent, cap per-agent weight, same scoring_version.
    bench_public, bench_private = {}, {}
    latest_sv = max((e.get("scoring_version") or "" for e in net), default="")
    per_cat = defaultdict(list)
    cat_agents = defaultdict(set)
    per_agent_seen = Counter()
    seen_identical = set()
    for e in net:
        if e.get("scoring_version") != latest_sv:
            continue
        key = (e["agent_id"], e.get("step_pattern"), e.get("score"))
        if key in seen_identical or per_agent_seen[e["agent_id"]] >= MAX_CALLS_PER_AGENT:
            continue
        seen_identical.add(key)
        per_agent_seen[e["agent_id"]] += 1
        per_cat[e.get("goal_category") or "other"].append(_num(e["score"]))
        cat_agents[e.get("goal_category") or "other"].add(e["agent_id"])
    for cat, vals in per_cat.items():
        vals.sort()
        row = {"n": len(vals), "median": _pct(vals, 0.5), "deciles": [_pct(vals, q / 10) for q in range(1, 10)]}
        bench_private[cat] = row
        if len(vals) >= MIN_N_BENCHMARK and len(cat_agents[cat]) >= MIN_AGENTS:
            bench_public[cat] = row

    needs = Counter(c for e in calls for c in _codes(e["issue_codes"]))
    needs_net = Counter(c for e in net for c in _codes(e["issue_codes"]))
    misses = Counter((e.get("reason_code") or "", e.get("endpoint") or "") for e in events if e["event_type"] in ("miss", "error"))
    alias_suggestions = [{"reason": r, "path": p, "count": n} for (r, p), n in misses.most_common(20) if n >= 2]
    days = defaultdict(set)
    for e in calls:
        days[e["agent_id"]].add(e["ts_utc"][:10])
    profiles = {}
    for aid, runs in by_agent.items():
        scores = [_num(r["score"]) for r in runs]
        ratings = [_num(r["explicit_feedback"]) for r in runs if _num(r.get("explicit_feedback"))]
        profiles[aid] = {
            "agent_kind": runs[-1].get("agent_kind"), "client_family": runs[-1].get("client_family"),
            "first_seen": runs[0]["ts_utc"], "last_seen": runs[-1]["ts_utc"], "calls": len(runs),
            "active_days": len(days[aid]), "returning": len(days[aid]) > 1,
            "avg_score": round(statistics.mean(scores), 1), "trend": round(scores[-1] - scores[0], 1),
            "top_needs": [c for c, _ in Counter(c for r in runs for c in _codes(r["issue_codes"])).most_common(3)],
            "ratings": ratings,
        }
    links = defaultdict(set)
    for aid, p in profiles.items():
        for c in p["top_needs"]:
            links[c].add(aid)
    rated = [e for e in calls if _num(e.get("explicit_feedback"))]
    rating_stats = {"rated_calls": len(rated), "total_calls": len(calls),
                    "ratio": round(len(rated) / len(calls), 3) if calls else 0,
                    "avg": round(statistics.mean(_num(e["explicit_feedback"]) for e in rated), 2) if rated else None,
                    "distribution": dict(Counter(int(_num(e["explicit_feedback"])) for e in rated))}
    feedback = [e for e in events if e["event_type"] == "feedback"]
    gaps = []
    for e in feedback:
        p = json.loads(e["payload"] or "{}")
        g = _num(p.get("gap"))
        if g is not None:
            gaps.append({"batch_id": p.get("batch_id"), "trace_id": p.get("trace_id"), "gap": g,
                         "scoring_version": e["scoring_version"]})
    pod_values = [_num(json.loads(e["payload"] or "{}").get("value_to_submitter") or e.get("explicit_feedback"))
                  for e in feedback]
    pod_values = [v for v in pod_values if v is not None]

    knowledge = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "counts": {"events": len(events), "by_type": dict(Counter(e["event_type"] for e in events)),
                   "calls": len(calls), "net_calls": len(net), "agents": len(by_agent),
                   "agents_by_kind": dict(Counter(p["agent_kind"] for p in profiles.values())),
                   "new_agents": sum(1 for p in profiles.values() if not p["returning"]),
                   "returning_agents": sum(1 for p in profiles.values() if p["returning"])},
        "needs_all": dict(needs.most_common()), "needs_net": dict(needs_net.most_common()),
        "fixes": fixes_private, "benchmarks": bench_private, "alias_suggestions": alias_suggestions,
        "profiles": profiles, "cross_agent_links": {c: sorted(a) for c, a in links.items() if len(a) > 1},
        "ratings": rating_stats,
        "feedback": {"events": len(feedback), "gaps": gaps,
                     "mean_abs_gap": round(statistics.mean(abs(g["gap"]) for g in gaps), 1) if gaps else None,
                     "pod_value_avg": round(statistics.mean(pod_values), 2) if pod_values else None,
                     "pod_value_n": len(pod_values)},
    }
    if weekly:
        knowledge["weekly"] = weekly_report(events, calls, profiles, fixes_private, needs, feedback, gaps)
    serving = {
        "schema": "tracecheck-serving/1",
        "generated_utc": knowledge["generated_utc"],
        "scoring_version": latest_sv,
        "min_n": {"benchmark": MIN_N_BENCHMARK, "fix": MIN_N_FIX},
        "fixes": fixes_public,
        "benchmarks": bench_public,
        "aliases": {},
    }
    assert_public_safe(serving)
    return knowledge, serving


def weekly_report(events, calls, profiles, fixes, needs, feedback, gaps) -> dict:
    found = len({e["agent_id"] for e in events if e["event_type"] in ("discovery", "miss", "error", "call")})
    first_call = len(profiles)
    success = len({e["agent_id"] for e in calls if _num(e["score"]) is not None})
    improved = len({aid for aid, p in profiles.items() if p["calls"] > 1 and p["trend"] > 0})
    returned = sum(1 for p in profiles.values() if p["returning"])
    funnel = {"found": found, "first_call": first_call, "successful_call": success, "improved_run": improved,
              "returned": returned}
    steps = list(funnel.items())
    leaks = [(f"{a[0]}->{b[0]}", (a[1] - b[1]) / a[1] if a[1] else 0) for a, b in zip(steps, steps[1:])]
    biggest = max(leaks, key=lambda x: x[1]) if leaks else None
    no_good_fix = [c for c, n in needs.items() if n >= 3 and not any(
        f.get("avg_score_delta", 0) > 0 for fid, f in fixes.items() if fid.endswith(c.split("_")[-1]))]
    proposals = []
    for e in feedback:
        p = json.loads(e["payload"] or "{}")
        if p.get("usecase_candidate") == "y":
            proposals.append({"batch_id": p.get("batch_id"), "trace_id": p.get("trace_id"),
                              "recommendation": p.get("tester_recommendation")})
    for aid, p in profiles.items():
        if p["agent_kind"] not in NOT_NET and any(r >= 4 for r in p["ratings"]):
            proposals.append({"agent_need": p["top_needs"][:1], "reason": "external rating >= 4"})
    return {"funnel": funnel, "biggest_leak": biggest,
            "calibration": {"pod_gaps": gaps, "n": len(gaps),
                            "mean_gap": round(statistics.mean(g["gap"] for g in gaps), 1) if gaps else None},
            "needs_without_good_fix": no_good_fix, "signpost_proposals": proposals}


def _walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_keys(v)


def assert_public_safe(serving: dict) -> None:
    bad = FORBIDDEN_PUBLIC_KEYS & set(_walk_keys(serving))
    text = json.dumps(serving)
    if bad or "ag_" in text or "pod-geldverdiener" in text:
        raise ValueError(f"serving.json would leak private fields: {sorted(bad) or 'agent ids'}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", default=os.environ.get("TRACECHECK_STORE", DEFAULT_STORE))
    ap.add_argument("--knowledge", default=os.environ.get("TRACECHECK_KNOWLEDGE", DEFAULT_KNOWLEDGE))
    ap.add_argument("--serving", default=os.environ.get("TRACECHECK_SERVING", DEFAULT_SERVING))
    ap.add_argument("--ingest-log", metavar="FILE")
    ap.add_argument("--ingest-feedback", metavar="FILE")
    ap.add_argument("--weekly", action="store_true")
    ap.add_argument("--no-write", action="store_true", help="analyse only, print summary")
    a = ap.parse_args(argv)
    store = Path(a.store)
    if a.ingest_log:
        lines = Path(a.ingest_log).read_text(encoding="utf-8").splitlines()
        n = append_events(store, [event_from_log(l) for l in lines])
        print(f"ingested {n} new log events into {store.name}")
        return 0
    if a.ingest_feedback:
        n = append_events(store, feedback_events(Path(a.ingest_feedback)))
        print(f"ingested {n} new feedback events into {store.name}")
        return 0
    knowledge, serving = analyse(read_store(store), weekly=a.weekly)
    if not a.no_write:
        Path(a.knowledge).write_text(json.dumps(knowledge, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        sp = Path(a.serving)
        new = json.dumps(serving, indent=2, ensure_ascii=False) + "\n"
        old = sp.read_text(encoding="utf-8") if sp.exists() else ""
        strip = lambda s: json.dumps({k: v for k, v in json.loads(s).items() if k != "generated_utc"}, sort_keys=True) if s else ""
        if strip(old) != strip(new):
            sp.parent.mkdir(parents=True, exist_ok=True)
            sp.write_text(new, encoding="utf-8")
            print("serving.json changed")
        else:
            print("serving.json unchanged")
    c = knowledge["counts"]
    print(f"events={c['events']} calls={c['calls']} net_calls={c['net_calls']} agents={c['agents']} "
          f"new={c['new_agents']} returning={c['returning_agents']} feedback={knowledge['feedback']['events']} "
          f"ratings={knowledge['ratings']['rated_calls']}")
    if a.weekly:
        w = knowledge["weekly"]
        print(f"funnel={w['funnel']} biggest_leak={w['biggest_leak']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
