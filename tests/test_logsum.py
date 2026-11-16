"""Tests for the logsum CLI, written from spec.md only.

Single-file suite: fixtures and helpers are defined here.
Assumes a POSIX environment, a non-root user, and the tool at src/logsum.py.
"""

import csv
import io
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
COMMAND = [sys.executable, str(ROOT / "src" / "logsum.py")]
SAMPLE = ROOT / "data" / "sample_events.csv"

IN_HEADER = "timestamp,level,service,message"
OUT_HEADER = ["service", "level", "count", "first_seen", "last_seen"]
OUT_HEADER_LINE = "service,level,count,first_seen,last_seen\n"
T = "2026-03-01T10:00:00Z"
GOOD = f"{T},INFO,api,ok"


# --------------------------------------------------------------------------
# fixtures / helpers
# --------------------------------------------------------------------------
@dataclass
class Run:
    code: int
    stdout: str
    stderr: str


class Workspace:
    """Scratch directory holding events.csv / summary.csv."""

    def __init__(self, root: Path):
        root.mkdir()
        self.root = root
        self.input = root / "events.csv"
        self.output = root / "summary.csv"

    # ---- building inputs (bytes, so BOM / CRLF / bad UTF-8 survive) -----
    def write(self, *rows, header=IN_HEADER, eol="\n", trailing_eol=True):
        text = eol.join([header, *rows]) + (eol if trailing_eol else "")
        return self.write_raw(text)

    def write_raw(self, content):
        data = content.encode("utf-8") if isinstance(content, str) else content
        self.input.write_bytes(data)
        return self.input

    # ---- running ---------------------------------------------------------
    def run(self, *args, cwd=None) -> Run:
        proc = subprocess.run(
            COMMAND + [str(a) for a in args],
            check=False, 
            cwd=str(cwd or self.root),
            capture_output=True,
            timeout=60,
        )
        return Run(
            proc.returncode,
            proc.stdout.decode("utf-8", errors="replace"),
            proc.stderr.decode("utf-8", errors="replace"),
        )

    def cli(self, *flags) -> Run:
        """Run with this workspace's INPUT and OUTPUT."""
        return self.run(self.input, self.output, *flags)

    # ---- inspecting outputs ---------------------------------------------
    def out_bytes(self) -> bytes:
        return self.output.read_bytes()

    def out_rows(self):
        text = self.out_bytes().decode("utf-8")
        return list(csv.reader(io.StringIO(text, newline="")))

    def summary(self):
        rows = self.out_rows()
        assert rows[0] == OUT_HEADER
        return {(r[0], r[1]): (int(r[2]), r[3], r[4]) for r in rows[1:]}

    def listing(self):
        return sorted(p.name for p in self.root.iterdir())


@pytest.fixture
def ws(tmp_path):
    return Workspace(tmp_path / "work")


def assert_ok(r):
    assert r.code == 0, r.stderr
    assert r.stdout == ""  # spec §7: nothing on stdout on success


def assert_fail(r, code):
    assert r.code == code, f"expected {code}, got {r.code}; stderr={r.stderr!r}"
    assert r.stderr.strip(), "an error message must be printed to stderr"
    assert "Traceback" not in r.stderr


def last_line(stderr):
    return [ln for ln in stderr.splitlines() if ln.strip()][-1]


def assert_skipped_count(stderr, n):
    assert re.search(rf"(?<!\d){n}(?!\d)", last_line(stderr)), stderr


def reported_skipped(stderr):
    """Skipped count from the final stderr line (0 if nothing was reported)."""
    lines = [ln for ln in stderr.splitlines() if ln.strip()]
    if not lines:
        return 0
    m = re.search(r"skipped\D{0,30}(\d+)|(\d+)\D{0,30}skipped", lines[-1], re.IGNORECASE)
    if m:
        return int(m.group(1) or m.group(2))
    nums = re.findall(r"\d+", lines[-1])
    return int(nums[0]) if nums else 0


def one(ws, ts):
    """Run a single-row file; return (first_seen, last_seen)."""
    ws.write(f"{ts},INFO,api,m")
    assert_ok(ws.cli())
    cnt, first, last = ws.summary()[("api", "INFO")]
    assert cnt == 1
    return first, last


# ==========================================================================
# §1 grouping
# ==========================================================================
def test_single_group_count_first_last(ws):
    ws.write(
        "2026-03-01T10:00:00Z,INFO,api,a",
        "2026-03-01T12:00:00Z,INFO,api,b",
        "2026-03-01T11:00:00Z,INFO,api,c",
    )
    assert_ok(ws.cli())
    assert ws.summary() == {
        ("api", "INFO"): (3, "2026-03-01T10:00:00Z", "2026-03-01T12:00:00Z")
    }


def test_distinct_groups_by_service_and_level(ws):
    ws.write(
        "2026-03-01T10:00:00Z,INFO,api,a",
        "2026-03-01T10:00:01Z,ERROR,api,a",
        "2026-03-01T10:00:02Z,INFO,web,a",
        "2026-03-01T10:00:03Z,INFO,api,a",
    )
    assert_ok(ws.cli())
    assert ws.summary() == {
        ("api", "INFO"): (2, "2026-03-01T10:00:00Z", "2026-03-01T10:00:03Z"),
        ("api", "ERROR"): (1, "2026-03-01T10:00:01Z", "2026-03-01T10:00:01Z"),
        ("web", "INFO"): (1, "2026-03-01T10:00:02Z", "2026-03-01T10:00:02Z"),
    }


