**Source file:** `CLAUDE.md`

## 1. Project Context
- The project is a tiny CLI tool that summarises synthetic `events.csv` logs.

## 2. Conventions
- Code lives in `src/`.
- Tests live in `tests/`.
- Data lives in `data/`.

## 3. Utilities to Prefer
- Use the Python 3.11 standard library. Avoid third-party packages unless approved.
- Use `ruff` for linting and formatting.
- Use `pytest` for testing.

## 4. Escalation Gates
- Stop and ask before adding any new dependencies.
- Use synthetic data only. Never use real or production data.
- Never overwrite `spec.md` after sign-off without asking first.
