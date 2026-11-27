"""Basic scoring: good vs bad traces, loop-detector regressions, malformed input."""

import server

# Built from the pod-geldverdiener tester report (v0.4.3 false positive / false negative).
PROGRESSING_SHELL_TRACE = [
    {"type": "action", "tool": "shell", "status": "ok"},
    {"type": "observe", "status": "ok"},
    {"type": "action", "tool": "shell", "status": "empty"},
    {"type": "observe", "status": "empty"},
    {"type": "action", "tool": "shell", "status": "ok"},
    {"type": "observe", "status": "ok"},
    {"type": "action", "tool": "shell", "status": "empty"},
    {"type": "observe", "status": "empty"},
    {"type": "action", "tool": "shell", "status": "ok"},
    {"type": "observe", "status": "ok"},
    {"type": "action", "tool": "shell", "status": "error"},
    {"type": "error", "tool": "shell", "status": "error"},
    {"type": "retry", "tool": "shell", "status": "ok"},
    {"type": "observe", "status": "ok"},
    {"type": "action", "tool": "search", "status": "ok"},
    {"type": "observe", "status": "ok"},
    {"type": "final"},
]
assert len(PROGRESSING_SHELL_TRACE) == 17

IDENTICAL_SHELL_ERROR_TRACE = (
    [{"type": "action", "tool": "shell", "status": "error"}] * 6
    + [{"type": "error"}]
)


def test_good_trace_scores_higher_than_bad_trace():
    example = server.TRACE_SCHEMA["example_call"]
    good = server.score_trace(example["goal"], example["trace"], example["outcome"])
    bad = server.score_trace(
        "do it",
        "1. tried\n2. error timeout",
        "success done",
    )

    assert "error" not in good
    assert "error" not in bad
    assert isinstance(good["score"], int)
    assert isinstance(bad["score"], int)
    assert 0 <= bad["score"] < good["score"] <= 100
    assert good.get("scoring_version") == server.SCORING_VERSION


def test_malformed_input_returns_clear_error_without_crashing():
    cases = [
        server.evaluate_trace(),
        server.evaluate_trace(goal="", trace="", outcome=""),
        server.score_trace("find the cheapest direct flight", {"not": "a trace"}, "success claimed"),
        server.score_trace("find the cheapest direct flight", 123, "success claimed"),
        server.score_trace("find the cheapest direct flight", "1. called search", None),
    ]
    for result in cases:
        assert isinstance(result, dict)
        assert "error" in result
        assert isinstance(result["error"], str) and result["error"].strip()
        assert "score" not in result or result["score"] is None


def test_progressing_shell_steps_are_not_a_loop():
    """17-step real flow: shell ok/empty/error with observes and status changes, retry succeeds, final, no verify."""
    result = server.score_trace("code change", PROGRESSING_SHELL_TRACE, "success")
    issues = " ".join(i["code"] for i in result["issues"])
    assert "loop_detected" not in issues
    loop = server.score_trace("code change", IDENTICAL_SHELL_ERROR_TRACE, "success")
    assert result["dimensions"]["efficiency"] > loop["dimensions"]["efficiency"]
    assert result["score"] > loop["score"]
    # Missing verify is still flagged; recovery of the error was real.
    assert result["dimensions"]["error_recovery"] > 0
    # Exact tuned numbers: tests/test_scoring_config.py (private golden file).


def test_identical_failing_shell_calls_are_a_loop():
    """6x identical shell error + an error, no final, outcome success: must count as a loop."""
    result = server.score_trace("code change", IDENTICAL_SHELL_ERROR_TRACE, "success")
    issues = " ".join(i["code"] for i in result["issues"])
    assert "loop_detected" in issues
    assert result["dimensions"]["efficiency"] < server.SC["efficiency"]["full"]
    assert result["dimensions"]["error_recovery"] == 0
    assert result["score"] <= server.SC["caps"]["ungrounded_success"]  # false-success cap kept
    assert any(i["code"] == "ungrounded_success" for i in result["issues"])



def test_two_shell_ok_without_label_is_not_a_loop():
    """Privacy-light cannot tell different shell commands apart; give the benefit of the doubt."""
    trace = [
        {"type": "action", "tool": "shell", "status": "ok"},
        {"type": "action", "tool": "shell", "status": "ok"},
        {"type": "final"},
    ]
    result = server.score_trace("code change", trace, "success")
    issues = " ".join(i["code"] for i in result["issues"])
    assert "loop_detected" not in issues
    assert "redundant" not in issues
    assert result["dimensions"]["efficiency"] == server.SC["efficiency"]["full"]


def test_same_label_and_status_is_a_real_repeat():
    trace = [
        {"type": "action", "tool": "shell", "status": "ok", "label": "install-deps"},
        {"type": "action", "tool": "shell", "status": "ok", "target_hash": "install-deps"},
        {"type": "final"},
    ]
    result = server.score_trace("code change", trace, "success")
    issues = " ".join(i["code"] for i in result["issues"])
    assert "loop_detected" in issues or "redundant" in issues
    assert result["dimensions"]["efficiency"] < server.SC["efficiency"]["full"]


def test_different_labels_are_not_a_loop():
    trace = [
        {"type": "action", "tool": "shell", "status": "ok", "label": "build"},
        {"type": "action", "tool": "shell", "status": "ok", "label": "test"},
        {"type": "final"},
    ]
    result = server.score_trace("code change", trace, "success")
    issues = " ".join(i["code"] for i in result["issues"])
    assert "loop_detected" not in issues
    assert "redundant" not in issues