def test_rows_pooled_across_file_no_time_bucketing(ws):
    ws.write(
        "2026-01-01T00:00:00Z,INFO,api,a",
        "2026-06-15T13:00:00Z,INFO,api,a",
        "2026-12-31T23:59:59Z,INFO,api,a",
    )
    assert_ok(ws.cli())
    assert ws.summary() == {
        ("api", "INFO"): (3, "2026-01-01T00:00:00Z", "2026-12-31T23:59:59Z")
    }


def test_group_key_uses_normalised_values(ws):
    ws.write(
        "2026-03-01T10:00:00Z,info,API,a",
        "2026-03-01T10:00:01Z,Info,Api,a",
        "2026-03-01T10:00:02Z,  INFO ,  api  ,a",
    )
    assert_ok(ws.cli())
    assert ws.summary() == {
        ("api", "INFO"): (3, "2026-03-01T10:00:00Z", "2026-03-01T10:00:02Z")
    }


def test_message_is_ignored(ws):
    ws.write(
        "2026-03-01T10:00:00Z,INFO,api,",
        '2026-03-01T10:00:01Z,INFO,api,"has, comma"',
        "2026-03-01T10:00:02Z,INFO,api,different text",
        '2026-03-01T10:00:03Z,INFO,api,"multi\nline"',
    )
    assert_ok(ws.cli())
    assert ws.summary() == {
        ("api", "INFO"): (4, "2026-03-01T10:00:00Z", "2026-03-01T10:00:03Z")
    }


def test_rows_sorted_by_service_then_level_in_plain_string_order(ws):
    ws.write(
        "2026-03-01T10:00:00Z,INFO,svc2,m",
        "2026-03-01T10:00:00Z,INFO,svc10,m",
        "2026-03-01T10:00:00Z,WARN,api,m",
        "2026-03-01T10:00:00Z,ERROR,api,m",
        "2026-03-01T10:00:00Z,DEBUG,api,m",
        "2026-03-01T10:00:00Z,INFO,api_x,m",
        "2026-03-01T10:00:00Z,INFO,api2,m",
        "2026-03-01T10:00:00Z,INFO,api-x,m",
        "2026-03-01T10:00:00Z,INFO,Zed,m",
        "2026-03-01T10:00:00Z,INFO,b,m",
        "2026-03-01T10:00:00Z,DEBUG,b,m",
    )
    assert_ok(ws.cli())
    keys = [(r[0], r[1]) for r in ws.out_rows()[1:]]
    assert keys == [
        ("api", "DEBUG"),
        ("api", "ERROR"),
        ("api", "WARN"),
        ("api-x", "INFO"),
        ("api2", "INFO"),
        ("api_x", "INFO"),
        ("b", "DEBUG"),
        ("b", "INFO"),
        ("svc10", "INFO"),
        ("svc2", "INFO"),
        ("zed", "INFO"),
    ]


def test_sort_is_independent_of_input_order(ws):
    rows = [f"{T},{lvl},{svc},m" for svc in ("b", "a") for lvl in ("WARN", "ERROR")]
    ws.write(*rows)
    assert_ok(ws.cli())
    keys = [(r[0], r[1]) for r in ws.out_rows()[1:]]
    assert keys == [("a", "ERROR"), ("a", "WARN"), ("b", "ERROR"), ("b", "WARN")]


# ==========================================================================
# Duplicates (implementation notes)
# ==========================================================================
def test_identical_rows_are_all_counted_without_warning(ws):
    ws.write(GOOD, GOOD, GOOD, f"{T},info, API ,ok")
    r = ws.cli()
    assert_ok(r)
    assert ws.summary() == {("api", "INFO"): (4, T, T)}
    assert "warn" not in r.stderr.lower()


def test_duplicates_do_not_trip_strict(ws):
    ws.write(GOOD, GOOD)
    assert_ok(ws.cli("--strict"))
    assert ws.summary()[("api", "INFO")][0] == 2


# ==========================================================================
# §2 normalisation
# ==========================================================================
def test_trims_whitespace_incl_tabs_on_all_three_fields(ws):
    ws.write(" \t2026-03-01T10:00:00Z \t,\t warn \t,\t Web\t ,m")
    assert_ok(ws.cli())
    assert ws.summary() == {("web", "WARN"): (1, T, T)}


def test_internal_whitespace_is_preserved(ws):
    ws.write(f"{T},INFO,my svc,m")
    assert_ok(ws.cli())
    assert ("my svc", "INFO") in ws.summary()


def test_level_uppercased_service_lowercased(ws):
    ws.write(f"{T},wArN,SeRvIcE,m")
    assert_ok(ws.cli())
    assert list(ws.summary()) == [("service", "WARN")]


def test_utf8_passthrough(ws):
    ws.write(f"{T},INFO,café,mensaje ñ ✓")
    assert_ok(ws.cli())
    assert ("café", "INFO") in ws.summary()


