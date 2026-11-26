#!/usr/bin/env bash
# Rule: a PR must not delete a test file unless a commit message in the PR
# has a line "DELETE_TESTS: <reason>" (reason must be non-empty).
# Exit 0 = rule satisfied, exit 1 = violated. Any git failure also exits non-zero.
set -euo pipefail

base="${1:-origin/main}"

# Test file = under tests/ or test/, or named test_*.py or *_test.py.
test_re='(^|/)tests?/|(^|/)test_[^/]*\.py$|_test\.py$'

# Step 1: list deleted files. Kept separate from grep so a git failure is not masked.
# -M means a renamed or moved file is not reported as a deletion.
all_deleted="$(git -c core.quotepath=off diff --name-only --diff-filter=D -M "${base}...HEAD")"
deleted="$(grep -E "$test_re" <<<"$all_deleted" || true)"

if [ -z "$deleted" ]; then
  echo "OK: no test files deleted."
  exit 0
fi

# Step 2: look for an acknowledgement line in the PR's commit messages.
messages="$(git log --format=%B "${base}..HEAD")"

if grep -Eq '^[[:space:]]*DELETE_TESTS:[[:space:]]*[^[:space:]]' <<<"$messages"; then
  echo "OK: test deletion acknowledged via DELETE_TESTS."
  echo "Deleted:"
  echo "$deleted"
  exit 0
fi

echo "::error::Test file(s) deleted without a 'DELETE_TESTS: <reason>' commit message line."
echo "Deleted:"
echo "$deleted"
echo "Add a line 'DELETE_TESTS: <reason>' to a commit message in this PR."
exit 1