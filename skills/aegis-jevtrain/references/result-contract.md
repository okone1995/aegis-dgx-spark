# aegis-jevtrain result contract

## Codes (exit 2)

| code | When |
| --- | --- |
| `engine_not_found` | cannot locate the engine checkout |
| `operator_gate_not_satisfied` | `AEGIS_JEVTRAIN_OPERATOR` unset, or `--reviewer` differs |
| `queue_empty` | nothing to report/approve/export |
| `approve_refused` | engine refused (unknown id / already reviewed) |
| `export_refused` | no approved rows — unapproved rows never enter a dataset |
| `collect_failed` | run dir missing `flow.jsonl` or unreadable |

## Fields

- `report`: `total`, `review{pending,approved,rejected}`, `by_truth`, `judge_agreement
  {agree,disagree,no_judge}`, `false_positives`, `false_negatives`, plus the standing note
  **"队列 ≠ 已训练；只有 approved 才会进入导出"**.
- `export`: manifest (`schema=jevtrain-manifest-v1`) with `rows`, `labels`, per-file
  `sha256`, `queue_sha256`, `refused` (sealed-family rejections) and
  `balance_warnings`.
- `selftest`: engine root, queue path/stats, label policy (`hard_signals_only`), and a
  probe of the sealed-family guard.

## Calibers

- Judge verdicts are **advisory** inputs to the training record, never labels.
- Missing markers do not establish benign traffic. Attack probes remain `attack`; exploit outcome and label review are separate.
- `review-label` explicitly confirms/corrects a label with an evidence note; `approve` only approves the sample.
- Only complete versioned hunt exchanges are collected. Historical summaries are quarantined.
- A draft is not a model. Do not quote accuracy from a draft.

## Exit codes and refusals

- `0` = ran — including "ran and the answer is empty", which is a **legitimate** result;
- `2` = refused, and the JSON body carries a structured `code`. Argparse usage errors (for example a
  missing required `--reviewer`) *also* exit `2`, so **read the JSON `code`, not just the exit status**.

Codes added in this revision:

| code | When |
| --- | --- |
| `operator_reviewer_required` | the operator variable is set but `--reviewer` is empty/whitespace |
| `pairs_unavailable` | the engine in use has no pairing report (an older checkout) |

`approve_refused` now carries a `reason`:

| reason | Meaning |
| --- | --- |
| `no_hard_signal_label` | the row's `intent_label` is not `attack`/`benign` — a sample with no hard-signal label must never be approved |
| `not_a_disagreement` | `--only-disagreements` was passed but this row is not a judge disagreement |

## Field notes

- `selftest` returns `operator_gate_set` (whether `AEGIS_JEVTRAIN_OPERATOR` is set). Read-only commands
  (`selftest` / `report` / `pairs`) need no gate.
- `report`: `false_positives` / `false_negatives` list **at most 20 rows each** — they are samples, not
  the full set. Use the counts to judge scale.
- `MANIFEST.balance_warnings`: needs an engine from the same batch as this skill. On an older engine the
  field is absent — then check `stats.json` (label distribution) and the `pairing` block yourself.
  **Absent means "not computed", never "no problem".**
- `pairs` reports `families / paired_families / unpaired_only_attack / unpaired_only_benign`; a family
  listed as unpaired on one side is a label bias waiting to be trained in — pair it (or drop it) before
  exporting.
- Engine lookup order: `AEGIS_ENGINE_ROOT` first; if unset, walk up from the skill directory looking for a
  checkout that contains `engine/jevtrain.py`.
