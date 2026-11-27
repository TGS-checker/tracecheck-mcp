# Expected behaviour — pod-2026-11-27-high-stakes

Pattern: praktijktest aandacht + 0.5.0 point 5 (and it should help point 4). An unrecovered error sits on a high-stakes step, then success is claimed. Optional per-step `stakes` (`low`/`normal`/`high`) is planned for 0.5.0; today's scorer may ignore it.

Pending 0.5.0:
- The high-stakes error should weigh more than the same error on a low-stakes step.
- Success claimed over an unresolved error should still be treated as a problem.
- Sub-scores (`goal_clarity`, `observability`, `efficiency`, and the others) should actually move with this trace — they must not be identical to the other fixtures in this folder (point 4). This trace is short and has no observe step, so observability should not look like a rich, well-observed run.

Do not invent an expected numeric score.
