# Expected behaviour — pod-2026-11-27-partial-success

Pattern: praktijktest aandacht + 0.5.0 point 1. A retry still ends in partial/empty, then the outcome claims success.

Pending 0.5.0:
- Raise issue `conclusion_on_incomplete_data`.
- `goal_alignment` should drop clearly; this must not look like a clean success.
- Status `partial` here is incomplete data after a retry, not a reason to ignore the success-claim problem.

Do not invent an expected numeric score.
