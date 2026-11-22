"""Score every examples/*.json trace with TraceCheck's evaluate_trace and print a table.
Run from the repo root:  python examples/run_examples.py
Logs go to examples/calls.example.jsonl so the real calls.jsonl stays clean."""
import json, os, sys
from pathlib import Path

here = Path(__file__).resolve().parent
os.environ.setdefault("TRACECHECK_LOG_FILE", str(here / "calls.example.jsonl"))
sys.path.insert(0, str(here.parent))
from server import evaluate_trace  # noqa: E402

for f in sorted(here.glob("*.json")):
    ex = json.loads(f.read_text())
    r = evaluate_trace(ex["goal"], ex["trace"], ex["outcome"], ex.get("agent_name", "unknown"))
    print(f"{f.name:36s} score={r['score']:3d}  dims={r['dimensions']}")
    for i in r["issues"]:
        print(f"    - {i}")
