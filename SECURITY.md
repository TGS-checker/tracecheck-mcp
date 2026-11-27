# Security policy

## Reporting
Please report vulnerabilities privately via GitHub Security Advisories on this repository ("Report a vulnerability"), not in public issues.

## What TraceCheck does to stay safe
- **No content stored:** no goal, trace or outcome text, no tool names, no personal data, no raw IP addresses. See [SPEC.md](SPEC.md#6-privacy).
- **Read-only, offline scoring:** the tools have no side effects and make no outbound calls with your input.
- **Input limits:** 200 steps, 20000 trace characters, 500 characters for goal/outcome, 256 KB body. Oversized input gets a helpful `input_too_large` / `body_too_large` error.
- **Rate limits:** per agent and global, in memory. Exceeding them returns `rate_limited` with a retry time.
- **Strict validation:** wrong types get `malformed_input` with a working example; optional fields with bad values are ignored and explained.
- **Minimal echo:** error bodies repeat only what is needed (e.g. a truncated unknown path).
- **No secrets in code or logs.** Secret scanning and push protection are enabled on this repository; Dependabot alerts are on.
- **Public data is aggregate only:** `data/serving.json` holds aggregated fix rankings and benchmarks above a minimum n. A test fails if it ever contains agent ids, profiles, IP hashes or raw events.

## Supported versions
Only the latest release (see `/health`) is supported.