# ==========================================================================
# §3 timestamps: truncation, offsets, UTC output
# ==========================================================================
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2026-03-01T10:00:00Z", "2026-03-01T10:00:00Z"),
        ("2026-03-01T10:00:00", "2026-03-01T10:00:00Z"),
        ("2026-03-01T10:00:00+00:00", "2026-03-01T10:00:00Z"),
        ("2026-03-01T10:00:00-00:00", "2026-03-01T10:00:00Z"),
        ("2026-03-01T12:00:00+02:00", "2026-03-01T10:00:00Z"),
        ("2026-03-01T08:00:00-02:00", "2026-03-01T10:00:00Z"),
        ("2026-03-01T03:00:00+05:30", "2026-02-28T21:30:00Z"),
        ("2026-12-31T23:30:00-01:00", "2027-01-01T00:30:00Z"),
        ("2026-03-01T00:30:00+01:00", "2026-02-28T23:30:00Z"),
        ("2024-03-01T00:30:00+01:00", "2024-02-29T23:30:00Z"),
        ("2026-03-01T10:00:00+23:59", "2026-02-28T10:01:00Z"),
        ("2026-03-01T10:00:00-23:59", "2026-03-02T09:59:00Z"),
        ("2026-03-01T00:00:00Z", "2026-03-01T00:00:00Z"),
        ("2026-03-01T23:59:59Z", "2026-03-01T23:59:59Z"),
        ("2024-02-29T12:00:00Z", "2024-02-29T12:00:00Z"),
        ("2000-02-29T12:00:00Z", "2000-02-29T12:00:00Z"),
    ],
)
def test_timestamp_converted_to_utc_and_formatted(ws, raw, expected):
    assert one(ws, raw) == (expected, expected)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2026-03-01T10:00:00.0Z", "2026-03-01T10:00:00Z"),
        ("2026-03-01T10:00:00.999Z", "2026-03-01T10:00:00Z"),
        ("2026-03-01T10:00:00.999999999", "2026-03-01T10:00:00Z"),
        ("2026-03-01T23:59:59.999999Z", "2026-03-01T23:59:59Z"),
        ("2026-03-01T10:00:00.5+02:00", "2026-03-01T08:00:00Z"),
        ("2026-03-01T23:59:59.9-01:00", "2026-03-02T00:59:59Z"),
    ],
)
def test_fraction_is_truncated_not_rounded(ws, raw, expected):
    assert one(ws, raw) == (expected, expected)


def test_offset_less_timestamp_is_not_shifted(ws):
    ws.write(
        "2026-03-01T10:00:00,INFO,api,m",
        "2026-03-01T10:00:00Z,INFO,api,m",
        "2026-03-01T10:00:00+00:00,INFO,api,m",
    )
    assert_ok(ws.cli())
    assert ws.summary() == {("api", "INFO"): (3, T, T)}


def test_first_last_compared_after_utc_conversion(ws):
    # 10:00+02:00 is 08:00Z: earlier than 09:00Z although its text sorts later.
    ws.write(
        "2026-03-01T09:00:00Z,INFO,api,m",
        "2026-03-01T10:00:00+02:00,INFO,api,m",
    )
    assert_ok(ws.cli())
    assert ws.summary()[("api", "INFO")] == (
        2,
        "2026-03-01T08:00:00Z",
        "2026-03-01T09:00:00Z",
    )


def test_utc_comparison_across_day_boundary(ws):
    ws.write(
        "2026-03-02T01:00:00Z,INFO,api,m",
        "2026-03-01T23:00:00-05:00,INFO,api,m",  # = 2026-03-02T04:00:00Z
    )
    assert_ok(ws.cli())
    assert ws.summary()[("api", "INFO")] == (
        2,
        "2026-03-02T01:00:00Z",
        "2026-03-02T04:00:00Z",
    )


def test_timestamp_whitespace_trimmed(ws):
    assert one(ws, "  2026-03-01T10:00:00Z  ") == (T, T)


def test_quoted_timestamp_is_accepted(ws):
    ws.write(f'"{T}",INFO,api,m')
    assert_ok(ws.cli())
    assert ws.summary() == {("api", "INFO"): (1, T, T)}


# ==========================================================================
# §4 missing level / service
# ==========================================================================
def test_empty_level_becomes_unknown(ws):
    ws.write(f"{T},,api,m")
    r = ws.cli()
    assert_ok(r)
    assert ws.summary() == {("api", "UNKNOWN"): (1, T, T)}
    assert "warn" not in r.stderr.lower()


def test_whitespace_only_level_becomes_unknown(ws):
    ws.write(f"{T},  \t ,api,m")
    assert_ok(ws.cli())
    assert list(ws.summary()) == [("api", "UNKNOWN")]


def test_empty_service_becomes_unknown(ws):
    ws.write(f"{T},INFO,,m", f"{T},INFO,   ,m")
    assert_ok(ws.cli())
    assert ws.summary() == {("unknown", "INFO"): (2, T, T)}


def test_both_missing(ws):
    ws.write(f"{T},,,m")
    assert_ok(ws.cli())
    assert ws.summary() == {("unknown", "UNKNOWN"): (1, T, T)}


def test_explicit_unknown_values_merge_with_missing(ws):
    ws.write(
        f"{T},,,m",
        f"{T},unknown,unknown,m",
        f"{T},UNKNOWN,UNKNOWN,m",
        f"{T},Unknown,Unknown,m",
        f"{T}, ,  ,m",
    )
    assert_ok(ws.cli())
    assert ws.summary() == {("unknown", "UNKNOWN"): (5, T, T)}


def test_missing_values_are_not_malformed_under_strict(ws):
    ws.write(f"{T},,,m")
    assert_ok(ws.cli("--strict"))
    assert ws.summary() == {("unknown", "UNKNOWN"): (1, T, T)}


def test_quoted_empty_level_becomes_unknown(ws):
    ws.write(f'{T},"",api,m')
    assert_ok(ws.cli())
    assert list(ws.summary()) == [("api", "UNKNOWN")]


