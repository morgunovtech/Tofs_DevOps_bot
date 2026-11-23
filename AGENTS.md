# Working rules for agents

`CLAUDE.md` here and `~/CLAUDE.md` say the same; this file is for Codex and other agents.

- Every change goes through a branch and a PR with a «Проверено» section; no parallel PRs for alternative attempts; close what will not be merged.
- No twins: when rewriting something, delete the old version in the same PR.
- Never commit secrets (`.env`, tokens, the database). A leaked secret is revoked first; history is rewritten second.
- The bot's behaviour follows the shared principles in `docs/DEVELOPMENT.md` (icons, three bottom buttons, alert anatomy, notification policy): a message to the owner goes through `services/notifier.py`, its words through `services/humanize.py`.
- Docs are tested: `tests/test_docs.py` fails on a broken link, a screen the bot does not have, a token-shaped example or an `.env.example` default that differs from the code. Keep `README.md`, `README.ru.md` and `docs/FEATURES.md` in step.
- Add new bots, keys, servers, DNS records or hosted projects to the README table «What it creates outside the repo».
