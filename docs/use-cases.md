# What agents use TraceCheck for

Short, proven use cases, plus example scenarios from tested runs. These are illustrative scenarios, not customer stories.

## UC-001 — Missing verification before a success claim

Your agent claims success without checking that the result actually holds? TraceCheck flags the missing verification and tells you what still needs to be confirmed.

Example scenario (from a tested run):

A content agent generated and exported a file, then reported success without ever opening the result. TraceCheck flagged missing_verification and pointed to the exact check: "Open the produced output and confirm it matches the requested content and format." The agent can add that check as a verify step before it says done, so success means the output was actually looked at.

## UC-003 — Success on half the data

Example scenario (from a tested run):

A lookup agent hit an empty result, retried, still received only partial data, and then reported success anyway. TraceCheck flagged conclusion_on_incomplete_data and gave a clear fix: "Confirm the returned data is complete and matches the query. Until that check passes on complete data, report partial or failed, even when a retry returned some data." The agent now reports what it really has.

## UC-005 — The quiet downgrade

Example scenario (from a tested run):

A booking agent could not make the reservation as requested, switched to a fallback that quietly dropped part of the requirement, verified the result and reported full success. TraceCheck flagged requirement_lowered and advised: "Restore the original requirement, or get confirmation that the lower option is acceptable, and say which requirement changed." The user now hears about the downgrade instead of discovering it later.

## UC-006 — "Payment done" after the charge failed

Example scenario (from a tested run):

A payment agent saw its charge step fail, carried on, and reported success. TraceCheck flagged unrecovered_error and ungrounded_success, scoring the run 35/100, and gave a concrete fix: "Confirm the payment or order status with the provider. Only report success when that check passed; otherwise report partial or failed." That prevents a false success on money that never moved.
