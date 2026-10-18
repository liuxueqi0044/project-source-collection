# Terra review: OpenClaw

Agent: Copernicus (`01a0a527-ef76-74e0-8847-528ca50942c5`)  
Mode: read-only explorer; no file edits, installs, fetch, push or workflow trigger.

## Baseline

- HEAD `6b4fd067f90fbad6ca7cfc0ac89befba9b3f6f62`, `main...origin/main`, clean.
- Remote `https://github.com/liuxueqi0044/xq-openclaw-windows-kit.git`.
- No extra AGENTS.md found.
- `Test-ProductPolicy.ps1` passed; all PowerShell files parsed. Locked Pester/PSScriptAnalyzer modules were not present, so full gate was not run locally.

## Key findings

- Reusable: fail-closed supply-chain lock; diagnostic allowlist/timeouts/redaction; deterministic allowlisted package; minimal-permission pinned CI actions; clear credential and unofficial-provider boundaries.
- P0: production diagnostic defaults to `../policy/diagnostic-command-policy.json`, but package allowlist and copy list omit that file. Delivered diagnostic will fail initialization.
- M6 uses a one-line fixture diagnostic rather than the production script, so tests do not catch the P0.
- M1/M2 tests use fake API, 15-byte fake installer and fake signature; valid logic tests, not real provenance evidence.
- No completed M9/M10 real VM/release evidence.
- Remote service remains outside existing SKU and lacks AI-specific permissions, audit, disconnect, rollback and evidence schema.

## Minimal remediation

- Package closure + production diagnostic startup test: 0.5 day.
- Production-tool M6/M7 coverage: 0.5-1 day.
- Repo-wide secret scan and fixture exception: 0.5 day.
- Real Windows VM/release gate: 1-2 days plus waits.

Exact line evidence is integrated into `outputs/agent-kits-tech/STAGE-A-TECH-REVIEW.md`.

