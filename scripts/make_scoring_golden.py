"""Write the PRIVATE golden results for tests/scoring_cases.py. Requires the private scoring config.
Usage: TRACECHECK_SCORING_CONFIG_PATH=<private config> python scripts/make_scoring_golden.py <output path outside this repo>"""
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
data = {name: scoring_cases.summarize(server.score_trace(*args)) for name, args in scoring_cases.cases().items()}
out.write_text(json.dumps({"scoring_version": server.SCORING_VERSION, "cases": data,
                                "config_sha256": hashlib.sha256(json.dumps(server.SC, sort_keys=True).encode()).hexdigest()}, indent=1, sort_keys=True))
print(f"wrote {len(data)} cases")
