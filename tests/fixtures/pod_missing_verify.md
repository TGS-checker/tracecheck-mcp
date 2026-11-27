# Expected behaviour — pod-2026-11-27-missing-verify

Pattern: SUCCESS #1 from the 2026-11-27 praktijktest (pod-geldverdiener). A success is claimed and the run never verifies the result.

Pending 0.5.0 (and already expected on 0.4.5 for the core catch):
- Flag missing verification on a success claim.
- The fix should say *what* still needs to be confirmed for this goal type (content production / the exported result), not only a generic "add a verify step".
- This is a use-case candidate (UC-001).

Do not invent an expected numeric score. Privacy-light only; no raw content from the original tester trace.
