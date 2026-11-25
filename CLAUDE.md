## Project Context
Tiny CLI that summarises synthetic events.csv logs.

## Conventions
- Code lives in `src/`.
- Tests live in `tests/`.
- Data lives in `data/`.

## Utilities to Prefer
- Python 3.11 standard library (avoid third-party packages unless approved).
- `ruff` for linting and formatting.
- `pytest` for testing.

## Escalation Gates
- Stop and ask before adding any new dependencies.
- Use synthetic data only — never real or production data.
- Never overwrite `spec.md` after sign-off without asking first.
