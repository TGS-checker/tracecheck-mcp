"""0.5.7: a stable verdict on every result (ok_to_report | report_partial | fix_first). Scores unchanged."""
import server

COORD = ("action:plan ok > action:delegate_build ok > action:delegate_qa ok > verify:tests passed > "
         "verify:lint passed > verify:build passed > verify:docs passed > verify:accessibility passed")
SIX = ["tests", "lint", "build", "docs", "accessibility", "security_scan"]


def _serve(trace, outcome, **kw):
    return server.serve_evaluation("code change", trace, outcome, agent_name="verdict-test", **kw)


def test_every_result_has_a_documented_verdict():
    for outcome, trace in (("success", "action:search ok > error > retry > verify passed > final"),
                           ("partial", "action:edit ok > verify failed > final"),
                           ("failed", "action:edit error > final")):
        r = _serve(trace, outcome)
        assert r["verdict"] in server.VERDICTS


def test_clean_high_score_success_is_ok_to_report():
    r = _serve(COORD + " > verify:security_scan passed > final", "success", expected_checks=SIX)
    assert r["score"] >= server.VERDICT_OK_MIN and not r["issues"] and r["verdict"] == "ok_to_report"


def test_honest_partial_is_report_partial_even_when_the_score_is_high():
    r = _serve("action:plan ok > action:build ok > verify:tests passed > verify:visual passed > final",
               "partial", expected_checks=["tests", "lint", "visual"], open_blockers=["lint"])
    assert r["checks"]["reported_open"] == ["lint"] and not r["issues"]
    assert r["verdict"] == "report_partial"
    # a missing check reported honestly as partial stays report_partial even when the score is high
    r2 = _serve(COORD + " > handoff:owner ok", "partial", expected_checks=SIX)
    assert r2["verdict"] == "report_partial"
    # the label does not flip to ok_to_report just because the number is high
    r2["score"] = 94
    assert server.verdict_for(94, r2["issues"], "partial") == "report_partial"


def test_success_with_open_checks_or_blockers_is_fix_first():
    missing = _serve(COORD + " > final", "success", expected_checks=SIX)
    assert "success_with_open_checks" in [i["code"] for i in missing["issues"]]
    assert missing["verdict"] == "fix_first"
    blocked = _serve("action:edit_file ok > verify:tests passed > final", "success", open_blockers=2)
    assert "success_with_open_blockers" in [i["code"] for i in blocked["issues"]]
    assert blocked["verdict"] == "fix_first"


def test_scope_exceeded_is_fix_first_even_in_a_higher_band():
    r = _serve("action:edit_file ok > action:deploy ok > verify:tests passed > final", "success",
               out_of_scope=["deploy"])
    assert r["issues"][0]["code"] == "scope_exceeded" and r["verdict"] == "fix_first"
    # a non-destructive out-of-scope tool may score in the 50-69 band; still fix_first
    light = _serve("action:search ok > action:send_email ok > verify passed > final", "success",
                   in_scope=["search"])
    assert light["issues"][0]["code"] == "scope_exceeded" and light["verdict"] == "fix_first"


def test_ungrounded_success_and_low_scores_are_fix_first():
    r = _serve("action:charge error > final", "success")
    assert "ungrounded_success" in [i["code"] for i in r["issues"]]
    assert r["score"] < 50 and r["verdict"] == "fix_first"
    # a looping, unrecovered failure (golden compact/identical-shell shape)
    low = server.serve_evaluation(
        "code change",
        "action:shell error > action:shell error > action:shell error > action:shell error > final",
        "success", agent_name="verdict-test")
    assert low["score"] < 50 and low["verdict"] == "fix_first"


def test_failed_check_is_fix_first():
    r = _serve(COORD + " > verify:security_scan failed > final", "success", expected_checks=SIX)
    assert "expected_check_failed" in [i["code"] for i in r["issues"]]
    assert r["verdict"] == "fix_first"


def test_small_gaps_on_a_success_claim_are_report_partial():
    r = _serve("action:edit_file ok > final", "success")  # no verify
    assert r["score"] >= 50 and "missing_verification" in [i["code"] for i in r["issues"]]
    assert r["verdict"] == "report_partial"


def test_schema_and_a2a_include_verdict():
    assert "verdict" in server.get_trace_schema()["returned_fields"]
    from starlette.testclient import TestClient
    c = TestClient(server.build_http_app()[0])
    msg = {"message": {"role": "ROLE_USER", "messageId": "1", "parts": [{"data": {
        "goal": "code change", "trace": COORD + " > verify:security_scan passed > final",
        "outcome": "success", "expected_checks": SIX, "agent_name": "verdict-test"}}]}}
    data = c.post("/a2a/message:send", json=msg).json()
    part = next(p["data"] for p in data["message"]["parts"] if "data" in p)
    assert part["verdict"] == "ok_to_report" and part["score"] >= 90