# ==========================================================================
# §5 malformed rows
# ==========================================================================
BAD_TIMESTAMPS = [
    "",
    "   ",
    "not-a-timestamp",
    "1772359200",
    "2026-03-01",
    "2026-03-01T10:00",
    "2026-03-01 10:00:00",
    "2026-03-01t10:00:00",
    "2026-03-01T10:00:00z",
    "2026-03-01T10:00:00+0100",
    "2026-03-01T10:00:00+01",
    "2026-03-01T10:00:00+1:00",
    "2026-03-01T10:00:00+01:00:00",
    "2026-03-01T10:00:00+01:00Z",
    "2026-03-01T10:00:00ZZ",
    "2026-03-01T10:00:00 Z",
    "2026-03-01T10:00:00.",
    "2026-03-01T10:00:00.Z",
    "2026-3-1T10:00:00",
    "26-03-01T10:00:00",
    "２０２６-03-01T10:00:00Z",  # full-width digits
    "2026-13-01T10:00:00Z",
    "2026-00-10T10:00:00Z",
    "2026-03-00T10:00:00Z",
    "2026-03-32T10:00:00Z",
    "2026-04-31T10:00:00Z",
    "2026-02-29T10:00:00Z",  # 2026 is not a leap year
    "2100-02-29T10:00:00Z",  # 2100 is not a leap year
    "2026-03-01T24:00:00Z",
    "2026-03-01T25:00:00",
    "2026-03-01T10:60:00Z",
    "2026-03-01T10:00:60Z",
    "2026-03-01T10:00:00+24:00",
    "2026-03-01T10:00:00-24:00",
    "2026-03-01T10:00:00+05:60",
    "2026-03-01T10:00:00.5+24:00",
]


@pytest.mark.parametrize("bad", BAD_TIMESTAMPS, ids=repr)
def test_bad_timestamp_row_is_skipped_with_warning(ws, bad):
    ws.write(GOOD, f"{bad},INFO,api,m", f"{bad},ERROR,other,m")
    r = ws.cli()
    assert r.code == 0, r.stderr
    assert r.stdout == ""
    # bad rows count toward no group
    assert ws.summary() == {("api", "INFO"): (1, T, T)}
    assert r.stderr.strip()
    assert "Traceback" not in r.stderr
    assert_skipped_count(r.stderr, 2)


@pytest.mark.parametrize("bad", BAD_TIMESTAMPS, ids=repr)
def test_bad_timestamp_row_fails_strict(ws, bad):
    ws.write(GOOD, f"{bad},INFO,api,m")
    r = ws.cli("--strict")
    assert_fail(r, 4)
    assert not ws.output.exists()


@pytest.mark.parametrize(
    "ts",
    [
        "2026-02-28T23:59:59Z",
        "2024-02-29T23:59:59Z",
        "2026-12-31T23:59:59-23:59",
        "2026-03-01T00:00:00+23:59",
    ],
)
def test_boundary_values_that_are_valid(ws, ts):
    ws.write(f"{ts},INFO,api,m")
    assert_ok(ws.cli("--strict"))


@pytest.mark.parametrize(
    "row",
    [
        f"{T},INFO,api",  # too few
        f"{T},INFO",
        f"{T}",
        f"{T},INFO,api,m,extra",  # too many
        f'{T},INFO,api,m,"x,y"',  # correctly quoted extra field
        "   ",  # whitespace-only line is not a blank line: 1 field
        ",,",
    ],
    ids=repr,
)
def test_wrong_field_count_is_malformed_and_skipped(ws, row):
    ws.write(GOOD, row)
    r = ws.cli()
    assert r.code == 0, r.stderr
    assert ws.summary() == {("api", "INFO"): (1, T, T)}
    assert_skipped_count(r.stderr, 1)


@pytest.mark.parametrize(
    "row", [f"{T},INFO,api", f"{T},INFO,api,m,extra", "   "], ids=repr
)
def test_wrong_field_count_fails_strict(ws, row):
    ws.write(GOOD, row)
    assert_fail(ws.cli("--strict"), 4)
    assert not ws.output.exists()


def test_field_count_is_compared_with_header_column_count(ws):
    header = "id,timestamp,level,service,message,extra"
    ws.write(f"1,{T},INFO,api,m,x", f"2,{T},INFO,api,m", header=header)
    r = ws.cli()
    assert r.code == 0
    assert ws.summary() == {("api", "INFO"): (1, T, T)}
    assert_skipped_count(r.stderr, 1)


def test_quoted_commas_count_as_one_field(ws):
    ws.write(f'{T},INFO,"svc,with,commas","msg, with, commas"')
    assert_ok(ws.cli("--strict"))
    assert ws.summary() == {("svc,with,commas", "INFO"): (1, T, T)}


def test_summary_reports_total_skipped_count_at_end(ws):
    ws.write(
        GOOD,
        "bad1,INFO,api,m",
        GOOD,
        "bad2,INFO,api,m",
        f"{T},INFO",
        "bad3,INFO,api,m",
        GOOD,
    )
    r = ws.cli()
    assert r.code == 0
    assert ws.summary() == {("api", "INFO"): (3, T, T)}
    assert_skipped_count(r.stderr, 4)
    # 4 warnings + the total
    assert len([ln for ln in r.stderr.splitlines() if ln.strip()]) >= 5


def test_each_warning_identifies_its_row(ws):
    rows = [GOOD] * 10  # file lines 2..11
    rows.append("BADTS-123,INFO,api,m")  # file line 12 / data row 11
    rows += [GOOD] * 2
    rows.append("short,row")  # file line 15 / data row 14
    ws.write(*rows)
    r = ws.cli()
    assert r.code == 0
    assert "BADTS-123" in r.stderr or re.search(r"(?<!\d)(12|11)(?!\d)", r.stderr)
    assert "short,row" in r.stderr or re.search(r"(?<!\d)(15|14)(?!\d)", r.stderr)


def test_all_rows_malformed_gives_header_only_output(ws):
    ws.write("bad,INFO,api,m", ",INFO,api,m", f"{T},INFO")
    r = ws.cli()
    assert r.code == 0, r.stderr
    assert ws.out_bytes() == OUT_HEADER_LINE.encode()
    assert_skipped_count(r.stderr, 3)


