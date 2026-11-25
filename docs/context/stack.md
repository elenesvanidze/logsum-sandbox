# Stack reference

Source: repo files read on 2026-11-25. Items marked (unverified) were
not confirmed against the full source. Confirm them before relying on them.

## Language
Python 3.11 (pinned in `.github/workflows/ci.yml`).
Standard library only. Dependencies need approval (see hot file).

## Framework
None. Plain `argparse` command-line tool. `main(argv)` returns an exit
code, which the `__main__` guard passes to `sys.exit`. (unverified)

## Build tool
None found. No `pyproject.toml`, `requirements.txt` or `setup.py` was
seen in the repo root. Run as a module from the repo root:
`python -m src.logsum INPUT OUTPUT [--strict] [--force] [--min-count N]`

## Test runner
pytest. Run `pytest -v` from the repo root. Tests are in
`tests/test_logsum.py`. Last recorded run (`test_notes.md`): 249 collected,
249 passed. Known caveat: `test_unreadable_input_exit_1` fails as root.

## Linter and formatter
ruff. CI runs `ruff check .`. `CLAUDE.md` also names ruff for formatting,
but CI does not run `ruff format`. No ruff config file was seen. (unverified)

## CI
GitHub Actions, `.github/workflows/ci.yml`. Runs on push and pull request,
ubuntu-latest, Python 3.11. Steps: install ruff and pytest (unpinned),
`ruff check .`, `pytest -v`.

## Architectural constraint: validate the whole file before writing output
`src/logsum.py` is a single module. Input is fully validated (file-level
checks, §6) before any row is judged, and output is written only after
processing succeeds. An existing OUTPUT is never left partly written.
(unverified: stage names and write mechanism are not confirmed against the
full source.)

## Exit codes
0 ok · 1 I/O · 2 usage · 3 file-level · 4 malformed row (`--strict`).
Defined in `src/logsum.py`. (unverified: values checked only on the first
lines of the file)

## Gotchas (unverified)
- The csv field-size limit is raised while reading and restored in `finally`.
- Duplicate rows are counted, not removed (spec "Implementation notes").