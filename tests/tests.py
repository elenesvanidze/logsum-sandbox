from __future__ import annotations

import importlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 11), reason="spec requires Python 3.11 or newer"
)

# Directory that contains main.py. Override with the LOGSUM_ROOT env var.
ROOT = Path(os.environ.get("LOGSUM_ROOT", Path(__file__).resolve().parents[1]))
MAIN_PY = ROOT / "main.py"

# FLAG: how the tool is started.
#   "import" (default): a launcher imports main.py and runs
#       sys.exit(main.main(argv)). This works whether or not main.py has an
#       `if __name__ == "__main__":` guard.
#   "script": runs `python main.py events.csv summary.csv`. This needs the guard.
MODE = os.environ.get("LOGSUM_MODE", "import")
_LAUNCHER = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "import main; sys.exit(main.main(sys.argv[2:]))"
)

HEADER_IN = "timestamp,level,service,message"
HEADER_OUT = "service,level,count,first_seen,last_seen"
T0 = "2026-03-01T10:00:00Z"
T1 = "2026-03-01T10:01:00Z"
T2 = "2026-03-01T10:02:00Z"


@pytest.fixture(scope="session", autouse=True)
def _main_py_exists():
    if not MAIN_PY.is_file():
        pytest.fail(
            f"main.py not found at {MAIN_PY}. Set LOGSUM_ROOT to the "
            "directory that contains main.py.",
            pytrace=False,
        )


@dataclass
class Run:
    code: int
    out: bytes
    err: bytes
    summary: bytes | None

    @property
    def summary_text(self) -> str:
        assert self.summary is not None, f"summary.csv was not created; stderr={self.err!r}"
        return self.summary.decode("utf-8")

    @property
    def err_lines(self) -> list[str]:
        text = self.err.decode("utf-8", errors="replace")
        if text == "":
            return []
        parts = text.split("\n")  # split on \n only, so a stray \r stays visible
        if parts[-1] == "":
            parts.pop()
        return parts


def csv_in(*rows: str) -> str:
    return "\n".join([HEADER_IN, *rows]) + "\n"


def table(*rows: str) -> str:
    return "\n".join([HEADER_OUT, *rows]) + "\n"


def has_num(line: str, n: int) -> bool:
    """Whole-number match, so that '10' or '100' cannot satisfy n == 0."""
    return re.search(rf"(?<![\w.]){n}(?![\w.])", line) is not None


def _command() -> list[str]:
    if MODE == "script":
        return [sys.executable, str(MAIN_PY), "events.csv", "summary.csv"]
    return [sys.executable, "-c", _LAUNCHER, str(ROOT), "events.csv", "summary.csv"]


def run_logsum(tmp_path: Path, content: str | bytes) -> Run:
    (tmp_path / "events.csv").write_bytes(
        content.encode("utf-8") if isinstance(content, str) else content
    )
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        _command(), cwd=tmp_path, env=env, capture_output=True, timeout=60
    )
    out_path = tmp_path / "summary.csv"
    return Run(
        code=proc.returncode,
        out=proc.stdout,
        err=proc.stderr,
        summary=out_path.read_bytes() if out_path.exists() else None,
    )


def assert_clean_run(r: Run) -> None:
    """Exit 0, empty stdout, and one stderr line that is the skipped count 0."""
    assert r.code == 0, r.err
    assert r.out == b""
    assert len(r.err_lines) == 1, r.err_lines
    assert has_num(r.err_lines[0], 0), r.err_lines[0]


# ==========================================================================
# AC1 positive cases
# ==========================================================================
def test_ac1_exact_spec_example(tmp_path):
    r = run_logsum(
        tmp_path,
        csv_in(
            f"{T0},error,Auth,a",
            "2026-03-01T10:05:00Z, ERROR ,auth,b",
            "2026-03-01T10:10:00Z,Error,  AUTH,c",
        ),
    )
    assert_clean_run(r)
    assert r.summary_text == table("auth,ERROR,3,2026-03-01T10:00:00Z,2026-03-01T10:10:00Z")


def test_ac1_count_reaches_two_digits(tmp_path):
    rows = [f"2026-03-01T10:{m:02d}:00Z,info,api,m{m}" for m in range(12)]
    r = run_logsum(tmp_path, csv_in(*rows))
    assert_clean_run(r)
    assert r.summary_text == table("api,INFO,12,2026-03-01T10:00:00Z,2026-03-01T10:11:00Z")