def test_strict_stops_at_first_malformed_row(ws):
    ws.write(GOOD, "BAD-FIRST,INFO,api,m", GOOD, "BAD-SECOND,INFO,api,m")
    r = ws.cli("--strict")
    assert_fail(r, 4)
    assert "BAD-SECOND" not in r.stderr
    assert not ws.output.exists()


def test_strict_with_all_valid_rows_behaves_like_default(ws):
    ws.write(GOOD, f"{T},ERROR,web,m")
    assert_ok(ws.cli("--strict"))
    strict_out = ws.out_bytes()
    ws.output.unlink()
    assert_ok(ws.cli())
    assert ws.out_bytes() == strict_out


# ==========================================================================
# Input file rules: BOM, quoting, blank lines, header handling
# ==========================================================================
def test_leading_bom_is_accepted(ws):
    ws.write_raw(b"\xef\xbb\xbf" + f"{IN_HEADER}\n{GOOD}\n".encode())
    assert_ok(ws.cli())
    assert ws.summary() == {("api", "INFO"): (1, T, T)}
    assert not ws.out_bytes().startswith(b"\xef\xbb\xbf")


def test_quoted_field_with_embedded_line_break(ws):
    ws.write(f'{T},INFO,api,"line1\nline2"', f"{T},INFO,api,m")
    assert_ok(ws.cli("--strict"))
    assert ws.summary() == {("api", "INFO"): (2, T, T)}


def test_quoted_field_with_crlf_inside(ws):
    ws.write_raw(f'{IN_HEADER}\r\n{T},INFO,api,"a\r\nb"\r\n{GOOD}\r\n')
    assert_ok(ws.cli("--strict"))
    assert ws.summary() == {("api", "INFO"): (2, T, T)}


def test_blank_line_inside_quoted_field_is_part_of_field(ws):
    ws.write(f'{T},INFO,api,"para1\n\npara2"', GOOD)
    assert_ok(ws.cli("--strict"))
    assert ws.summary() == {("api", "INFO"): (2, T, T)}


def test_escaped_double_quotes_in_input(ws):
    ws.write(f'{T},INFO,api,"she said ""hi"""', f'{T},"IN""FO",api,m')
    assert_ok(ws.cli("--strict"))
    assert ws.summary() == {("api", "INFO"): (1, T, T), ("api", 'IN"FO'): (1, T, T)}


def test_quoted_values_are_trimmed_and_normalised(ws):
    ws.write(f'{T},"  warn ","  API  ",m')
    assert_ok(ws.cli())
    assert list(ws.summary()) == [("api", "WARN")]


def test_blank_lines_are_ignored_everywhere(ws):
    ws.write_raw(f"\n\n{IN_HEADER}\n\n{GOOD}\n\n\n{GOOD}\n\n")
    assert_ok(ws.cli("--strict"))
    assert ws.summary() == {("api", "INFO"): (2, T, T)}


def test_blank_crlf_lines_are_ignored(ws):
    ws.write_raw(f"\r\n{IN_HEADER}\r\n\r\n{GOOD}\r\n\r\n")
    assert_ok(ws.cli("--strict"))
    assert ws.summary() == {("api", "INFO"): (1, T, T)}


def test_crlf_line_endings(ws):
    ws.write(GOOD, f"{T},ERROR,web,m", eol="\r\n")
    assert_ok(ws.cli("--strict"))
    assert set(ws.summary()) == {("api", "INFO"), ("web", "ERROR")}
    assert b"\r" not in ws.out_bytes()


def test_no_trailing_newline(ws):
    ws.write(GOOD, trailing_eol=False)
    assert_ok(ws.cli())
    assert ws.summary() == {("api", "INFO"): (1, T, T)}


def test_header_is_first_non_blank_line_and_trimmed(ws):
    ws.write(GOOD, header="  timestamp , level\t,service ,  message ")
    assert_ok(ws.cli())
    assert ws.summary() == {("api", "INFO"): (1, T, T)}


def test_column_order_does_not_matter(ws):
    ws.write(f"ok,api,INFO,{T}", header="message,service,level,timestamp")
    assert_ok(ws.cli())
    assert ws.summary() == {("api", "INFO"): (1, T, T)}


def test_extra_columns_are_ignored(ws):
    ws.write(f"1,{T},INFO,api,m,zzz", header="id,timestamp,level,service,message,extra")
    assert_ok(ws.cli())
    assert ws.summary() == {("api", "INFO"): (1, T, T)}


def test_duplicate_extra_column_is_allowed(ws):
    # Inferred: only *required* column names may not repeat (§6).
    ws.write(f"{T},INFO,api,m,a,b", header=IN_HEADER + ",x,x")
    assert_ok(ws.cli())


# ==========================================================================
# Output file format
# ==========================================================================
def test_output_exact_bytes(ws):
    ws.write(
        "2026-03-01T10:00:00Z,error,api,m",
        "2026-03-01T11:30:00+01:00,ERROR,API,m",
        "2026-03-01T09:00:00.9,info,web,m",
    )
    assert_ok(ws.cli())
    assert ws.out_bytes() == (
        b"service,level,count,first_seen,last_seen\n"
        b"api,ERROR,2,2026-03-01T10:00:00Z,2026-03-01T10:30:00Z\n"
        b"web,INFO,1,2026-03-01T09:00:00Z,2026-03-01T09:00:00Z\n"
    )


def test_output_has_no_bom_and_lf_line_endings_even_for_crlf_input(ws):
    ws.write(GOOD, eol="\r\n")
    assert_ok(ws.cli())
    data = ws.out_bytes()
    assert not data.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in data
    assert data.endswith(b"\n")


