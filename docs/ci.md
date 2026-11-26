## test-deletion-guard

- Workflow: `.github/workflows/test-deletion-guard.yml`
- Script: `scripts/ci/check_test_deletion.sh`
- Trigger: `pull_request` against `main`

### Admin step: make the check required

GitHub (repo admin): **Settings → Branches → Branch protection rules → edit `main` → tick "Require status checks to pass before merging" → select `test-deletion-guard`.**

- The check only shows up in the selector after it has run once in the last 7 days. Open a throwaway PR first.
- If the repo uses Rulesets instead, go to **Settings → Rules → Rulesets → (ruleset for `main`) → "Require status checks to pass"**.
- GitLab: this workflow is GitHub Actions only. The script is portable, but it needs its own `.gitlab-ci.yml` job. The matching setting is **Settings → Merge requests → "Pipelines must succeed"**.

### Note for engineers

This gate fails a pull request against `main` that deletes a test file (anything under `tests/` or `test/`, or named `test_*.py` or `*_test.py`) unless a commit in the PR has a line `DELETE_TESTS: <reason>` with a non-empty reason. It exists for AI-assisted PRs because an assistant can turn a red build green by deleting the failing test, and in a large diff that deletion is easy to miss. The gate makes the deletion visible and makes someone state a reason. It does not catch tests that are weakened without being deleted (emptied bodies, removed assertions, added skips or xfails, loosened expected values). It also can't judge whether the stated reason is a good one, and it ignores a test file that is added and then deleted within the same PR. One limitation: the check reads the PR's commit messages, so a squash merge drops the `DELETE_TESTS:` line from `main`'s history unless you copy it into the squash commit message. The audit trail then lives only in the PR.