def test_ac1_message_is_ignored(tmp_path):
    r = run_logsum(
        tmp_path,
        csv_in(
            f"{T0},info,api,",
            f'{T1},info,api,"has, comma"',
            f'{T2},info,api,"two\nlines"',
        ),
    )
    assert_clean_run(r)
    assert r.summary_text == table("api,INFO,3,2026-03-01T10:00:00Z,2026-03-01T10:02:00Z")


def test_ac1_columns_reordered_and_extra_columns_ignored(tmp_path):
    r = run_logsum(
        tmp_path,
        "extra,service,message,timestamp,level\n"
        f"x,Auth,hello,{T0},error\n"
        f'y,auth,"hi, there",{T1},ERROR\n',
    )
    assert_clean_run(r)
    assert r.summary_text == table("auth,ERROR,2,2026-03-01T10:00:00Z,2026-03-01T10:01:00Z")


def test_ac1_header_names_are_trimmed(tmp_path):
    r = run_logsum(
        tmp_path,
        f" timestamp , level,service ,message\n{T0},info,api,a\n",
    )
    assert_clean_run(r)
    assert r.summary_text == table("api,INFO,1,2026-03-01T10:00:00Z,2026-03-01T10:00:00Z")


def test_ac1_leading_bom_on_input_is_accepted(tmp_path):
    r = run_logsum(tmp_path, b"\xef\xbb\xbf" + csv_in(f"{T0},info,api,a").encode())
    assert_clean_run(r)
    assert r.summary_text == table("api,INFO,1,2026-03-01T10:00:00Z,2026-03-01T10:00:00Z")


def test_ac1_blank_lines_outside_quotes_are_ignored(tmp_path):
    content = f"\n\n{HEADER_IN}\n\n{T0},info,api,a\n\n\n{T1},info,api,b\n\n"
    r = run_logsum(tmp_path, content)
    assert_clean_run(r)
    assert r.summary_text == table("api,INFO,2,2026-03-01T10:00:00Z,2026-03-01T10:01:00Z")


def test_ac1_blank_line_inside_quoted_field_is_part_of_the_field(tmp_path):
    r = run_logsum(tmp_path, f'{HEADER_IN}\n{T0},info,api,"a\n\nb"\n{T1},info,api,c\n')
    assert_clean_run(r)
    assert r.summary_text == table("api,INFO,2,2026-03-01T10:00:00Z,2026-03-01T10:01:00Z")


def test_ac1_quoted_service_with_comma_and_doubled_quote(tmp_path):
    r = run_logsum(
        tmp_path,
        csv_in(f'{T0},info,"a,b",x', f'{T0},info,"say ""hi""",x'),
    )
    assert_clean_run(r)
    # Output quoting rule (section 7): quote only when needed, double the quotes.
    assert r.summary_text == table(
        f'"a,b",INFO,1,{T0},{T0}',
        f'"say ""hi""",INFO,1,{T0},{T0}',
    )


def test_ac1_output_is_utf8_lf_no_bom_and_ends_with_newline(tmp_path):
    r = run_logsum(tmp_path, csv_in(f"{T0},info,api,a"))
    assert_clean_run(r)
    assert not r.summary.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in r.summary
    assert r.summary.endswith(b"\n")


def test_ac1_non_ascii_service_is_written_as_utf8(tmp_path):
    r = run_logsum(tmp_path, csv_in(f"{T0},info,Caf\u00c9,a"))
    assert_clean_run(r)
    assert r.summary == table(f"caf\u00e9,INFO,1,{T0},{T0}").encode("utf-8")


# ==========================================================================
# AC1 normalisation (section 2)
# ==========================================================================
def test_ac1_trim_uses_str_strip_so_unicode_whitespace_is_removed(tmp_path):
    r = run_logsum(
        tmp_path,
        csv_in(
            f"{T0},\u00a0error\u00a0,\u2003Auth\u2003,a",  # NBSP, EM SPACE
            f"{T1},ERROR,auth,b",
        ),
    )
    assert_clean_run(r)
    assert r.summary_text == table("auth,ERROR,2,2026-03-01T10:00:00Z,2026-03-01T10:01:00Z")


def test_ac1_only_the_ends_are_trimmed(tmp_path):
    r = run_logsum(tmp_path, csv_in(f"{T0},info, Api  Gateway ,a"))
    assert_clean_run(r)
    assert r.summary_text == table(f"api  gateway,INFO,1,{T0},{T0}")


