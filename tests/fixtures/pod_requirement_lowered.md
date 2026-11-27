# Expected behaviour — pod-2026-11-27-requirement-lowered

Pattern: praktijktest aandacht + 0.5.0 point 3. A fallback succeeds only by lowering a requirement (optional privacy-light flag `requirement_lowered` on the fallback step, because step types alone cannot show that a constraint was dropped). Today's scorer may ignore the flag.

Pending 0.5.0:
- Flag `requirement_lowered`.
- Do **not** reward this fallback as successful error recovery.
- A verify of the lowered outcome does not erase the flag.

Do not invent an expected numeric score.
