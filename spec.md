## Overview
A small command-line tool that reads one synthetic events CSV file (for
example `events.csv`) and writes one summary CSV file (for example
`summary.csv`) with one row per event group.

Input columns: `timestamp`, `level`, `service`, `message`.
Output columns: `service`, `level`, `count`, `first_seen`, `last_seen`.

Input file rules:
- Encoding is UTF-8. A leading byte order mark is accepted.
- Fields follow standard CSV quoting: a field may be wrapped in double
quotes, and a literal double quote inside a quoted field is written as two
  double quotes. Quoted fields may contain commas and line breaks.
- Completely blank lines outside quoted fields are ignored. A blank line
  inside a quoted field is part of the field.
- The header is the first non-blank line. Header names are trimmed of
  surrounding whitespace and matched case-sensitively.
- Column order does not matter. Extra columns are ignored.
- `message` must exist as a column. Its values are never used.

## 1. Group key
`(service, level)`, using the normalised values from §2.
Rows from the whole file are pooled. There is no time bucketing.

## 2. Normalisation rules
- Trim leading and trailing whitespace on `service`, `level` and `timestamp`.
- Uppercase `level`.
- Lowercase `service`.
- Empty values are handled as described in §4.
- `timestamp` must be valid (§5) and is converted to UTC (§3).
- `message` is ignored.

## 3. Aggregates: count, first_seen, last_seen
Each output row has `count`, `first_seen` and `last_seen`.
- `count`: number of valid rows in the group.
- `count` is final per group. A `--min-count` filter (§7) is applied after
  all rows are pooled and counted. It never changes `count`, `first_seen` or
  `last_seen` of a group that is kept.
- `first_seen` / `last_seen`: earliest and latest timestamp among valid rows
  in the group.
- Timestamps have whole-second precision. A fractional part in the input is
  discarded (truncated, not rounded) before conversion, comparison and
  output.
- A timestamp with an explicit offset (`Z`, `+HH:MM` or `-HH:MM`) is
  converted to UTC before comparison and output.
- A timestamp without an offset is assumed to be UTC and written with `Z`
  without conversion.
- Output format of `first_seen` / `last_seen`: UTC with a trailing `Z` and
  no fractional part, for example `2026-03-01T10:00:00Z`.

## 4. Missing level or service
- An empty or whitespace-only `level` becomes `UNKNOWN`.
- An empty or whitespace-only `service` becomes `unknown`.
- The row is kept and counted. It is not malformed.
- A raw value that is already `unknown` (or `UNKNOWN` for `level`) is
  indistinguishable from a missing value and is grouped together with it.
  This is intentional.

## 5. Malformed rows
A valid `timestamp` (after trimming) has this form:
`YYYY-MM-DDTHH:MM:SS`, optionally followed by a fractional part and an
offset.
- Separator between date and time is an uppercase `T`.
- Fractional part (optional): a `.` followed by one or more digits. It is
  accepted and ignored (§3).
- Offset (optional): uppercase `Z`, or `+HH:MM` / `-HH:MM`, with a colon.
- The value must be a real calendar date and time: month 01-12, a day that
  exists in that month (leap years apply), hour 00-23, minute 00-59, second
  00-59. Offset hours are 00-23 and offset minutes 00-59.

A row is malformed if:
- its `timestamp` is empty or not valid as defined above, or
- it is correctly quoted but its number of fields differs from the number of
  header columns.

Improperly quoted input is not a malformed row. It is a file-level failure
(§6).

Handling:
- Default: skip the whole row (it counts toward no group), print a warning
  to stderr that identifies the row and gives the reason, and print the
  total skipped count to stderr at the end.
- With `--strict`: stop at the first malformed row, print the error to
  stderr, write no output, and exit with code 4.
- `--strict` affects only malformed rows. It has no effect on the file-level
  failures in §6.

## 6. Empty and invalid input
File-level failures. The error is printed to stderr, no output is written,
and the exit code is 3:
- The input file has zero bytes.
- The input file is not valid UTF-8.
- The header is missing one or more required columns.
- A required column name appears more than once in the header.
- The file is improperly quoted anywhere: a quoted field is not closed before
  the end of the file, or characters follow a closing quote other than a
  comma or line end. A bad quote can merge following lines into one field, so
  the row boundaries after it cannot be trusted.

File-level checks cover the whole file and take precedence over row-level
handling (§5).

Empty but valid input. A summary file containing only the header is written
and the exit code is 0:
- The input has a valid header and no data rows (blank lines do not count).
- Every data row is malformed and `--strict` is not set. The skipped count is
  still reported on stderr (§5).