def test_ac1_level_uses_str_upper(tmp_path):
    # "\u00df".upper() == "SS" in Python.
    r = run_logsum(tmp_path, csv_in(f"{T0},\u00df,api,a", f"{T1},ss,api,b"))
    assert_clean_run(r)
    assert r.summary_text == table("api,SS,2,2026-03-01T10:00:00Z,2026-03-01T10:01:00Z")


def test_ac1_service_uses_str_lower(tmp_path):
    # "\u0130".lower() == "i\u0307" (i plus combining dot) in Python.
    r = run_logsum(tmp_path, csv_in(f"{T0},info,\u0130stanbul,a"))
    assert_clean_run(r)
    assert r.summary_text == table(f"i\u0307stanbul,INFO,1,{T0},{T0}")


@pytest.mark.parametrize("blank", ["", "   ", "\u00a0\t"], ids=["empty", "spaces", "nbsp_tab"])
def test_ac1_missing_level_and_service_become_unknown(tmp_path, blank):
    r = run_logsum(tmp_path, csv_in(f"{T0},{blank},{blank},a"))
    assert_clean_run(r)  # the row is kept and is not malformed
    assert r.summary_text == table(f"unknown,UNKNOWN,1,{T0},{T0}")


def test_ac1_missing_values_group_with_literal_unknown(tmp_path):
    # Section 4: this merge is intentional.
    r = run_logsum(
        tmp_path,
        csv_in(f"{T0},,,a", f"{T1},   ,   ,b", f"{T2},unknown,UNKNOWN,c"),
    )
    assert_clean_run(r)
    assert r.summary_text == table("unknown,UNKNOWN,3,2026-03-01T10:00:00Z,2026-03-01T10:02:00Z")


def test_ac1_missing_level_only_and_missing_service_only_are_separate_groups(tmp_path):
    r = run_logsum(tmp_path, csv_in(f"{T0},,api,a", f"{T1},info,,b"))
    assert_clean_run(r)
    assert r.summary_text == table(
        f"api,UNKNOWN,1,{T0},{T0}",
        f"unknown,INFO,1,{T1},{T1}",
    )


# ==========================================================================
# AC1 negative / edge cases
# ==========================================================================
def test_ac1_different_services_or_levels_are_not_merged(tmp_path):
    r = run_logsum(
        tmp_path,
        csv_in(f"{T0},info,auth,a", f"{T1},info,auth2,b", f"{T2},warn,auth,c"),
    )
    assert_clean_run(r)
    assert r.summary_text == table(
        f"auth,INFO,1,{T0},{T0}",
        f"auth,WARN,1,{T2},{T2}",
        f"auth2,INFO,1,{T1},{T1}",
    )


def test_ac1_valid_header_and_no_rows_gives_header_only_output(tmp_path):
    # Section 6: empty but valid input is exit 0 with a header-only file.
    for content in (HEADER_IN + "\n", HEADER_IN + "\n\n\n", HEADER_IN):
        r = run_logsum(tmp_path, content)
        assert_clean_run(r)
        assert r.summary_text == table()
        (tmp_path / "summary.csv").unlink()


def test_ac1_header_names_are_case_sensitive(tmp_path):
    # FLAG: this is a section 6 failure (missing required column), not AC1.
    # It is here to guard the "which rows are counted" reading of AC1.
    r = run_logsum(tmp_path, f"Timestamp,level,service,message\n{T0},info,api,a\n")
    assert r.code == 3
    assert r.summary is None
    assert r.out == b""
    assert r.err != b""


def test_ac1_crlf_input_gives_same_result(tmp_path):
    # FLAG: the spec says "standard CSV quoting" but never mentions CRLF line
    # ends. Assumed accepted.
    r = run_logsum(tmp_path, csv_in(f"{T0},info,api,a").replace("\n", "\r\n"))
    assert_clean_run(r)
    assert r.summary_text == table("api,INFO,1,2026-03-01T10:00:00Z,2026-03-01T10:00:00Z")


def test_ac1_input_without_final_newline(tmp_path):
    # FLAG: spec does not mention a missing final newline. Assumed accepted.
    r = run_logsum(tmp_path, f"{HEADER_IN}\n{T0},info,api,a")
    assert_clean_run(r)
    assert r.summary_text == table("api,INFO,1,2026-03-01T10:00:00Z,2026-03-01T10:00:00Z")


# ==========================================================================
# In-process check through the public main() signature
# ==