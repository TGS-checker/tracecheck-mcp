# Expected behaviour — pod-2026-11-27-verify-kind

Pattern: praktijktest aandacht + 0.5.0 points 2 and 6. A verify step exists, but `verify_kind` (visual) does not fit a code-change goal. The field `verify_kind` is optional and planned for 0.5.0; today's scorer may ignore it.

Pending 0.5.0:
- Do **not** suggest "add verify" — a verify step is already there.
- The fix should name *what* ought to be verified for this goal type (for a code change: a test, script, or end-to-end check), not a generic verify reminder.
- A kind that does not fit the goal type should count less than a fitting kind (no public formula; just this direction).

Do not invent an expected numeric score.
