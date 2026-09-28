# aegis-self-repair skill 实测报告（2026-09-27）

测试人：集成负责人（监考）。对象：GPT-6 产出的 `skills/aegis-self-repair/`（v0.1.0）。

## 结论：合格，可进入候选待核验状态

按 T10 红线逐项实测，无一条违反。**注意：本报告只证明"这个 skill 可用且诚实"，不证明 Aegis 引擎本身可迁移**——该 skill 是"注册目标 edu-lite-a/sqli 单靶预览版"，按其自述不宣称任意目标修复。

## 实测矩阵

| # | 测试 | 结果 |
|---|---|---|
| 1 | 自带测试套件 | **20 passed + 16 subtests**（0.09s，纯桥接层单测） |
| 2 | A 空权限 scope `start` | **正确拒绝**：`operation_not_authorized`，退出码 2 |
| 3 | A2 pin 哈希不匹配 | **正确拒绝**：`scope_hash_mismatch`，退出码 2 |
| 4 | A3 非回环 origin | **正确拒绝**：`invalid_console_origin`，退出码 2 |
| 4 | B 满权限 `selftest` | `status:ok`，且 `runtime_health_checked:false` **如实**（不冒充全量健康检查） |
| 5 | C 满权限 `start` 真机闭环 | `run-20260927-055255-278b` → `succeeded/verified/restored`，verdict `claim=verified_restored`，**17 项 proof 全真**，`evidence_sha256=69de7495…90fc`，学习队列如实报 `new_candidates:5/duplicates:4`（不把排队说成已训练） |
| 6 | E 幂等重放 | 同 request-id 再 start：`created:false` 返回既有 run，**不新建** |
| 7 | E2 幂等键冲突 | 同 request-id 改 cleanup=retain：`idempotency_parameter_mismatch`，退出码 2，**正确拒绝** |
| 8 | 宿主 agent 适配性（未读过引擎源码的子 agent，只给 SKILL.md） | **达到"拿来就能用"**：4 命令全跑通、负例行为与文档预判逐字一致；卡点仅"操作员 env 未进其执行环境"（部署交接问题，非文档缺陷）；汇报见 `_test_host_agent_report.md` |
| 9 | env 缺失路径（补测） | 缺 SHA256 → `scope_pin_missing_or_invalid`；缺 ORIGIN → `console_origin_missing`；均退出码 2 **可见失败** |

## 该 skill 自己声明的限制（如实，无夸大）

只支持注册的 `edu-lite-a/sqli`；Console 侧不强制 scope（bridge 自限）；JEV 是建议性标签非 oracle；`verified_restored` 不含 cleanup 后独立读回；`retain` 需独立授权；非 NVIDIA-Verified。

## 与 T11 计划的关系

该 skill 实现了 T11 计划里 **S2（agent 入口+机器可读结论）+ S3（selftest）+ 部分 S4（能力边界声明）** 的"单注册目标"版本，且质量好（fail-closed、幂等、诚实降级声明都做对了）。**未覆盖**：S1（target profile 抽象——引擎 13 处 edu-lite 耦合仍在）、S5（迁移证明）。**下一步若按 T11 推进，S1 是主战场**；skill 侧同步动作是把 `REGISTERED` 写死改为读 profile。

## 遗留

- `_test_skill.ps1.bak`（我写的测试脚本备份）待决定去留；
- 该 skill 未经 NVIDIA 信任管线（其 skill-card 自述"scan report/Tier-3 benchmark/签名未产出"，诚实）。