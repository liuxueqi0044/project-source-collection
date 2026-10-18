# V5 source baseline

- Created: 2026-09-09
- Source project: `C:\Users\xiayu\Documents\Codex\2026-09-07\qq-codex-chatgpt-conversation-6a9b9c7e-9364\outputs\personal-messenger-ai`
- Architecture: `C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat\outputs\personal-messenger-ai-v5-multicontact-architecture.md`
- Source audit: `C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat\outputs\personal-messenger-ai-v5-source-audit.json`

Copied the source, tests, synthetic fixtures, certified compatibility profiles, rule packs, documentation, scripts, `pyproject.toml`, and README. Excluded runtime state, virtual environments, caches, compiled files, databases, environment files, credentials, keys, tokens, cookies, and account/chat data. The QQ compatibility profile was manually inspected and contains only hashed executable/system fingerprints and public client configuration; it contains no account identity or chat content.

The source directory remains unchanged. This repository is the implementation baseline for Personal Messenger AI V5.

`src/messenger_ai/observability/secrets.py` and `tests/observability/test_secrets.py` were restored from the source project after the B0 filename exclusion was identified. They contain DPAPI implementation code and synthetic tests only; no real key, credential, account, or chat data was copied.
