# Expected behaviour — pod-20261127-2

Pattern: Media production: an approval request (hand-off) goes out before any verification.

Expected (0.5.1):
- Goal type is media (not other).
- Codes: `missing_verification`, `handoff_before_verification`.
- No errors: `error_recovery` is not applicable.

Do not invent an expected numeric score.
