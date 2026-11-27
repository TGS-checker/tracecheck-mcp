"""Scoring knowledge is private: the code is open, the tuned config is not.

- Both modes: the service works, every response carries codes and fix ids.
- Private mode only: exact tuned results equal the private golden file.
- Always: no private config or golden file is tracked in this repo; the fallback is not the private config.
"""
import hashlib
import json
import os
import pathlib
import subprocess

import pytest

import scoring_cases
import server

ROOT = pathlib.Path(__file__).resolve().parent.parent
PRIVATE = server.SCORING_CONFIG_SOURCE == "private"
requires_private = pytest.mark.skipif(not PRIVATE, reason="needs the private scoring config (CI runs the public fallback)")
FALLBACK = json.loads(server.SCORING_CONFIG_FALLBACK_FILE.read_text())


def _golden_path() -> pathlib.Path | None:
    p = os.environ.get("TRACECHECK_SCORING_GOLDEN_PATH")
    if p:
        return pathlib.Path(p)
    cfg = os.environ.get("TRACECHECK_SCORING_CONFIG_PATH")
    return pathlib.Path(cfg).with_name("scoring-golden.json") if cfg else None


def test_every_case_returns_codes_and_fix_ids_in_this_mode():
    for name, args in scoring_cases.cases().items():
        r = server.score_trace(*args)
        assert isinstance(r["score"], int) and 0 <= r["score"] <= 100, name
        assert all(i["code"] in server.ISSUES and i["text"] for i in r["issues"]), name
        assert all(s["id"] in server.FIXES and s["text"] for s in r["suggestions"]), name
        assert set(r["dimensions"]) == {"goal_clarity", "observability", "error_recovery", "goal_alignment", "efficiency"}


def test_clean_runs_beat_flawed_runs_in_this_mode():
    c = {n: server.score_trace(*a)["score"] for n, a in scoring_cases.cases().items()}
    for good in ("example:01_clean_success", "example:05_error_then_retry_success", "example:08_privacy_light_steps"):
        for bad in ("example:02_looping_redundant", "example:03_ignored_error_ungrounded", "example:04_vague_minimal"):
            assert c[good] > c[bad], (good, bad)


def test_fallback_is_complete_and_loads():
    cfg, src = server.load_scoring_config(env={})
    assert src == "fallback" and cfg == FALLBACK
    assert server._config_shape_errors(FALLBACK, FALLBACK) == []
    assert abs(sum(FALLBACK["weights"].values()) - 1.0) < 1e-9


def test_bad_private_config_falls_back_with_a_code_not_values(caplog):
    for env in ({"TRACECHECK_SCORING_CONFIG": "{not json"},
                {"TRACECHECK_SCORING_CONFIG": json.dumps({"weights": {}})},
                {"TRACECHECK_SCORING_CONFIG_PATH": "/nonexistent/scoring.json"}):
        cfg, src = server.load_scoring_config(env=env)
        assert src == "fallback" and cfg == FALLBACK
    assert all("scoring_config_error=" in r.getMessage() for r in caplog.records if "scoring_config" in r.getMessage())


def test_env_json_is_used_as_private(tmp_path):
    custom = json.loads(json.dumps(FALLBACK))
    custom["caps"]["loop"] = custom["caps"]["loop"] - 1
    cfg, src = server.load_scoring_config(env={"TRACECHECK_SCORING_CONFIG": json.dumps(custom)})
    assert src == "private" and cfg == custom
    f = tmp_path / "scoring.json"
    f.write_text(json.dumps(custom))
    cfg, src = server.load_scoring_config(env={"TRACECHECK_SCORING_CONFIG_PATH": str(f),
                                               "TRACECHECK_SCORING_CONFIG": json.dumps(FALLBACK)})
    assert src == "private" and cfg == custom  # the secret file wins over the env string


def test_health_exposes_source_never_values():
    from starlette.testclient import TestClient
    with TestClient(server.build_http_app()[0]) as client:
        body = client.get("/health").json()
    assert body["scoring_config"] == server.SCORING_CONFIG_SOURCE
    assert "weights" not in json.dumps(body) and "caps" not in json.dumps(body)


def test_no_private_scoring_files_are_tracked():
    try:
        tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    names = {pathlib.Path(t).name for t in tracked}
    assert not names & {"scoring-config.json", "scoring.json", "scoring-golden.json", "scoring-config.private.json"}
    for t in tracked:
        if t.endswith(".json"):
            text = (ROOT / t).read_text(errors="ignore")
            assert "PRIVATE TraceCheck scoring" not in text, t


@requires_private
def test_public_fallback_differs_from_private_config():
    assert server.SC != FALLBACK
    for section in ("weights", "caps", "efficiency", "steps", "text", "statuses", "words"):
        assert server.SC[section] != FALLBACK[section], section
    same = sum(1 for sec in ("steps", "text") for k in FALLBACK[sec] if FALLBACK[sec][k] == server.SC[sec][k])
    total = sum(len(FALLBACK[sec]) for sec in ("steps", "text"))
    assert same / total < 0.5, "fallback is too close to the private config"


@requires_private
def test_private_config_reproduces_golden_results():
    gp = _golden_path()
    if gp is None or not gp.exists():
        pytest.skip("private golden file not available")
    doc = json.loads(gp.read_text())
    # Any change to the private config must come with a regenerated golden file (intentional calibration).
    assert hashlib.sha256(json.dumps(server.SC, sort_keys=True).encode()).hexdigest() == doc["config_sha256"]
    golden = doc["cases"]
    current = {n: scoring_cases.summarize(server.score_trace(*a)) for n, a in scoring_cases.cases().items()}
    assert set(golden) == set(current)
    for name in golden:
        assert current[name] == golden[name], name
