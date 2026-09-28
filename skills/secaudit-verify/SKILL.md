---
name: secaudit-verify
version: "0.1.0"
description: Gate one patched finding — replay the FULL payload set (attack and held-out verify groups) and require every marker dead, then run the functional test suite and require it green. Both or the finding regresses. Use after secaudit-patch reports a patch applied. Never pass a finding on partial evidence, and never skip the held-out group — "the patch blocks my own payloads" is not verification.
license: Apache-2.0
metadata:
  author: Aegis Team
  tags: [security, verification, gate, regression, held-out]
---

# secaudit-verify (the gate)

You are the reason self-healing can be trusted. For each patched finding you run
two hostile checks and both must pass: (1) every attack AND held-out verify payload
replayed against the patched target must produce zero marker hits; (2) the target's
functional test suite must be fully green. You always use a FRESH session — patch
redeploys wipe the auth store, and an old cookie would make everything look blocked.

## Required questions

1. Which finding id and instance (a/b)?
2. Was the patch applied to the canonical source AND the instance redeployed?
   (php -S caches; verifying against a stale process proves nothing.)

## 环境与 scope 配对（**别猜，照这张表**）

| 变量 | 谁设 | 含义 |
| --- | --- | --- |
| `AEGIS_TARGET` | 操作员，agent 任务外 | 载入哪个 target profile（决定路径/门禁/端面/认证） |
| `AEGIS_TARGETS_FILE` | 操作员 | 目标登记表 JSON（target_id/case_id/target_base/source_root） |
| `AEGIS_ENGINE_ROOT` | 操作员 | 引擎仓根 |

**硬规则**：任何"路径 / 语法门禁 / 功能测试 / 端面参数 / 认证字段"都从 **profile** 取，
**不得**在作业书里写死（历史版本写死了 `php -l` 与 `plugins/<cls>`，换目标即失效）。

## Workflow

1. Fresh authentication (`engine.replay.admin_session`) — never reuse the red
   side's cookie.
2. `engine.verify.replay_all` with the plugin's verify_mode
   (all_blocked = default; last_blocked for brute — first attempt must still be
   processed normally, last must be locked).
3. `engine.verify.functional_tests` — pytest against the target instance.
4. Both green → transition to verified; any red → regressed (rollback branch,
   retry ≤2 with failure feedback, then accepted with written reason).
5. Re-attack in the same round with a NEW session is the convergence proof.

## Tool entry

- replay + tests + gate: `engine.verify.gate` / `engine.verify.replay_all` /
  `engine.verify.functional_tests`（功能测试命令与 env 由 profile 的
  `gates.functional_test` / `gates.functional_test_env` 提供，不写死 pytest 路径）
- blackboard: transitions with replay stats in detail

## Definite prohibitions

- NEVER verify with a session that predates the patch deploy.
- NEVER count a payload as blocked if its marker appears anywhere in the response
  (the echo-channel lesson: a marker inside its own payload is self-deception).
- NEVER skip the held-out verify group — blocking your own payloads is not evidence.

## See also

- secaudit-patch (whose work you judge), secaudit-run (your round counter)
