"""Basic scoring: a good trace outranks a bad one, and malformed input does not crash."""

import server


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
