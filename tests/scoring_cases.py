"""Named cases whose exact tuned results are pinned in the PRIVATE golden file (never in this repo).

Regenerate after an intentional calibration change (private config active):
    TRACECHECK_SCORING_CONFIG_PATH=... python scripts/make_scoring_golden.py <private golden path>
"""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def cases() -> dict:
    out = {}
    for f in sorted((ROOT / "examples").glob("0*.json")):
        d = json.loads(f.read_text())
        out[f"example:{f.stem}"] = (d["goal"], d["trace"], d["outcome"])
    for f in sorted((ROOT / "tests" / "fixtures").glob("pod_*.json")):
        d = json.loads(f.read_text())
        out[f"fixture:{f.stem}"] = (d["goal"], d["trace"], d["outcome"])
    import test_scoring as ts  # same traces as the behaviour tests
    out["scoring:progressing_shell"] = ("code change", ts.PROGRESSING_SHELL_TRACE, "success")
    out["scoring:identical_shell_error"] = ("code change", ts.IDENTICAL_SHELL_ERROR_TRACE, "success")
    out["compact:failed_verify"] = ("code change", "action:edit ok > verify failed", "partial")
    out["compact:pagination"] = ("data lookup", "action:list partial > action:list partial > action:list ok > verify passed",
                                 "success")
    # 0.5.5: optional run context (expected checks, scope, blockers). Generic coordinator runs, identifiers only.
    six = ["tests", "lint", "build", "docs", "accessibility", "security_scan"]
    coord = ("action:plan ok > action:delegate_build ok > action:delegate_qa ok > verify:tests passed > "
             "verify:lint passed > verify:build passed > verify:docs passed > verify:accessibility passed")
    out["context:all_checks_passed"] = ("code change", coord + " > verify:security_scan passed > final", "success",
                                        {"expected_checks": six})
    out["context:missing_check_success"] = ("code change", coord + " > final", "success", {"expected_checks": six})
    out["context:missing_check_partial"] = ("code change", coord + " > handoff:owner ok", "partial",
                                            {"expected_checks": six})
    out["context:missing_check_reported_open"] = ("code change", coord + " > final", "partial",
                                                  {"expected_checks": six,
                                                   "open_blockers": {"count": 1, "ids": ["security_scan"]}})
    out["context:failed_check_no_pass"] = ("code change", coord + " > verify:security_scan failed > final", "success",
                                           {"expected_checks": six})
    out["context:failed_then_passed"] = ("code change", coord + " > verify:security_scan failed > "
                                         "action:fix_finding ok > verify:security_scan passed > final", "success",
                                         {"expected_checks": six})
    out["context:unconfirmed_check"] = ("code change", coord + " > verify:security_scan > final", "success",
                                        {"expected_checks": six})
    out["context:out_of_scope_action"] = ("code change", "action:edit_file ok > action:deploy ok > verify:tests passed > final",
                                          "success", {"out_of_scope": ["deploy", "delete_branch"]})
    out["context:in_scope_violation"] = ("data lookup", "action:search ok > action:send_email ok > verify passed > final",
                                         "success", {"in_scope": ["search", "fetch"]})
    out["context:success_open_blockers"] = ("code change", "action:edit_file ok > verify:tests passed > final", "success",
                                            {"open_blockers": {"count": 2}})
    return out


def summarize(result: dict) -> dict:
    out = {"score": result["score"], "verdict": result.get("verdict"), "dimensions": result["dimensions"],
           "codes": [i["code"] for i in result["issues"]], "fix_ids": [s["id"] for s in result["suggestions"]]}
    for key in ("checks", "scope", "blockers"):  # 0.5.5, only present with a run context
        if key in result:
            out[key] = result[key]
    return out
