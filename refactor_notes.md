## Removed by AI in the refactor

- `previous_line` variable name in `load_input`. It is renamed to `lines_consumed` in `_read_records`, because it holds the last line the reader has consumed. AI reason: the new name describes the value more clearly.
  My decision: keep removed.

- The inline body of `load_input`. The decoding, CSV reading, and header validation steps are split into `_decode`, `_read_records`, and `_check_header`, and `load_input` now calls them in order. AI reason: each function does one job, which makes the code easier to read. No logic was dropped, and the check order and error messages are unchanged.
  My decision: keep removed.
