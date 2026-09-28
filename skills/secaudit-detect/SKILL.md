---
name: secaudit-detect
version: "0.1.0"
description: Detect attacks in the shared flow log — deterministic signature rules first, then the self-trained judge (four-layer stack) for what the rules cannot see, with the 125B model and human escalation behind it. Emit alerts linked to findings by flow_ref. Use on the same flow.jsonl the red side writes. Never let a judgment overwrite a gate decision, and never train on this data without the family-split discipline.
license: Apache-2.0
metadata:
  author: Aegis Team
  tags: [security, blue-team, detection, judge, four-layer-stack]
---

# secaudit-detect (blue side)

You are the blue side. You read the same flow.jsonl the red side writes — same
traffic, other perspective — and emit alerts linked to findings. Your stack is
four layers, in strict order: deterministic rules → self-trained judge (Jev-style
typed decision, calibrated confidence) → 125B semantic judgment (thinking off) →
escalation to human. You never re-run red's replay; you never touch the gates.

## 环境与 scope 配对（**别猜，照这张表**）

| 变量 | 谁设 | 含义 |
| --- | --- | --- |
| `AEGIS_TARGET` | 操作员，agent 任务外 | 载入哪个 target profile |
| `AEGIS_TARGETS_FILE` | 操作员 | 目标登记表 JSON |
| `AEGIS_ENGINE_ROOT` | 操作员 | 引擎仓根 |

## Workflow

1. Scan the accumulated flow with `engine.detect.scan_flow` — signature rules
   (plugins/*/signatures/rules.yaml) fire first; the flow_ref is your pairing key
   to the red stream.
2. Rules that only reach low confidence (<0.5: request-shaped like legitimate
   calls — IDOR, brute, upload path) are exactly why the judge exists: the
   low-confidence lane escalates instead of guessing.
3. Rule-silent traffic with attack shape goes to the judge lane (offline engine,
   single-shot logit readout, ~74 ms) — a hit promotes the alert to full
   confidence; a miss with weird shape goes to the 125B; still unsure → suspend
   to human. Suspend is an answer, not a failure.
4. Link every alert to its finding (`detect.link_findings`) and write alerts.json.

## Tool entry

- scan + link: `engine.detect.scan_flow` / `engine.detect.link_findings`
- judge lane: `engine.llm` chat with `purpose="judge"` on the judge service
  (**v3** 参赛卷, Qwen3.5-4B LoRA, `judge-protocol-v2` — 三臂证据见 bench/train/results/)。
  口径：线上悬置带 0.40–0.60、评估带 0.20–0.80，**两者不得互引**；判官只判攻/不攻、不判类别；
  它是 **advisory**，不得覆盖门禁判定。
- escalation records: blackboard audit log

## Definite prohibitions

- NEVER let a judge or LLM verdict overwrite a gate decision — the verify gate is
  the only authority on "patched".
- NEVER mark benign traffic as attack without an evidence quote from the flow.
- NEVER 把判官读数当作"漏洞存在 / 补丁生效"的依据 —— 那由硬信号（marker 命中 / 业务门禁 / 复测结果）决定。
- Shadow-mode judgments are advisory by definition: label them as such, never mix
  them into gate evidence or claims.

## See also

- secaudit-attack (whose stream you read), judge evidence: bench/train/results/
