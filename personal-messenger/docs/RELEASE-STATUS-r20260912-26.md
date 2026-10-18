# Release status: r20260912-26

## Outcome

`r20260912-26` is frozen and installed in the QQ guest. It carries the accepted visual conversation-selection implementation and fixes byte-exact Windows backup writes by using `O_BINARY` plus a complete-write loop.

## Verification

- Full host test suite: `823 passed, 1 skipped`.
- Guest release installer: exit `0`, status `succeeded`.
- Canonical runtime config `--check`: exit `0` with no worker, API, WebUI, or QQ action.
- Latest runtime state: `graceful_stopped`; runtime and worker processes are not alive; WebUI is unreachable.
- Wheel SHA256: `D1F5BEC65ECB365E401ABCF1FE043D3DB6E88BE9CF686DC79F7946E4FF042322`.
- Manifest SHA256: `A17C5D43513ABCD17EAB75B4754939969D31B8E65D28128F8076A55F12A0854D`.

## Canonical config and rollback evidence

- Current canonical SHA256: `87a6de61967b234013c99dbbd25ab72e25fe65668e4f76646310c290e9cd02b6`.
- Exact pre-visual backup SHA256: `c9b6763fc40f428f6f32987c3691ff8917c8ef629a66efa714f667ffc5fe52b5`.
- Exact backup length: `10262` bytes.
- The earlier CRLF-expanded diagnostic backup is retained for forensic comparison; it is not the rollback source.

No reply generation, composer access, send-button action, or receipt was produced while building, installing, repairing the backup, or validating this release.

## Next gate

Do not start the persistent supervisor yet. The next allowed live gate is one explicit-binding, at-most-one-send QQ → DeepSeek → QQ run with prepare/commit/verify evidence and no automatic retry when outcome is uncertain.
