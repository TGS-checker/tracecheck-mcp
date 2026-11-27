# Expected behaviour — pod-20261127-1

Pattern: Scheduled batch work that ends partial: the remaining work is not handed over or scheduled in the trace.

Expected (0.5.1):
- Goal type is scheduled work (not booking).
- Codes: `missing_verification`, `partial_outcome_without_handover`.
- No errors: `error_recovery` is not applicable.

Do not invent an expected numeric score.
