---
name: aegis-evolve
description: Close the self-improvement loop for Aegis discovery results — report queued payload candidates, let the OPERATOR approve/reject the ones with hard evidence, write approved payloads back into the target's plugin overlay with provenance, verify the written payloads replay, and compare two rounds side by side. Use when asked to "review POC candidates", "让系统进化", "把这次发现沉淀下来", or to compare a zero-shot round against a library-first round. Approval and write-back require an operator gate and can never be done by the agent alone.
metadata:
  version: "0.1.0"
  tags: [security, evolution, poc-library, human-in-the-loop]
---

# aegis-evolve — the self-improvement loop, with a human in it

Four moves, in order: **沉淀 → 人审 → 入库 → 下轮用上**, plus a two-column comparison.

## The operator gate (read this first)

`review` and `apply` **require** `AEGIS_EVOLVE_OPERATOR` to be set by the operator
**outside the agent task**, and the `--reviewer` value must equal it. Without the
gate both commands fail with `operator_gate_not_satisfied`.

Why: discovery may be autonomous, but *what becomes part of the attack library*
is a human decision. An agent must never approve its own candidates — otherwise
the loop becomes self-confirming and the evidence chain means nothing.

## 环境变量与 scope 配对（**照这张表做，不要猜**）

| 变量 | 必须由谁设 | 含义 |
| --- | --- | --- |
| `AEGIS_EVOLVE_SCOPE_SHA256` | 操作员，agent 任务外 | **本技能**所用 scope 文件的 SHA-256（首选）；未设时回退 `AEGIS_SCOPE_SHA256` |
| `AEGIS_EVOLVE_OPERATOR` | 操作员，agent 任务外 | 审核人身份；`--reviewer` 必须与之**逐字相同** |
| `AEGIS_TARGETS_FILE` / `AEGIS_TARGET` | 操作员 | 目标登记表与 profile |
| `AEGIS_ENGINE_ROOT` | 操作员 | 引擎仓根（含 `engine/poc_queue.py`） |

**scope↔pin 配对规则**：`--scope` 传 `scope_evolve.json`，就必须把**它自己**的 SHA-256 放进
`AEGIS_EVOLVE_SCOPE_SHA256`。hunt 的销钉是另一个变量，别互相借用（借了会 `scope_hash_mismatch`）。

**加载操作员环境注意**：若用 Windows PowerShell 5.1 载入含非 ASCII 值的 `.ps1`，请确保该文件
是 **UTF-8 with BOM**（否则 gate 值会被静默吞掉，表现为"合法审核人也被判
`operator_gate_not_satisfied`"——这是 fail-closed，方向正确但会挡住合法流程）。

## 命令与参数总表

| 命令 | 必填 | 是否受闸门 | 说明 |
| --- | --- | --- | --- |
| `selftest` | `--scope` | 否 | 校验销钉/登记表/回环/引擎队列 |
| `report` | `--scope` | 否 | 队列现状（`--scope` 这里是 scope 文件，不是结果文件） |
| `verify-written` | `--scope` | 否 | 只重放带 provenance 的载荷 |
| `compare` | `--before <a.json>` `--after <b.json>` | 否 | 两栏 + `delta`；**a/b 是两个 `hunt run --out` 的结果文件** |
| `review` | `--scope` `--id` `--approve\|--reject` | **是** | 未命中不得 approve；状态单向 |
| └ 另需 | `--reviewer <人名>` | — | **必须与 `AEGIS_EVOLVE_OPERATOR` 逐字相同**，否则 `operator_gate_not_satisfied` |
| `apply` | `--scope` | **是** | 默认 dry-run；`--apply` 才写盘，只写 approved+confirmed；同样需 `--reviewer` 与闸门一致 |

**路径基准**：文档里的 `scripts/…` 相对**技能目录**；`--scope` / `--out` / `--before` / `--after` 相对**你的当前目录**（建议一律给绝对路径）。

## Read-only commands (no gate)

```text
python scripts/aegis_evolve.py selftest --scope <scope.json>
python scripts/aegis_evolve.py report  --scope <scope.json>
python scripts/aegis_evolve.py verify-written --scope <scope.json>
python scripts/aegis_evolve.py compare --before a.json --after b.json
```

## Gated commands

```text
python scripts/aegis_evolve.py review --scope <scope.json> --id <candidate_id> --approve|--reject --reviewer <operator> [--note ...]
python scripts/aegis_evolve.py apply  --scope <scope.json> --reviewer <operator> [--apply]
```

`apply` is **dry-run by default**; it only reports what would be written. With
`--apply` it writes into `<plugins_dir>/<class>/payloads/<intent>/` and drops a
`<payload>.provenance.json` beside each payload (source, parent, transform,
evidence, reviewer, queue hash).

## Hard rules enforced by the engine

1. A candidate with no hard evidence (`markers_hit` empty) **cannot** be approved.
2. A candidate that is not `approved` **cannot** be written back (dry-run says 0).
3. `approve`/`reject` are one-way and need a reviewer name.
4. `compare` prints two independent columns; merging them into one curve is a
  口径 error and is never acceptable.

## Prohibitions

- NEVER set `AEGIS_EVOLVE_OPERATOR` from inside the agent task, and never invent
  a reviewer name.
- NEVER edit `.provenance.json` or the queue file to make a candidate look approved.
- NEVER describe queued candidates as "trained", "published" or "part of the
  payload set" — only a written-back payload with provenance is in the library.
