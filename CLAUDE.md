Python CLI that summarises synthetic events.csv logs.

## Rules
- Behaviour is defined by `spec.md`. Read the relevant § before changing logic.
- Use the test runner and linter named in `docs/context/stack.md`.
- Code lives in `src/`, tests in `tests/`, data in `data/`.
- Exit codes are a contract. Do not renumber or reuse them.
- Success writes nothing to stdout. Warnings go to stderr.
- Change behaviour and tests in the same change.
- Write tests from `spec.md`, not from reading `src/`.
- In a refactor, log removed or renamed code in `refactor_notes.md`. Leave "My decision" to the human.
- Ask before changing CI (`.github/workflows/ci.yml`).

## Escalation gates
- Stop and ask before adding any new dependencies.
- Use synthetic data only - never real or production data.
- Never overwrite `spec.md` after sign-off without asking first.