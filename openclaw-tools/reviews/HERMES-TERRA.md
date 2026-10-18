# Terra review: Hermes

Agent: Ptolemy (`01a0a527-f022-79a2-94cc-a3d04dc93653`)  
Mode: read-only explorer; no file edits, installs, fetch, push or workflow trigger.

## Baseline

- HEAD `88747d400ddd662c20883ad5227677a3b1a32a57`, `main...origin/main`, clean.
- `core.autocrlf=true`; two commits.
- No extra AGENTS.md found.

## Key findings

- Reusable: formal validation rejects draft/zero/floating refs; robust package security gate; Draft Release governance.
- P0: manifest remains draft with zero commits/hashes and DRAFT waiver; formal validation fails seven checks.
- CI policy locally returns 35 checks / 2 failures because a CRLF-sensitive regex consumes the newline after `uses:`; browser confirms the current workflow stops at this step.
- Signature waiver can be any nonempty string and is not bound to approver/reason/expiry/artifact hash.
- User guide says `-OutputPath`; diagnostic script accepts `-OutputDirectory`.
- “diagnostic does not use network” is overstated because the wrapper invokes upstream `hermes doctor` without an outbound block.
- M1-M10 tests mainly use fake network/Defender/VM evidence and a 10-byte fake PE; old 113/113 output is gitignored and not HEAD-bound.
- `remoteBasicInstall` is only human-assisted scope text, not a remote tool or AI delivery implementation.

## Minimal remediation

- CI CRLF regex + LF/CRLF tests: 0.5 hour.
- Diagnostic docs/wording: 0.25 hour.
- Strong waiver and real formal lock: 0.5-1 day.
- Real Windows VM: 1-2 days plus waits.
- Shared remote-assistance design: 2-5 days before VM/pilot evidence.

Exact line evidence is integrated into `outputs/agent-kits-tech/STAGE-A-TECH-REVIEW.md`.
