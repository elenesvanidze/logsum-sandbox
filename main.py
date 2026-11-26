"""logsum T1: AC1 happy path only."""
import argparse
import csv
import io
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

HEADER = ["service", "level", "count", "first_seen", "last_seen"]


@dataclass(frozen=True)
class RawRecord:
    line_no: int  # provisional: csv.reader.line_num (T3 replaces)
    data_row: int
    fields: list


@dataclass(frozen=True)
class CheckedRow:
    service_raw: str
    level_raw: str
    utc: datetime


@dataclass(frozen=True)
class SummaryRow:
    service: str
    level: str
    count: int
    first_seen: str
    last_seen: str


def read_records(stream):
    reader = csv.reader(stream)
    header = next(r for r in reader if r)  # first non-blank line (T3 handles empty)
    index = {n.strip(): i for i, n in enumerate(header)}
    rows = (f for f in reader if f)  # skip blank lines
    return index, (RawRecord(reader.line_num, n, f) for n, f in enumerate(rows, 1))


def check_row(rec, index) -> CheckedRow:  # T2 replaces the body
    f = rec.fields
    dt = datetime.fromisoformat(f[index["timestamp"]].strip())
    dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    return CheckedRow(f[index["service"]], f[index["level"]], dt.replace(microsecond=0))


class Aggregator:
    def __init__(self):
        self._g = {}  # (service, level) -> (count, first, last)

    def add(self, key, utc):
        c, lo, hi = self._g.get(key, (0, utc, utc))
        self._g[key] = (c + 1, min(lo, utc), max(hi, utc))

    def finalize(self, min_count=1) -> list:
        f = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
        return [SummaryRow(s, l, c, f(a), f(b))
                for (s, l), (c, a, b) in sorted(self._g.items()) if c >= min_count]


def render(rows) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")  # QUOTE_MINIMAL = spec quoting
    w.writerow(HEADER)
    for r in rows:
        w.writerow([r.service, r.level, r.count, r.first_seen, r.last_seen])
    return buf.getvalue().encode("utf-8")


def write_output(path, content: bytes) -> None:  # T6 replaces the body
    Path(path).write_bytes(content)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="logsum")
    p.add_argument("input")
    p.add_argument("output")
    args = p.parse_args(argv)
    agg = Aggregator()
    with open(args.input, encoding="utf-8-sig", newline="") as stream:
        index, records = read_records(stream)
        for rec in records:
            row = check_row(rec, index)
            agg.add((row.service_raw.strip().lower() or "unknown",
                     row.level_raw.strip().upper() or "UNKNOWN"), row.utc)
    write_output(args.output, render(agg.finalize()))
    print("skipped: 0", file=sys.stderr)  # fixed at 0 until T4
    return 0


if __name__ == "__main__":
    sys.exit(main())