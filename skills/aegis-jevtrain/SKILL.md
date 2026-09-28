---
name: aegis-jevtrain
description: Turn real attack/benign exchanges into judge (JEV) training material — collect from a finished Aegis run or an external flow corpus, label by HARD SIGNALS, queue for human review, then export a trainable dataset draft (train/holdout + manifest + TRAINING.md) with sealed-family guards. Use when asked to "collect attack records", "把攻击记录存成训练材料", "给判官做数据集", or after a run worth learning from. Never let a model's own verdict become the label, and never export without human approval.
metadata:
  version: "0.1.0"
  tags: [security, judge, dataset, provenance, human-in-the-loop]
---

# aegis-jevtrain — judge training material, with the label discipline baked in

## 命令总表（可直接复制）

入口：`skills/aegis-jevtrain/scripts/aegis_jevtrain.py`（**桥接层**，操作员闸门与环境校验都在这里）。
引擎侧另有 `tools/_jevtrain_cli.py`（不过闸门，供本仓维护使用——**不要**拿它做人工批准）。
引擎定位顺序：先读 `AEGIS_ENGINE_ROOT`；未设则沿技能目录逐级向上找含 `engine/jevtrain.py` 的仓。

```bash
BR=skills/aegis-jevtrain/scripts/aegis_jevtrain.py
python $BR selftest                                  # 只读：环境/队列/引擎是否就位
python $BR report                                    # 只读：队列统计 + 判官分歧、误报、漏报
python $BR pairs                                     # 只读：攻良配对覆盖（未配对会训出偏标签的判官）
python $BR collect --run workspace/runs/run-XXXX      # 写：从一轮真机 run 收集（旁路，失败可见）
python $BR collect-flows --file s8/A1-flows.jsonl     # 写：从外部流语料收集
AEGIS_JEVTRAIN_OPERATOR="你的署名" python $BR approve --id <sample_id> --reviewer "你的署名"
AEGIS_JEVTRAIN_OPERATOR="你的署名" python $BR export --version v9-mine --reviewer "你的署名"
```

**闸门（三个条件缺一不可）**

1. 环境变量 `AEGIS_JEVTRAIN_OPERATOR` 必须设置；
2. `approve`/`export` 的 `--reviewer` **必填**且与其**逐字相同**（留空或写错 ⇒ `operator_gate_not_satisfied`）——
   这样 provenance 永远能落到"谁批的"；
3. 被批样本必须有**硬信号标签**（`attack`/`benign`），否则 `approve_refused` + `reason=no_hard_signal_label`；
   带 `--only-disagreements` 时，样本若并非判官分歧则拒批（`reason=not_a_disagreement`）。

**export 输出落点**：不填 `--out` 时写到 `dataset/judge_<version>/`，含 `train.jsonl`、`holdout.jsonl`、
`stats.json`、`MANIFEST.json`、`TRAINING.md`；同名目录会被**覆盖写**——要先确认里面没有要留的东西，
或改用 `--out` 指到别处。

**退出码口径**：`0` = 跑通（含"跑通但结论为空"这类**合法**结果）；`2` = 被拒，响应里有结构化 `code`；
argparse 的用法错误也返回 `2` —— **请以 JSON 里的 `code` 为准**，不要只看退出码。

**版本相关字段怎么读**

* `MANIFEST.json` 的 `balance_warnings` 需要与本技能同批的引擎；老引擎没有这个字段时，自己核
  `stats.json` 的标签分布与 `pairing` 块——**任一缺失都算"未计算"，不得当作"无问题"**；
* `report` 的 `false_positives` / `false_negatives` **最多各列 20 条**（是抽样列表，不是全集；看规模用计数）；
* `selftest` 会回 `operator_gate_set`（是否设了操作员变量）；只读动作（selftest/report/pairs）**不需要**闸门。


## The three rules that make this legitimate

1. **Labels come from hard signals only** — a response marker hit, a business gate, or a
   verification replay. A judge verdict is recorded (`judge.verdict`) but **never** used as
   the label; otherwise the model trains on its own opinion and grows more confident, not
   more correct.
2. **Sealed families never enter.** Every candidate passes `is_b_sealed` (sealed paths and
   family keys); sealed-family split assignment is **looked up, never re-split**, so no
   family leaks across train/holdout.
3. **Queue ≠ trained.** Export produces a *draft* (`judge_<version>/`) with a manifest and a
   `TRAINING.md`. Training and evaluation are separate steps; any published number must be
   split as zero-shot vs fine-tuned, and online/offline abstain bands must never be mixed.

## Operator setup (outside the agent task)

| Variable | Meaning |
| --- | --- |
| `AEGIS_ENGINE_ROOT` | engine checkout (contains `engine/jevtrain.py`) |
| `AEGIS_JEVCAND_QUEUE` | candidate queue path (default: `dataset/review_queue/jevtrain_candidates.jsonl`) |
| `AEGIS_JEVTRAIN_OPERATOR` | reviewer identity; `approve`/`export` require it, and `--reviewer` must match |

## Commands

| Command | Required | Gated | Notes |
| --- | --- | --- | --- |
| `selftest` | — | no | engine, queue, label-policy and B-seal probe |
| `collect --run <run_dir>` | `--run` | no | reads `flow.jsonl` + `judge.jsonl` of a finished run |
| `collect-flows --file <flows.jsonl>` | `--file` | no | external corpus (e.g. an audit's flow dump) |
| `report` | — | no | counts by truth label, judge agreement, and the **disagreements** (FP/FN) |
| `pairs` | — | no | per-family pairing: families with only one side would train a biased judge |
| `approve --id <sample_id>` | `--id`, `--reviewer` | **yes** | refuses samples with no hard-signal label |
| `export --version <v>` | `--version`, `--reviewer` | **yes** | `--only-approved` is implicit: unapproved rows never export |

`export` writes `<out>/train.jsonl`, `holdout.jsonl`, `MANIFEST.json`, `TRAINING.md`.
It also **warns** when the label mix is degenerate (e.g. all-benign) — read that warning
before training.

## Automatic collection inside the loop

Aegis's closed loop calls this collector at cleanup, so **every real run contributes
attack/defence exchanges to the queue** (`AEGIS_JEVTRAIN=off` disables it). The hook is a
side channel: a collection failure emits `jevtrain.collect_failed` and never changes the
run's verdict.

Each sample carries `pair_role` (attack/benign) so `pairs` can tell you which families
still have only one side — a draft built from unpaired families trains a biased judge, so
`export` warns when that is the case.

## Prohibitions

- NEVER set `AEGIS_JEVTRAIN_OPERATOR` from inside the agent task or invent a reviewer name.
- NEVER relabel a sample to make it fit the judge's verdict.
- NEVER export unapproved rows, and never describe a draft as "a trained model".
- NEVER mix the online abstain band (0.40–0.60) with the offline band (0.20–0.80) when
  reporting what the resulting model does.

## Label review before approval

Operator-only, separate from approving sample admission:
```text
python scripts/aegis_jevtrain.py review-label --id <sample_id> --traffic-intent attack --reviewer <operator> --note "independent evidence basis"
python scripts/aegis_jevtrain.py approve --id <sample_id> --reviewer <operator>
```
A corrected label invalidates its old sample approval. The exported metadata retains the label audit. `training_ready=false` forbids training; the public package has no sealed registry, so its material remains a draft. Environmental gates record intent/provenance; they are not OS security isolation.
