"""Write the PRIVATE golden results for tests/scoring_cases.py. Requires the private scoring config.
Usage: TRACECHECK_SCORING_CONFIG_PATH=<private config> python scripts/make_scoring_golden.py <output path outside this repo> [bands.json]
Score bands (private, {case_name: [lo, hi]}) are kept from the existing golden file unless a bands file is given."""
import hashlib
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
os.environ.setdefault("TRACECHECK_LOG_FILE", str(ROOT / "tests" / ".calls.jsonl"))
import server  # noqa: E402
import scoring_cases  # noqa: E402

if server.SCORING_CONFIG_SOURCE != "private":
    sys.exit("refusing: private scoring config not loaded")
out = pathlib.Path(sys.argv[1]).resolve()
if ROOT in out.parents:
    sys.exit("refusing: the golden file is private and must live outside this repo")
old = json.loads(out.read_text()) if out.exists() else {}
bands = json.loads(pathlib.Path(sys.argv[2]).read_text()) if len(sys.argv) > 2 else old.get("bands", {})
data = {name: scoring_cases.summarize(server.score_trace(*args)) for name, args in scoring_cases.cases().items()}
unknown = set(bands) - set(data)
if unknown:
    sys.exit(f"refusing: bands for unknown cases {sorted(unknown)}")
out.write_text(json.dumps({"scoring_version": server.SCORING_VERSION, "cases": data, "bands": bands,
                                "config_sha256": hashlib.sha256(json.dumps(server.SC, sort_keys=True).encode()).hexdigest()}, indent=1, sort_keys=True))
print(f"wrote {len(data)} cases")