- Every group is removed by `--min-count` (§7). Malformed rows are still
  handled as in §5.

## 7. CLI flags and exit codes
Usage: `<command> INPUT OUTPUT [--strict] [--force] [--min-count N]`
- `INPUT` (required, positional): path to the input CSV file.
- `OUTPUT` (required, positional): path where the summary CSV is written.
- `--strict`: fail on the first malformed row (§5).
- `--force`: allow replacing an existing `OUTPUT` file. It never allows
  `OUTPUT` to be the same file as `INPUT`.
- `--min-count N`: write only groups whose `count` (§3) is at least `N`.
  `N` must be a whole number of 1 or more, written with ASCII digits only.
  `0`, negative numbers, decimals, signs, spaces, non-ASCII digits, empty
  and missing values are usage errors. The default is 1, which writes every
  group. Malformed rows never count toward `N`. The filter does not change
  the skipped count reported on stderr (§5). Both `--min-count N` and
  `--min-count=N` are accepted.

Output file rules:
- Header: `service,level,count,first_seen,last_seen`.
- The header is always written, even when `--min-count` removes every group.
- Rows are sorted by `service`, then `level`, in plain string order.
- Encoding is UTF-8 without a byte order mark. Lines end with `\n`,
  including the last line.
- Fields use standard CSV quoting: a field is wrapped in double quotes only
  if it contains a comma, a double quote or a line break, and a double quote
  inside it is written as two double quotes.
- The parent directory of `OUTPUT` must already exist. The tool does not
  create it. If it is missing, the run fails (exit 1).
- If `OUTPUT` exists and `--force` is not given, the run fails (exit 1) and
  nothing is written.
- If `OUTPUT` resolves to the same file as `INPUT`, the run fails (exit 1)
  and nothing is written, regardless of `--force`.
- Output is all or nothing. If the run fails for any reason, no new file is
  created, no partial file is left behind, and an existing `OUTPUT` file
  (including one that `--force` would have replaced) is left unchanged.

Order of checks:
1. Arguments (exit 2), including an invalid `--min-count` value.
2. `OUTPUT` is the same file as `INPUT` (exit 1).
3. `INPUT` exists and is readable (exit 1).
4. `OUTPUT` exists without `--force` (exit 1).
5. File-level validation of the whole file (exit 3).
6. Row processing (exit 4 in `--strict` mode). The `--min-count` filter is
   then applied to the groups. It cannot fail.
7. Write output (exit 1 if it cannot be written).

On success, nothing is written to stdout. Warnings and the skipped count go
to stderr.

| Exit code | Meaning |
|---|---|
| 0 | Success, including empty but valid input (§6) |
| 1 | I/O error: `INPUT` does not exist or cannot be read, `OUTPUT` cannot be written (including a missing parent directory), `OUTPUT` exists without `--force`, or `OUTPUT` is the same file as `INPUT` |
| 2 | Usage error: `INPUT` or `OUTPUT` not provided, unknown arguments, or an invalid or missing `--min-count` value |
| 3 | File-level failure (§6): zero bytes, invalid UTF-8, missing or duplicated required columns, or improper quoting |
| 4 | Malformed row encountered in `--strict` mode (§5) |

## 8. Out of scope
- Grouping or analysing `message` content.
- Time bucketing (hourly, daily, and so on).
- Sub-second precision. Fractional seconds in timestamps are ignored.
- Time zone conversion beyond converting offset timestamps to UTC and
  assuming offset-less ones are UTC.
- Processing more than one input file, or a directory of files.
- Reading input from piped standard input (for example,
  `cat events.csv | <command>`).
- Writing output in any format other than the summary CSV, such as JSON,
  Excel, or Parquet.
- Input encodings other than UTF-8.

## Implementation notes

### Duplicate rows are not removed
The tool does not deduplicate. Every valid row is counted, so `count` (§3) is
the number of valid rows in the group, not the number of distinct events.

The input has no field that identifies an event, such as an event ID or a
sequence number. Rows with identical values can still be separate events, so
removing any of them would be a guess and could hide real activity.

- Identical rows, including rows that are identical after the §2
  normalisation, are all counted. They do not change `first_seen` or
  `last_seen`.
- They are not malformed (§5). They produce no warning and do not affect the
  skipped count, `--strict` or the exit code.

If the input later gains a column that identifies an event, deduplication can
be reconsidered as a new, separately signed-off change.

## Signed off
E.S. — 2026-11-17