def test_output_fields_quoted_only_when_needed(ws):
    ws.write(
        f'{T},INFO,"a,b",m',
        f'{T},INFO,"say ""hi""",m',
        f'{T},INFO,"two\nlines",m',
        f'{T},"w,x",plain svc,m',
    )
    assert_ok(ws.cli())
    # sorted by service: "a,b" < "plain svc" < 'say "hi"' < "two\nlines"
    assert ws.out_bytes() == (
        "service,level,count,first_seen,last_seen\n"
        f'"a,b",INFO,1,{T},{T}\n'
        f'plain svc,"W,X",1,{T},{T}\n'
        f'"say ""hi""",INFO,1,{T},{T}\n'
        f'"two\nlines",INFO,1,{T},{T}\n'
    ).encode()


def test_output_utf8_without_ascii_escaping(ws):
    ws.write(f"{T},INFO,café,m")
    assert_ok(ws.cli())
    assert "café,INFO,1".encode() in ws.out_bytes()


def test_nothing_written_to_stdout_on_success(ws):
    ws.write(GOOD, "bad,INFO,api,m")
    r = ws.cli()
    assert r.code == 0
    assert r.stdout == ""


def test_no_temp_files_left_after_success(ws):
    ws.write(GOOD)
    assert_ok(ws.cli())
    assert ws.listing() == ["events.csv", "summary.csv"]


# ==========================================================================
# §6 empty but valid input
# ==========================================================================
def test_header_only_gives_header_only_summary(ws):
    ws.write()
    assert_ok(ws.cli())
    assert ws.out_bytes() == OUT_HEADER_LINE.encode()


def test_header_without_newline(ws):
    ws.write_raw(IN_HEADER)
    assert_ok(ws.cli())
    assert ws.out_bytes() == OUT_HEADER_LINE.encode()


def test_header_plus_only_blank_lines_is_empty_input(ws):
    ws.write_raw(f"{IN_HEADER}\n\n\n\r\n\n")
    assert_ok(ws.cli("--strict"))
    assert ws.out_bytes() == OUT_HEADER_LINE.encode()


# ==========================================================================
# §6 file-level failures (exit 3)
# ==========================================================================
def test_zero_byte_file(ws):
    ws.write_raw(b"")
    assert_fail(ws.cli(), 3)
    assert not ws.output.exists()


def test_file_with_only_blank_lines_has_no_header(ws):
    # Inferred: no header can be found, so required columns are missing.
    ws.write_raw("\n\n\r\n")
    assert_fail(ws.cli(), 3)
    assert not ws.output.exists()


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(
            IN_HEADER.encode()
            + b"\n"
            + GOOD.encode()[:-2]
            + b"\xff\n"
            + GOOD.encode()
            + b"\n",
            id="invalid-byte-in-message",
        ),
        pytest.param(
            IN_HEADER.encode() + b"\n" + b"2026-03-01T10:00:00Z,INFO,a\xc3\x28pi,m\n",
            id="bad-continuation-byte",
        ),
        pytest.param(
            IN_HEADER.encode() + b"\n" + GOOD.encode() + b"\n" + b"caf\xe9\n",
            id="latin1-at-end",
        ),
        pytest.param(
            IN_HEADER.encode() + b"\n" + b"bad\xff,INFO,api,m\n",
            id="invalid-utf8-in-otherwise-malformed-row",
        ),
        pytest.param((IN_HEADER + "\n" + GOOD + "\n").encode("utf-16"), id="utf-16-file"),
    ],
)
@pytest.mark.parametrize("flags", [(), ("--strict",)], ids=["default", "strict"])
def test_invalid_utf8(ws, content, flags):
    ws.write_raw(content)
    assert_fail(ws.cli(*flags), 3)
    assert not ws.output.exists()


@pytest.mark.parametrize("missing", ["timestamp", "level", "service", "message"])
def test_missing_required_column(ws, missing):
    cols = [c for c in IN_HEADER.split(",") if c != missing]
    ws.write(",".join(["x"] * len(cols)), header=",".join(cols))
    assert_fail(ws.cli(), 3)
    assert not ws.output.exists()


def test_missing_message_column_with_otherwise_valid_rows(ws):
    ws.write(f"{T},INFO,api", header="timestamp,level,service")
    assert_fail(ws.cli(), 3)


@pytest.mark.parametrize("col", ["timestamp", "level", "service", "message"])
def test_duplicate_required_column(ws, col):
    ws.write(f"{T},INFO,api,m,x", header=f"{IN_HEADER},{col}")
    assert_fail(ws.cli(), 3)
    assert not ws.output.exists()


def test_duplicate_required_column_after_trimming(ws):
    ws.write(f"{T},INFO,api,m,x", header=f"{IN_HEADER}, level ")
    assert_fail(ws.cli(), 3)


@pytest.mark.parametrize(
    "header",
    [
        "Timestamp,level,service,message",
        "timestamp,LEVEL,service,message",
        "timestamp,level,Service,message",
        "timestamp,level,service,Message",
    ],
)
def test_header_names_are_case_sensitive(ws, header):
    ws.write(f"{T},INFO,api,m", header=header)
    assert_fail(ws.cli(), 3)


@pytest.mark.parametrize("flags", [(), ("--strict",)], ids=["default", "strict"])
@pytest.mark.parametrize(
    "tail",
    [
        pytest.param(
            f'{T},INFO,api,"never closed\n{GOOD}\n', id="unclosed-quote-merges-lines"
        ),
        pytest.param(f'{GOOD}\n{T},INFO,api,"never closed', id="unclosed-quote-at-eof"),
        pytest.param(f'{T},"INFO"x,api,m\n{GOOD}\n', id="chars-after-closing-quote"),
        pytest.param(f'{T},"INFO" ,api,m\n{GOOD}\n', id="space-after-closing-quote"),
        pytest.param(
            f'{GOOD}\n{GOOD}\n{T},INFO,"api"junk\n', id="junk-after-quote-in-last-row"
        ),
        pytest.param(f'{T},INFO,api,"abc"def\n', id="junk-after-quote-last-field"),
    ],
)
def test_improper_quoting_is_file_level_failure(ws, tail, flags):
    ws.write_raw(f"{IN_HEADER}\n{tail}")
    assert_fail(ws.cli(*flags), 3)
    assert not ws.output.exists()


