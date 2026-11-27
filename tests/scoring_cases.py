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
    return out


def summarize(result: dict) -> dict:
    return {"score": result["score"], "dimensions": result["dimensions"],
            "codes": [i["code"] for i in result["issues"]], "fix_ids": [s["id"] for s in result["suggestions"]]}
