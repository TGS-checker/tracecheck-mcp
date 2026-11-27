# Expected behaviour — pod-20261127-3

Pattern: Analysis claims success while a partial observation was never resolved, and results were written before the data was complete. The empty fetch was recovered by a fallback and a retry.

Expected (0.5.1):
- Codes: `missing_verification`, `unresolved_partial_observation`, `write_before_complete_data`.
- The successful fallback and retry count as recovery (not penalised).

Do not invent an expected numeric score.
