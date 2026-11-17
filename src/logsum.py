"""Summarise a synthetic events CSV into one row per (service, level) group.

Usage: python -m src.logsum INPUT OUTPUT [--strict] [--force]

Behaviour is defined in spec.md; section marks (§n) below point there.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys
import uuid
from datetime import datetime, timedelta

EXIT_OK = 0
EXIT_IO = 1
EXIT_USAGE = 2
EXIT_FILE = 3
EXIT_MALFORMED = 4

REQUIRED_COLUMNS = ("timestamp", "level", "service", "message")
OUTPUT_HEADER = ("service", "level", "count", "first_seen", "last_seen")

# YYYY-MM-DDTHH:MM:SS[.fraction][Z|+HH:MM|-HH:MM]; ASCII digits only (§5).
_TIMESTAMP_RE = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})"
    r"T([0-9]{2}):([0-9]{2}):([0-9]{2})"
    r"(?:\.[0-9]+)?"
    r"(?:(Z)|([+-])([0-9]{2}):([0-9]{2}))?"
)


class FileLevelError(Exception):
    """The file as a whole is unusable (exit 3, §6)."""


class MalformedRowError(Exception):
    """A malformed row was met in --strict mode (exit 4, §5)."""


class OutputExistsError(Exception):
    """OUTPUT appeared after the early existence check (exit 1, §7)."""


def _show(value: str) -> str:
    """Return a short, ASCII-only repr that is safe to print to stderr."""
    shown = ascii(value)
    return shown if len(shown) <= 60 else shown[:57] + "..."


def parse_timestamp(text: str) -> datetime:
    """Parse an already-trimmed timestamp into a naive datetime in UTC.

    The fractional part is discarded (truncated). Raises ValueError with a
    human-readable reason if the timestamp is not valid (§5).
    """
    if not text:
        raise ValueError("empty timestamp")
    match = _TIMESTAMP_RE.fullmatch(text)
    if match is None:
        raise ValueError(f"invalid timestamp {_show(text)}")
    year, month, day, hour, minute, second = (
        int(part) for part in match.group(1, 2, 3, 4, 5, 6)
    )
    try:
        moment = datetime(year, month, day, hour, minute, second)
    except ValueError:
        raise ValueError(f"not a real date and time: {_show(text)}") from None
    sign = match.group(8)
    if sign is not None:
        offset_hours = int(match.group(9))
        offset_minutes = int(match.group(10))
        if offset_hours > 23 or offset_minutes > 59:
            raise ValueError(f"invalid offset in timestamp {_show(text)}")
        offset = timedelta(hours=offset_hours, minutes=offset_minutes)
        try:
            moment = moment - offset if sign == "+" else moment + offset
        except OverflowError:
            raise ValueError(
                f"out of range after UTC conversion: {_show(text)}"
            ) from None
    return moment


def format_timestamp(moment: datetime) -> str:
    """Format a naive UTC datetime as YYYY-MM-DDTHH:MM:SSZ (§3)."""
    return (
        f"{moment.year:04d}-{moment.month:02d}-{moment.day:02d}"
        f"T{moment.hour:02d}:{moment.minute:02d}:{moment.second:02d}Z"
    )


def _decode(raw: bytes) -> str:
    """Decode the file as UTF-8 (BOM allowed); raise FileLevelError if unusable."""
    if not raw:
        raise FileLevelError("input file is empty (zero bytes)")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FileLevelError(f"input file is not valid UTF-8: {exc}") from None


def _read_records(
    text: str,
) -> tuple[list[str] | None, list[tuple[int, int, list[str]]]]:
    """Split CSV text into (header or None, data rows).

    The first non-blank record is the header (names stripped). Each later
    non-blank record is returned as (first_line, last_line, fields); a record
    spans several lines when a quoted field contains newlines.
    """
    # Raise csv's 128 KiB field limit so a large valid field is not reported as
    # bad quoting. The limit is global, so it is restored in `finally`.
    previous_limit = csv.field_size_limit(2**31 - 1)
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    header: list[str] | None = None
    rows: list[tuple[int, int, list[str]]] = []
    lines_consumed = 0
    try:
        for fields in reader:
            first_line = lines_consumed + 1
            lines_consumed = reader.line_num
            if not fields:  # completely blank line outside quotes
                continue
            if header is None:
                header = [name.strip() for name in fields]
            else:
                rows.append((first_line, lines_consumed, fields))
    except csv.Error as exc:
        raise FileLevelError(
            f"improperly quoted input near line {reader.line_num}: {exc}"
        ) from None
    finally:
        csv.field_size_limit(previous_limit)
    return header, rows


def _check_header(header: list[str]) -> None:
    """Require every REQUIRED_COLUMNS name exactly once; raise FileLevelError."""
    missing = [name for name in REQUIRED_COLUMNS if name not in header]
    if missing:
        raise FileLevelError("header is missing column(s): " + ", ".join(missing))
    repeated = [name for name in REQUIRED_COLUMNS if header.count(name) > 1]
    if repeated:
        raise FileLevelError("header repeats column(s): " + ", ".join(repeated))


def load_input(raw: bytes) -> tuple[list[str], list[tuple[int, int, list[str]]]]:
    """Decode and parse the whole file; return (header, data rows).

    Each data row is (first_line, last_line, fields). Every file-level check
    (§6) happens here, before any row is judged, so that it takes precedence
    over row-level handling. Raises FileLevelError.
    """
    text = _decode(raw)
    header, rows = _read_records(text)
    if header is None:
        raise FileLevelError("header is missing: the file has no non-blank line")
    _check_header(header)
    return header, rows


def summarise(
    header: list[str],
    rows: list[tuple[int, int, list[str]]],
    strict: bool,
) -> tuple[dict, int]:
    """Group the data rows (§1-§5); return (groups, skipped_count).

    Each group maps (service, level) to [count, first_seen, last_seen].
    Every valid row is counted; rows are never deduplicated.
    Raises MalformedRowError on the first malformed row if strict is set.
    """
    time_at = header.index("timestamp")
    level_at = header.index("level")
    service_at = header.index("service")
    groups: dict[tuple[str, str], list] = {}
    skipped = 0
    for first_line, last_line, fields in rows:
        if first_line == last_line:
            where = f"line {first_line}"
        else:
            where = f"lines {first_line}-{last_line}"
        try:
            if len(fields) != len(header):
                raise ValueError(
                    f"expected {len(header)} fields but found {len(fields)}"
                )
            moment = parse_timestamp(fields[time_at].strip())
        except ValueError as exc:
            if strict:
                raise MalformedRowError(f"{where}: {exc}") from None
            skipped += 1
            print(
                f"warning: skipping malformed row at {where}: {exc}",
                file=sys.stderr,
            )
            continue
        service = fields[service_at].strip().lower() or "unknown"
        level = fields[level_at].strip().upper() or "UNKNOWN"
        entry = groups.get((service, level))
        if entry is None:
            groups[(service, level)] = [1, moment, moment]
        else:
            entry[0] += 1
            entry[1] = min(entry[1], moment)
            entry[2] = max(entry[2], moment)
    return groups, skipped


def _quote(field: str) -> str:
    """Quote a field only if it needs it (§7 output rules)."""
    if any(char in field for char in ',"\r\n'):
        return '"' + field.replace('"', '""') + '"'
    return field


def render(groups: dict) -> bytes:
    """Build the summary CSV: header plus rows sorted by (service, level)."""
    lines = [",".join(OUTPUT_HEADER)]
    for service, level in sorted(groups):
        count, first_seen, last_seen = groups[(service, level)]
        fields = (
            _quote(service),
            _quote(level),
            str(count),
            format_timestamp(first_seen),
            format_timestamp(last_seen),
        )
        lines.append(",".join(fields))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _remove_quietly(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _copy_mode(existing: str, temp_path: str) -> None:
    """Give the new file the permission bits of the OUTPUT it will replace."""
    try:
        os.chmod(temp_path, os.stat(existing).st_mode & 0o777)
    except OSError:
        pass  # OUTPUT does not exist yet: keep the default mode


def _create_new(temp_path: str, path: str, data: bytes) -> None:
    """Publish without ever replacing: raise OutputExistsError if path exists."""
    try:
        os.link(temp_path, path)  # atomic and complete; fails if path exists
        return
    except FileExistsError:
        raise OutputExistsError(path) from None
    except OSError:
        pass  # hard links unsupported here: fall back to exclusive create
    try:
        handle = open(path, "xb")  # noqa: SIM115 - handle is returned; caller closes it
    except FileExistsError:
        raise OutputExistsError(path) from None
    try:
        with handle:
            handle.write(data)
    except BaseException:
        _remove_quietly(path)
        raise


def write_output(path: str, data: bytes, force: bool) -> None:
    """Write all-or-nothing: temp file in the same directory, then publish it.

    Without force, OUTPUT is only ever created, never replaced, so a file that
    appears after the early check survives. With force, OUTPUT is replaced and
    the new file keeps the permission bits of the file it replaces.
    """
    directory = os.path.dirname(os.path.abspath(path))
    temp_path = os.path.join(directory, f".logsum-{uuid.uuid4().hex}.tmp")
    try:
        with open(temp_path, "xb") as handle:
            handle.write(data)
        if force:
            _copy_mode(path, temp_path)
            os.replace(temp_path, path)
        else:
            _create_new(temp_path, path, data)
    finally:
        _remove_quietly(temp_path)  # already gone after os.replace


def _same_file(first: str, second: str) -> bool:
    try:
        return os.path.samefile(first, second)
    except OSError:  # one of them does not exist, so they cannot be the same
        return False


def _fail(code: int, message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.logsum",
        description="Summarise an events CSV into one row per (service, level).",
        allow_abbrev=False,
    )
    parser.add_argument("input", metavar="INPUT", help="path to the input CSV file")
    parser.add_argument("output", metavar="OUTPUT", help="path for the summary CSV")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="stop at the first malformed row (exit 4) and write nothing",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace OUTPUT if it already exists (never if it is INPUT)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:  # argparse: 2 for usage errors, 0 for --help
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE

    if _same_file(args.input, args.output):
        return _fail(EXIT_IO, "OUTPUT is the same file as INPUT")
    try:
        with open(args.input, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        return _fail(EXIT_IO, f"cannot read INPUT: {exc}")
    exists_message = f"OUTPUT exists (use --force to replace it): {args.output}"
    if os.path.lexists(args.output) and not args.force:
        return _fail(EXIT_IO, exists_message)
    try:
        header, rows = load_input(raw)
    except FileLevelError as exc:
        return _fail(EXIT_FILE, str(exc))
    try:
        groups, skipped = summarise(header, rows, args.strict)
    except MalformedRowError as exc:
        return _fail(EXIT_MALFORMED, f"malformed row (--strict): {exc}")
    try:
        write_output(args.output, render(groups), args.force)
    except OutputExistsError:  # OUTPUT appeared after the early check
        return _fail(EXIT_IO, exists_message)
    except OSError as exc:
        return _fail(EXIT_IO, f"cannot write OUTPUT: {exc}")
    if not args.strict:
        print(f"skipped {skipped} malformed row(s)", file=sys.stderr)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
