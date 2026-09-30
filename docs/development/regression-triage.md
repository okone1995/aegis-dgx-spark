# Full CPU regression triage

> This is the initial diagnostic run, not the final result. See regression-final.json for current counts. Missing static validators and historical metric summaries were restored. Public JSON assertions and private raw-data assertions are now separate; the latter explicitly skip when private evidence is absent. The fixed-port network test now injects a transport failure and runs offline. Label-revision reapproval was migrated and is included in the final run.

Run date: 2026-09-30 (Windows, Python 3.12.14, repository `.venv`).

## Scope and safety

Pytest collection found **387 tests** across `tests/` and the four `aegis-*` skill test directories, using `--import-mode=importlib` to allow their duplicate `test_bridge.py` module names.

Source inspection found one test that performs an actual TCP reachability request: `tests/test_replay.py::test_replay_result_error_on_unreachable`, which calls `replay_payload` against `127.0.0.1:59999`. It was collected but deselected so the regression run would not connect to a local service. The HTTP probe and judge HTTP tests create their own ephemeral loopback servers; the browser runtime acceptance invokes the offline Node harness without `--live`. No real target, Console, model, or external service was contacted.

The run also deselected `tests/test_submission_quality.py::test_label_review_is_separate_and_invalidates_old_approval` per the ongoing migration from the legacy reapproval call to `expected_label_revision`.

## Result

Command:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --import-mode=importlib `
  --deselect=tests/test_replay.py::test_replay_result_error_on_unreachable `
  --deselect=tests/test_submission_quality.py::test_label_review_is_separate_and_invalidates_old_approval `
  tests skills/aegis-evolve/tests skills/aegis-hunt/tests `
  skills/aegis-jevtrain/tests skills/aegis-self-repair/tests
```

Result: **362 passed, 8 failed, 15 skipped, 2 deselected**, with 16 subtests passed. The two deselections are the safety exclusion and the known API migration case above. The run emitted dependency deprecation warnings from FastAPI/Starlette; they did not cause failures.

## Failure triage

All eight failures are attributable to verification/evidence assets absent from this checkout. No engine behavior regression was isolated in this run.

| Tests | Classification | Evidence |
|---|---|---|
| `tests/test_claims.py::test_claims_all_traceable`, `test_outward_language_gate`, `test_outward_numbers_traceable`, `test_no_host_paths_in_tracked_files` | Missing verification tooling | Their subprocesses fail with `FileNotFoundError` because `bench/train/assert_claims.py`, `lint_language.py`, `check_docs_numbers.py`, and `desensitize.py` are absent. |
| `tests/test_claims.py::test_manifest_tracked_is_fresh` | Missing checkout manifest | `MANIFEST.tracked.txt` is absent, so the test cannot compare the checkout inventory to its manifest. |
| All three tests in `tests/test_demo_metrics.py` | Missing model/evidence manifest | `bench/results/demo_models.json` is absent. The `/api/demo/models` handler deliberately returns `{"models": [], "status": "unavailable"}` when that manifest is missing, before adding `metrics_three_layers`; the tests consequently cannot compare metric fields to their source files. |

These failures should be rerun after the omitted verification scripts, tracked-file manifest, and demo model/evidence manifest are restored. The test failures were left intact.

## Skips

Pytest reported 15 skips. The test tree declares data-dependent skips for unavailable judge/B-retention evidence, and platform/optional-tool skips for missing Node, non-`/proc` platforms, or optional web dependencies. The summary output for this run did not include per-test skip reasons, so no exact per-reason count is claimed here.