def test_file_level_failure_takes_precedence_over_row_level(ws):
    ws.write_raw(f'{IN_HEADER}\nBAD,INFO,api,m\n{T},"INFO"x,api,m\n')
    assert_fail(ws.cli("--strict"), 3)
    assert_fail(ws.cli(), 3)
    ws.write_raw(IN_HEADER.encode() + b"\nBAD,INFO,api,m\n" + b"\xff\n")
    assert_fail(ws.cli("--strict"), 3)
    assert not ws.output.exists()


# ==========================================================================
# All-or-nothing output
# ==========================================================================
def _zero(ws):
    ws.write_raw(b"")
    return (), 3


def _bad_utf8(ws):
    ws.write_raw(IN_HEADER.encode() + b"\n" + GOOD.encode() + b"\n\xff\n")
    return (), 3


def _bad_quote(ws):
    ws.write_raw(f'{IN_HEADER}\n{GOOD}\n{T},"INFO"x,api,m\n')
    return (), 3


def _missing_col(ws):
    ws.write(f"{T},INFO,api", header="timestamp,level,service")
    return (), 3


def _strict_row(ws):
    ws.write(GOOD, "BAD,INFO,api,m")
    return ("--strict",), 4


FAILURES = [_zero, _bad_utf8, _bad_quote, _missing_col, _strict_row]


@pytest.mark.parametrize("make", FAILURES, ids=lambda f: f.__name__.strip("_"))
def test_failure_creates_no_output_and_leaves_no_files(ws, make):
    flags, code = make(ws)
    assert_fail(ws.cli(*flags), code)
    assert ws.listing() == ["events.csv"]


@pytest.mark.parametrize("make", FAILURES, ids=lambda f: f.__name__.strip("_"))
def test_failure_leaves_existing_output_untouched_even_with_force(ws, make):
    flags, code = make(ws)
    ws.output.write_bytes(b"precious existing content\n")
    assert_fail(ws.cli(*flags, "--force"), code)
    assert ws.out_bytes() == b"precious existing content\n"
    assert ws.listing() == ["events.csv", "summary.csv"]


# ==========================================================================
# §7 CLI: arguments, --force, I/O errors
# ==========================================================================
def test_cli_relative_paths_from_cwd(ws):
    ws.write(GOOD)
    assert_ok(ws.run("events.csv", "summary.csv", cwd=ws.root))
    assert ws.summary() == {("api", "INFO"): (1, T, T)}


@pytest.mark.parametrize(
    "flags",
    [
        ("--strict",),
        ("--force",),
        ("--strict", "--force"),
        ("--force", "--strict"),
    ],
)
def test_flags_after_positionals_in_either_order(ws, flags):
    # Usage: INPUT OUTPUT [--strict] [--force]
    ws.write(GOOD)
    assert_ok(ws.run(ws.input, ws.output, *flags))
    assert ws.summary() == {("api", "INFO"): (1, T, T)}


@pytest.mark.parametrize(
    "args",
    [
        pytest.param([], id="no-args"),
        pytest.param(["IN"], id="only-input"),
        pytest.param(["--strict"], id="only-flag"),
        pytest.param(["--strict", "--force"], id="only-flags"),
        pytest.param(["IN", "OUT", "--bogus"], id="unknown-flag"),
        pytest.param(["IN", "OUT", "EXTRA"], id="extra-positional"),
        pytest.param(["--bogus", "IN", "OUT"], id="unknown-flag-first"),
    ],
)
def test_usage_errors_exit_2(ws, args):
    ws.write(GOOD)
    resolved = [ws.input if a == "IN" else ws.output if a == "OUT" else a for a in args]
    assert_fail(ws.run(*resolved), 2)
    assert not ws.output.exists()


def test_usage_error_is_checked_before_missing_input(ws):
    assert_fail(ws.run(ws.root / "missing.csv", ws.output, "--bogus"), 2)


def test_usage_error_is_checked_before_same_file(ws):
    ws.write(GOOD)
    assert_fail(ws.run(ws.input, ws.input, "--bogus"), 2)


def test_missing_input_file_exit_1(ws):
    assert_fail(ws.run(ws.root / "nope.csv", ws.output), 1)
    assert not ws.output.exists()


def test_input_is_a_directory_exit_1(ws):
    assert_fail(ws.run(ws.root, ws.output), 1)
    assert not ws.output.exists()


def test_unreadable_input_exit_1(ws):
    ws.write(GOOD)
    ws.input.chmod(0o000)
    try:
        assert_fail(ws.cli(), 1)
    finally:
        ws.input.chmod(0o644)
    assert not ws.output.exists()


def test_existing_output_without_force_exit_1_and_unchanged(ws):
    ws.write(GOOD)
    ws.output.write_bytes(b"old\n")
    assert_fail(ws.cli(), 1)
    assert ws.out_bytes() == b"old\n"
    assert ws.listing() == ["events.csv", "summary.csv"]


def test_existing_output_with_force_is_replaced_completely(ws):
    ws.write(GOOD)
    ws.output.write_bytes(b"old content that is much longer than the new summary\n" * 20)
    assert_ok(ws.cli("--force"))
    assert ws.summary() == {("api", "INFO"): (1, T, T)}
    assert ws.out_bytes().count(b"old") == 0
    assert ws.listing() == ["events.csv", "summary.csv"]


