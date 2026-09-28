# T11 · 把 Aegis 做成「给别的智能体用的自修补 skill」

> 目的（用户原话）：这个项目一开始就是为了形成一个 **skill**，能给别的智能体**自修补的能力**。
> 本文件是施工计划；状态记在 `docs/worklog/T8-status-board.md`。C 靶场在此的角色是**能力可迁移性的证伪器**，不是终点。

## 一、现状盘点（实测，非推测）

**已经有的资产（可以直接站上去）**

- **7 个 skill 已存在**：`skills/secaudit-{run,recon,attack,detect,patch,verify,report}`，每个含 `SKILL.md` + `runner.yaml`；`SKILL.md` 已写成"别的 agent 读了就执行"的形态（角色边界、必答问题、workflow、**禁止事项**、工具入口、see-also）。
- **可复用核心**：10 阶段闭环（`engine/demo_case.py`）+ 签名证据链（`evidence-receipt.json` / `runtime-attestation.json`）+ 判官服务（`judge-protocol-v2`）+ 受约束自适应探针（S4）+ 硬负例队列（`dataset/review_queue/hard_negatives.jsonl`）+ **故障注入钩子**（`AEGIS_FAULT_INJECT=llm_unreachable|bad_patch|bad_patch_subtle|post_gate_corrupt|target_down`）+ 本机 278 / 远端 255 测试。
- **治理面已起步**：`governance/scope.example.yaml`（授权范围）、`engine/scope.py`（scope 闸门）、diff bounds / backdoor 扫描 / 异构模型第二意见（fail-closed）。
- **失败分类法已成形**：`succeeded / partial / failed / interrupted / cancelled / regressed / inconclusive`，且终态六条件有真机对照。

**卡住的三件事（这才是"能不能给别人用"的真门槛）**

1. **target 耦合**：`engine` 内 `target/edu-lite` 出现 **13 处**；`CANONICAL_APP`、`INSTANCE_CFG`、`case_probes`（复用 edu-lite 的功能测试语义）、`jail_ctl`（php:8.3 cage）、`acceptance`（edu-lite 的 benign_traffic）、`verify.py::functional_tests` 全部写死；`secaudit-patch` 的语法门禁写的是 `php -l`、补丁来源是 `plugins/<class>/patch-template/fix.patch.tpl`。
2. **没有 agent 面入口**：无 `pyproject.toml` / 无单一 CLI；skill 直接调引擎内部函数（`engine.patcher.apply_template`、`engine.patch_review.review`、`engine.replay.admin_session`）⇒ **换目标/换语言必须改引擎**，别人装不动。
3. **不能自证、不能优雅降级**：别人装上后没有"本机能力自检"，也说不清"没有 GPU / 没有云 review / 没有判官服务时，你会损失什么"。

## 二、目标（可验收的一句话）

> 别的智能体在**它自己的环境**里：装它 → 写一份 `target profile` → 用 `aegis selftest` **证明本机能力可用** → 对**授权**目标跑一轮 → 拿到**机器可读**的结论与证据链 → **缺依赖时可见失败**（不得静默降级成"成功"）。

## 三、五步施工

### S1 · target profile 抽象（把"目标"变成数据）
- 新增 `targets/<name>/profile.yaml`：`kind`（php/node/python/…）、`root`、`canonical_sources`、`deploy_cmd`、**`syntax_gate_cmd`**、`functional_test_cmd`、`endpoints`（含参数名与语料关键词）、`benign_traffic_cmd`、`ports`、`healthcheck`。
- 替换 13 处 `target/edu-lite` 与 `CANONICAL_APP`/`INSTANCE_CFG`/`case_probes`/`jail_ctl`/`acceptance`/`verify.py` 的写死路径；**语法门禁与功能测试由 profile 提供命令**（`php -l` 只是其中一种取值）。
- 验收：同一个 10 阶段闭环在 **edu-lite（A）** 与 **≥1 个 profile 化的新目标** 上都能跑出 `succeeded/verified`，且证据链**结构同形**。

### S2 · 单一 agent 入口 + 机器可读结论
- `aegis run --profile <p> --mode observe|patch --json`、`aegis status <run>`、`aegis verdict <run>`、`aegis learn export`。
- 结论 schema 固定：`state / repair_outcome / cleanup / gates / evidence_paths / failure_code`，并把**失败分类法**写进文档（别人据此写 if-else，不靠猜）。
- 保留既有 Console HTTP API 作为异步观察面；CLI 与 API 共用同一 run 目录与证据格式。
- 验收：一个**没有读过本仓源码**的 agent，只读 `SKILL.md` + `--help` 就能跑完一轮并正确解读结论。

### S3 · 自检（让别人能验证"这工具在我这儿到底行不行"）
- `aegis selftest --profile <p>`：复用既有 5 种注入（`llm_unreachable` / `bad_patch` / `bad_patch_subtle` / `post_gate_corrupt` / `target_down`）逐项跑，逐项断言"应当失败且失败原因正确"。
- 输出一张机器可读的自检表（哪项通过、哪项缺依赖、缺什么）。
- 验收：**故意把依赖拆坏**（如把判官端口指错），自检必须红，且红在正确的那一项上。

### S4 · 能力清单与降级
- `capabilities.yaml`：本地补丁模型（GPU/SSD-stream）、云 review（step，边界=`diff only`）、判官服务（`judge-protocol-v2`）分别标注：必需 / 可降级 / 可关闭，各自"降级后损失什么"。
- 硬规矩：**缺依赖可见失败**；不得把降级模式的结果说成全能力结果。
- 验收：三种缺依赖场景各跑一次，输出符合声明。

### S5 · 迁移证明（这一步才回答"能不能在没见过的环境里发现 + 修补 + 判别 + 进化"）
- 在**两个它没被建造过的目标**上跑；其中**至少一个由未记录披露的其他智能体执行**。
- 报告必须**分栏**：零样本 / 适配后 两套数字，各自附证据链；失败与人工介入点如实列出。
- **C 靶场不做 skill 开发目标**（它是盲评裁判，烧掉就没了）。若确要用 C，先在 `docs/integrity-log.md` 登记记录披露事实，并**保留原件与原始成绩**、另起"适配后"版本分栏。
- 验收：两份报告 + 两次 run 的证据链 + 明确的"哪一层不成立"。

## 四、硬约束（继承本仓红线）

1. 任何"完成"必须附**真机 run** 或可复跑证据；静态检查、DOM 存在、历史成绩**不得**写作"通过"。
2. 故障注入必须留痕（`fault.injected` + preflight `fault_inject`），不得把注入跑出的失败当自然失败。
3. 口径纪律：线上 `judge-protocol-v2` 悬置带 0.40–0.60、离线 0.20–0.80，**不得互引**；跨靶指标**分栏**。
4. 交付物**不得**宣称"必然成功"；必须带失败分类法与可见失败。
5. 授权边界：无 `scope.yaml` 授权文件的目标一律拒绝（`engine/scope.py` 已有闸门）。

## 五、待拍事项

1. **先做哪一段**：建议 S1（profile）＋ S2（入口）一起做——只做 S1，别人依然用不上。
2. **迁移证明选目标**：建议**新建一个可丢弃的小靶场**，且**换一种语言**（证明不是 PHP/profile 里的特例）。
3. **对外部用户的能力边界**：云 review 与判官服务是"自带 / 降级 / 直接关闭"三选一。
