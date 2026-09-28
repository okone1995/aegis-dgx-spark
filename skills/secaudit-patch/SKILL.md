---
name: secaudit-patch
version: "0.1.0"
description: Produce a repair for one confirmed finding — pre-validated class template first, LLM adaptation when the template cannot anchor, and every artifact pushed through php -l plus the triple review (diff bounds, backdoor scan, heterogeneous second opinion; fail-closed). Apply on a git-tracked canonical source branch and redeploy. Use only for findings with status=open. Never apply without the verify gate scheduled next.
license: Apache-2.0
metadata:
  author: Aegis Team
  tags: [security, patching, llm, triple-review, self-healing]
---

# secaudit-patch (repair layer)

## 环境与 scope 配对（**别猜，照这张表**）

| 变量 | 谁设 | 含义 |
| --- | --- | --- |
| `AEGIS_TARGET` | 操作员，agent 任务外 | 载入哪个 target profile（决定路径/门禁/端面/认证） |
| `AEGIS_TARGETS_FILE` | 操作员 | 目标登记表 JSON（target_id/case_id/target_base/source_root） |
| `AEGIS_ENGINE_ROOT` | 操作员 | 引擎仓根 |

**硬规则**：任何"路径 / 语法门禁 / 功能测试 / 端面参数 / 认证字段"都从 **profile** 取，
**不得**在作业书里写死（历史版本写死了 `php -l` 与 `plugins/<cls>`，换目标即失效）。

You repair one finding. Your product is a minimal diff on the canonical source —
and your work is NOT done when the diff exists: it is done when php -l passes and
the triple review says yes. The verify gate will try to break your patch afterwards;
assume it is hostile.

## Required questions

1. Finding id and class (must exist on the blackboard with status=open)?
2. Route: template-exact (fast path) or LLM-first (explicitly requested / anchor broken)?
3. LLM provider if needed: local qwen (sovereign path) or cloud step (enhanced tier)?

## Workflow

1. Load the class patch template (`plugins/<class>/patch-template/fix.patch.tpl`).
2. `engine.patcher.apply_template` — indent-adaptive exact match on all block pairs;
   on anchor failure with an LLM route configured, `engine.llm.patch_adapt`
   rewrites from the full file with the template's strategy as guidance.
3. the **profile's syntax gate** on the result (`profile.expand_syntax_gate`) — a syntax
   error rejects the patch immediately. **Do not hard-code `php -l`**: the gate command is
   target data (`gates.syntax` in `targets/<name>/profile.yaml`), so a Python/Node target
   runs its own checker. Never pass a patch that failed the gate.
   (this gate has caught real LLM output; it will again).
4. LLM-mode artifacts additionally pass `engine.patch_review.review`:
   diff bounds (hunks near the vulnerability block) → backdoor pattern scan on
   added lines → heterogeneous model second opinion (fail-closed: unavailable = rejected).
5. Write the diff to the blackboard, transition the finding to patched, redeploy
   the target instance so the patch is live for the verify gate.

## Tool entry

- template/LLM: `engine.patcher.apply_template` + `engine.llm.patch_adapt`
- review: `engine.patch_review.review`
- syntax gate: `engine.target_profile.active().expand_syntax_gate(file)`
  (edu-lite 的取值是 `php -l`；其它目标由各自 profile 声明)
- blackboard: transitions + patches/F-xxx.patch

## Definite prohibitions

- NEVER apply a patch that failed lint or review — fail-closed means fail-closed;
  the finding goes accepted with the reason, not retried silently.
- NEVER "fix" a review rejection by editing the review.
- NEVER introduce PDO (runtime lacks pdo_sqlite) — the templates say it, the
  review enforces it.

## See also

- secaudit-verify (the gate that judges you), secaudit-report (documents you)