def test_force_without_existing_output_just_writes(ws):
    ws.write(GOOD)
    assert_ok(ws.cli("--force"))
    assert ws.summary() == {("api", "INFO"): (1, T, T)}


def test_missing_parent_directory_exit_1_and_not_created(ws):
    ws.write(GOOD)
    out = ws.root / "no_such_dir" / "summary.csv"
    assert_fail(ws.run(ws.input, out), 1)
    assert not (ws.root / "no_such_dir").exists()
    assert_fail(ws.run(ws.input, out, "--force"), 1)
    assert not (ws.root / "no_such_dir").exists()


def test_output_is_a_directory_cannot_be_written(ws):
    ws.write(GOOD)
    ws.output.mkdir()
    assert_fail(ws.cli("--force"), 1)
    assert ws.output.is_dir()


@pytest.mark.parametrize("flags", [(), ("--force",)], ids=["no-force", "force"])
def test_output_same_path_as_input_exit_1(ws, flags):
    ws.write(GOOD)
    before = ws.input.read_bytes()
    assert_fail(ws.run(ws.input, ws.input, *flags), 1)
    assert ws.input.read_bytes() == before
    assert ws.listing() == ["events.csv"]


@pytest.mark.parametrize("flags", [(), ("--force",)], ids=["no-force", "force"])
def test_output_same_file_via_different_spelling_exit_1(ws, flags):
    ws.write(GOOD)
    before = ws.input.read_bytes()
    (ws.root / "sub").mkdir()
    sneaky = ws.root / "sub" / ".." / "events.csv"
    assert_fail(ws.run(ws.input, sneaky, *flags), 1)
    assert_fail(ws.run("events.csv", "./events.csv", *flags), 1)
    assert ws.input.read_bytes() == before


@pytest.mark.parametrize("flags", [(), ("--force",)], ids=["no-force", "force"])
def test_output_symlink_to_input_exit_1(ws, flags):
    ws.write(GOOD)
    before = ws.input.read_bytes()
    link = ws.root / "link.csv"
    os.symlink(ws.input, link)
    assert_fail(ws.run(ws.input, link, *flags), 1)
    assert ws.input.read_bytes() == before
    assert link.is_symlink()


# ---- order of checks (§7) --------------------------------------------------
def test_order_same_file_before_file_validation(ws):
    ws.write_raw(b"")  # would be exit 3 if validated first
    assert_fail(ws.run(ws.input, ws.input, "--force"), 1)


def test_order_existing_output_before_file_validation(ws):
    ws.write_raw(b"")  # zero bytes -> 3, but the output-exists check comes first
    ws.output.write_bytes(b"old\n")
    assert_fail(ws.cli(), 1)
    assert ws.out_bytes() == b"old\n"


def test_order_existing_output_before_strict_row_check(ws):
    ws.write(GOOD, "BAD,INFO,api,m")
    ws.output.write_bytes(b"old\n")
    assert_fail(ws.cli("--strict"), 1)
    assert ws.out_bytes() == b"old\n"


def test_order_file_validation_before_strict_row_processing(ws):
    ws.write_raw(f'{IN_HEADER}\nBAD,INFO,api,m\n{T},"INFO"x,api,m\n')
    assert_fail(ws.cli("--strict"), 3)


def test_order_file_validation_before_write_errors(ws):
    ws.write_raw(b"")
    out = ws.root / "no_such_dir" / "summary.csv"
    assert_fail(ws.run(ws.input, out), 3)


def test_order_row_processing_before_write_errors(ws):
    ws.write(GOOD, "BAD,INFO,api,m")
    out = ws.root / "no_such_dir" / "summary.csv"
    assert_fail(ws.run(ws.input, out, "--strict"), 4)
    assert not (ws.root / "no_such_dir").exists()


def test_strict_does_not_change_io_error_exit_code(ws):
    assert_fail(ws.run(ws.root / "nope.csv", ws.output, "--strict"), 1)
    ws.write(GOOD)
    ws.output.write_bytes(b"old\n")
    assert_fail(ws.cli("--strict"), 1)


# ==========================================================================
# data/sample_events.csv: content-independent smoke tests
# (the file is copied to a temp dir; the original is never touched)
# ==========================================================================
@pytest.fixture
def sample(ws):
    shutil.copyfile(SAMPLE, ws.input)
    return ws


def test_sample_file_summary_invariants(sample):
    r = sample.cli()
    assert_ok(r)
    assert sample.input.read_bytes() == SAMPLE.read_bytes()  # input untouched

    rows = sample.out_rows()
    assert rows[0] == OUT_HEADER
    body = rows[1:]

    keys = [(x[0], x[1]) for x in body]
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)

    stamp = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
    for service, level, count, first, last in body:
        assert service == service.strip().lower() and service != ""
        assert level == level.strip().upper() and level != ""
        assert int(count) >= 1
        assert stamp.fullmatch(first) and stamp.fullmatch(last)
        assert first <= last

    # every non-blank data record is either counted in a group or skipped
    text = SAMPLE.read_bytes().decode("utf-8-sig")
    records = [x for x in csv.reader(io.StringIO(text, newline="")) if x]
    data_rows = len(records) - 1
    total = sum(int(x[2]) for x in body)
    assert total + reported_skipped(r.stderr) == data_rows


def test_sample_file_strict_agrees_with_default_run(sample):
    r = sample.cli()
    assert_ok(r)
    skipped = reported_skipped(r.stderr)
    default_out = sample.out_bytes()
    sample.output.unlink()

    s = sample.cli("--strict")
    if skipped == 0:
        assert_ok(s)
        assert sample.out_bytes() == default_out
    else:
        assert_fail(s, 4)
        assert not sample.output.exists()
