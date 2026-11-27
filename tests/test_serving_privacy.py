"""Public data/serving.json must never contain agent ids, profiles, IP hashes or raw events."""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
FORBIDDEN = {"agent_id", "agent_name", "profile", "profiles", "ip_hash", "event_id", "user_agent", "session_id",
             "need_id", "ts_utc", "raw", "events", "client_name", "payload", "trace", "goal"}


def _keys(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k
            yield from _keys(v)
    elif isinstance(o, list):
        for v in o:
            yield from _keys(v)


def test_serving_json_is_aggregate_only():
    data = json.loads((ROOT / "data" / "serving.json").read_text())
    assert not (FORBIDDEN & set(_keys(data)))
    text = json.dumps(data)
    assert not re.search(r"ag_[0-9a-f]{12}", text)
    assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", text)
    assert "pod-geldverdiener" not in text
    for row in data.get("benchmarks", {}).values():
        assert row["n"] >= data["min_n"]["benchmark"]
    for row in data.get("fixes", {}).values():
        assert row["n"] >= data["min_n"]["fix"]


def test_no_private_data_files_in_repo():
    for p in ROOT.rglob("*"):
        if ".venv" in p.parts or ".git" in p.parts:
            continue
        assert p.name not in ("tracecheck-stats.csv", "knowledge-full.json"), p
