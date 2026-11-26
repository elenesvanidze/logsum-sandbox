## Run
- Command: `pytest -v`
- Result: 249 collected, 249 passed in 9.03s. No failures, errors or skips.
- The tests are in `tests/test_logsum.py`. They were written from `spec.md`
  only, and `src/logsum.py` was not read while writing them.

## Failures
None. Nothing was classified as an implementation bug, test bug or spec
ambiguity. No failure was isolated, and no failure decision was made.

## Interpretations the tests assume
The spec doesn't state these cases directly. The tests encode my reading of
them, and all of these tests passed:
- A space after a closing quote (`"INFO" ,`) is a file-level failure (exit 3),
  from §6: "characters follow a closing quote other than a comma or line end".
- A whitespace-only line is not "completely blank", so it is a malformed
  one-field row.
- A duplicated non-required header column is allowed. The spec names only
  duplicated required columns.
- A file with only blank lines has no header, so it exits 3.
- Same-file detection covers `..` paths, `./` paths and symlinks. Hard links
  are not tested.

A pass shows that the implementation and the tests read the spec the same way
on the asserted behaviours. It does not confirm the spec owner's intent.

## Known limits
- The spec doesn't define the warning text. The tests check only that a
  warning contains the row's line number, data-row number or bad value, and
  that the last stderr line contains the skipped count.
- `test_unreadable_input_exit_1` would fail if run as root. It passed here.
- `data/sample_events.csv` is covered only by content-independent invariants.
