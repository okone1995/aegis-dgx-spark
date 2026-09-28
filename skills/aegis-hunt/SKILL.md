---
name: aegis-hunt
description: Signal-driven payload discovery against one registered local target — finds an injection payload itself (quote/comment/boolean/column-count/schema-introspection/targeted-exfil ladder) and records every attempt as evidence. Use when asked to "find a payload", "probe this endpoint", "自动探测 payload", or before an audit round on a registered target. Never against a host outside the operator scope file.
metadata:
  version: "0.1.0"
  tags: [security, discovery, payload, signal-driven, self-healing]
---

# aegis-hunt — signal-driven payload discovery

Gives the host agent a **fail-closed** way to find an injection payload for one
registered endpoint. The engine (Aegis) does the ladder; the agent must not
improvise payloads itself.

## What it does NOT do

- It does **not** write payloads back into the library. Discovery only *queues*
  candidates; write-back is a separate operator action (`aegis-evolve`).
- It does **not** decide "vulnerable" from a model's opinion. A hit requires a
  **hard signal**: a configured response marker, a status-code change, or a
  response-length differential.
- It does **not** support arbitrary targets: only ones declared in the operator
  registry, and only hosts the pinned scope file authorises.

## Required operator setup (outside the agent task)

| Variable | Meaning |
| --- | --- |
| `AEGIS_SCOPE_SHA256` | SHA-256 of the scope file (pin) |
| `AEGIS_TARGETS_FILE` | target registry JSON — schema: `{"schema_version":1,"targets":[{"target_id","case_id","target_base","source_root"}]}` |
| `AEGIS_TARGET` | which registry/profile target to load |
| target credential env vars | as declared by the profile's `auth.credential_env` |

Scope file schema (see `assets/scope.example.json`):
```json
{"schema_version": 1, "target_id": "<registered id>", "case_id": "sqli",
 "target_base": "http://127.0.0.1:8093", "source_root": "targets/<name>",
 "path": "/reports/exception-queue", "param": "warehouse", "vclass": "sqli",
 "max_requests": 40, "max_seconds": 120}
```

## 环境变量与 scope 配对（**照这张表做，不要猜**）

| 变量 | 必须由谁设 | 含义 |
| --- | --- | --- |
| `AEGIS_HUNT_SCOPE_SHA256` | 操作员，agent 任务外 | **本技能**所用 scope 文件的 SHA-256（首选）；未设时回退 `AEGIS_SCOPE_SHA256` |
| `AEGIS_TARGETS_FILE` | 操作员 | 目标登记表 JSON |
| `AEGIS_TARGET` | 操作员 | 载入哪个 profile |
| `AEGIS_ENGINE_ROOT` | 操作员 | 引擎仓根（含 `engine/hunt.py` 的那个目录） |
| `<profile.auth.credential_env.user/pass>` | 操作员 | 目标凭据（见 profile） |

**scope↔pin 配对规则**：`--scope` 传哪个文件，`AEGIS_HUNT_SCOPE_SHA256` 就必须是这个文件的
SHA-256。两个技能各有各的 scope，**不要把一个的销钉塞给另一个**（那会得到
`scope_hash_mismatch`，且这是**设计如此**：销钉就是用来钉死具体文件的）。

## 命令与参数总表

| 命令 | 必填 | 可选 | 说明 |
| --- | --- | --- | --- |
| `selftest` | `--scope` | — | 校验销钉/登记表/回环/阶梯/可达性；不做发现 |
| `run` | `--scope` | `--out <file>`、`--use-library` | **不加 `--use-library` = 零样本**；加了先打 POC 库、命中即停 |
| `report` | `--in <result.json>` | — | 把一次 `run` 的结果压成表（**这里的 `--in` 是结果文件，不是 scope**） |

`--out` 写出的结果里包含 `library_hit`（库优先命中时的载荷文件名）与 `column_count`
（差分探测定出的列宽），两者都进结果契约。

## Commands

```text
python scripts/aegis_hunt.py selftest --scope <scope.json>
python scripts/aegis_hunt.py run --scope <scope.json> [--out result.json] [--use-library]
python scripts/aegis_hunt.py report --in result.json
```

`selftest` checks the pin, the registry entry, the loopback rule, that the
ladder exists for the class, and that the target is reachable. `run` performs
one discovery round and prints the machine-readable verdict. `report` condenses
a saved result for humans/agents.

## Verdict contract

One line of JSON. `claim` is one of:

| claim | Meaning |
| --- | --- |
| `exploited_confirmed` | A marker hit was observed — hard evidence only |
| `anomaly_only` | Status/length anomalies seen, but nothing confirmed |
| `not_found` | No anomaly at all within budget |
| `budget_exhausted` | Budget ran out; not a verdict about the target |

Transport/config problems return `{"status":"error","code":...}` with exit code 2:
`scope_hash_mismatch`, `target_not_registered`, `scope_schema_mismatch`,
`invalid_scope`, `invalid_target_base`, `budget_over_hard_cap`, `ladder_missing`,
the structured codes in `references/result-contract.md`; hunt has no operator write-back command.

## Prohibitions

- NEVER widen the scope file or the registry from inside the agent task.
- NEVER treat an LLM/JEV judgement as the discovery result; JEV is advisory.
- NEVER call a payload "verified" because it looks plausible — only a marker hit
  counts, and every attempt (including failures) is recorded.
- NEVER point this at a host outside the scope file.
