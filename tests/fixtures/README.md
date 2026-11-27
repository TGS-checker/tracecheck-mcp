# POD practice-pattern fixtures (privacy-light)

Prepared 2026-11-27 for the 0.5.0 scoring work. These are **fixtures and expected-behaviour notes only**. Regression tests that load these files: `tests/test_pod_050.py` (scoring 0.5.0).

## Rules

- Privacy-light only: generic goal categories, step types + statuses, no content from the original tester traces.
- `trace_id` is fixture metadata. Strip it (and any other meta) before sending `goal` / `trace` / `outcome` to `evaluate_trace`.
- Optional 0.5.0 fields (`verify_kind`, `requirement_lowered`, `stakes`) may be ignored by 0.4.5; that is expected.
- Sidecars describe behaviour, not formulas. No weights, thresholds or detector internals. No invented scores.
- Point 4 of the praktijktest (sub-scores were identical on all five real traces) is checked **across** this set: after 0.5.0, `goal_clarity`, `observability` and `efficiency` must not be the same on every fixture.

## Files

| File | trace_id | Pattern |
|---|---|---|
| `pod_missing_verify.json` | `pod-2026-11-27-missing-verify` | SUCCESS #1: success claimed, no verification |
| `pod_partial_success.json` | `pod-2026-11-27-partial-success` | Point 1: partial/empty after retry, then success |
| `pod_verify_kind.json` | `pod-2026-11-27-verify-kind` | Points 2 and 6: verify present, kind does not fit; fix must be specific |
| `pod_requirement_lowered.json` | `pod-2026-11-27-requirement-lowered` | Point 3: fallback lowers a requirement, must not be rewarded as recovery |
| `pod_high_stakes.json` | `pod-2026-11-27-high-stakes` | Point 5 (and 4): high-stakes unrecovered error; sub-scores should move |

Each JSON has a sidecar `*.md` with the expected behaviour.

## Mapping to the staging CSV

The five published praktijktest scores (82/91/82/91/84, staging `practice-1`…`practice-5`) are **not** mapped to these fixtures. The original traces were not kept, so a score-to-pattern link would be invented. The SUCCESS #1 row and the aandacht-rows in `data/tracecheck-feedback-events-staging.csv` use the `trace_id`s above where the pattern is known.
