# T13 · Skill 施工记（做了什么 / 有什么证据 / 还差什么）

> ## ⚠️ 更正（2026-09-28，独立审阅 P1-1）
>
> 本文下方凡出现「判官 107/122 分歧」「多为误报」的表述，**口径已错并作废**：旧规则把
> 「请求像攻击但没有 marker」判成 benign，等于用"这次没打穿"否定"这是攻击流量"——
> 而判官的本职是判**流量是不是攻击**。经复核，那批分歧中 **106 条是判官判 attack、而我方标签错**；
> 这是**我方标签口径的错，不是判官过报**。
>
> 已改：标签拆成 `traffic_intent` / `exploit_outcome` / `label_review_state`；攻击探针恒为 attack，
> 无 marker 记 `blocked|needs_review`；导出加 `training_ready`，有待人审行时**不得**交训练侧消费；
> 受影响的旧候选已重标并隔离（旧标签留在 `legacy_labels`）。结论请按此更正后的版本引用。

> 用途：交接与复盘。所有结论都附真机证据或命令；没有证据的一律标"未验证"。
> 状态板是唯一状态记录（`docs/worklog/T8-status-board.md`）；本文是施工过程与边界的完整叙述。
> 时间跨度：2026-09-27（本机与 Spark 联调）。

---

## 0. 一句话

把 Aegis 从"**能在我手里演示**"推进到"**别的智能体能直接用**"：引擎去掉目标硬编码、
四个技能包上线并过陌生 agent 验收、攻防流量能自动沉淀成判官训练材料。
**未完成**：多靶/多类别覆盖、`secaudit-report`（总结那一格）、训练本体。

---

## 1. 三条贯穿全程的纪律（写进代码，不是文档约定）

1. **判定只来自硬信号**（marker 命中 / 业务门禁 / 复测结果）；判官（JEV）读数**只作分歧分析**，不得作为漏洞存在或补丁生效的依据；
2. **人审闸门不可绕过**：agent 只能提议（收集/发现/导出草案），**批准与写回必须由操作员在 agent 任务之外**触发（`AEGIS_EVOLVE_OPERATOR` / `AEGIS_JEVTRAIN_OPERATOR`）；
3. **队列 ≠ 已训练**；B 留存家族拒入、已封桶不重切；口径（线上带 0.40–0.60 / 评估带 0.20–0.80）不得互引。

---

## 2. 引擎侧：把"目标"变成数据（fork 内完成，主仓 engine 一字未动）

| 做了什么 | 证据 |
|---|---|
| `targets/<name>/profile.yaml` + `engine/target_profile.py`（路径/门禁/实例/语料全数据化；缺 profile 与缺 `${ENV}` 均**可见失败**） | 单测 6 条；缺变量报 `环境变量未设置 ['${AEGIS_C_WORK}']` |
| plugin overlay：`targets/<t>/plugins/<cls>/` 优先，**只有 edu-lite 允许回退**仓内资产 | 探针实测 overlay 命中；负例（缺资产）如期 `TargetProfileError` |
| 六模块去耦（runloop/demo_case/case_probes/attacker/acceptance/replay）+ 认证字段/凭据/功能测试命令数据化 | fork 全量回归 **312 passed / 1 skipped** |
| **C 靶场工作副本闭环**（第二目标） | `run-20260927-103447-1026`：`succeeded` / `repair_outcome=verified` / `patch_mode=llm_guided`（模板**无源码锚点**，模型自定位并参数化修复）/ 三项门禁 PASS / 复测 2 条 0 命中 / 清理还原哈希 = 留存原件哈希 |

**过程中修掉的真缺陷**（都是"静默取错目标/假设"类）：`demo_case` 7 处绕过 profile、markers 读扁平键、
`plugin_dir` 静默回退、无认证目标被判 inconclusive、`cfg['cookie']` 硬取、`import os` 缺失、
功能测试路径 `relative_to(ROOT)` 在仓外抛错、`bash deploy.sh` 未走 profile、`shlex` 吃反斜杠（Windows 路径）。

---

## 3. 四个技能包（都在主仓 `skills/`，均 fail-closed 桥接）

| skill | 作用 | 真机证据 | 陌生 agent 验收 | 已知缺口 |
|---|---|---|---|---|
| `aegis-hunt` | **自主找 bug**：信号驱动阶梯（引号→注释→布尔→列宽差分→自省→取数）+ 库优先 | 零样本只给端点+参数名：**21 发命中**、自动定出列宽 7、自提取表名 | ✅ 两轮（第二轮"**拿来就能用**"：7 步全通、零源码、零猜测） | 只有 **sqli** 一类；只在 C 一靶验证 |
| `aegis-evolve` | **沉淀与复用**：报告→人审→写回→下轮用上→分栏对比 | 闭环：21 发零样本 → 人审 → 写回 1 条（带 provenance）→ **下轮 3 发命中同一载荷** | ✅ 同轮验收（**闸门拦住 agent 自批**） | — |
| `aegis-jevtrain` | **学习材料**：收集→硬信号打标→配对→人审→导出可训练卷 | 队列 **2 → 21 → 122 条**；`v8-mut` 草案 **116 行**（train 88/holdout 28）；逐条判官读数 | ❌ **未做** | 攻侧正样本仅 6 条；重叠度只到家族级 |
| `aegis-self-repair` | 跑官（注册目标预览，GPT-6 产出，我做了去硬编码与实测） | 真机闭环 `verified_restored`；17 项 proof 全真 | ✅ 首轮验收 | 单注册目标预览 |

**共同设计**：操作员 scope + SHA-256 钉住；目标必须在登记表且四字段逐字相符；只允许回环 base；不跟随重定向；
预算硬上限；机器可读错误码 + 非 0 退出码；"运行失败"是合法结果（退出码 0 + `claim`）。

---

## 4. 判官训练材料：从"攻防数据"到"可训练卷"

- **闭环自动收集**：每轮真机 run 收尾自动把攻/良交换入队（旁路：失败发 `jevtrain.collect_failed` 但**不改本轮结论**；`AEGIS_JEVTRAIN=off` 可关）—— 实测 `seq 33 jevtrain.collected {"added": 2}`；
- **攻良配对**：入队带 `pair_role`，`pairs` 看"只有单侧"的家族，导出写 `pairing` 块；未配对会**写进平衡告警**；
- **变异刷量**：把 hunt 阶梯每个探测点当母弹过变换菜单（sqli 5 招）—— 一轮 **24 母弹 → fired 106 → 入库 101 条**（策略拒 0）；
- **导出物形状与训练侧一致**：`train.jsonl` / `holdout.jsonl` / `stats.json` / `MANIFEST.json`（逐文件 sha256 + queue_sha256）/ `TRAINING.md`，可直接 `python bench/train/train_lora.py --data dataset/judge_<v> --batch-size 4 --epochs 3`；
- **诚实告警**：单一标签 / 严重不平衡 / 未配对家族都会触发 `balance_warnings`；对比卷不在本机时重合度标 `not_computed`（**不得把 0 当无重合**）。

**本轮照出来的判官弱点（新）**：在 C 靶场的阶梯流量上，判官 **107/122 条分歧**，其中绝大多数是误报
（请求呈注入形态但 marker 未命中 ⇒ 判官判 attack，硬信号真值 benign）。
**口径红线**：此数**不得**与留存 B 卷的 0.9992 混引（不同靶场、不同流量形态）。

---

## 5. `secaudit-*`（旧七件套）的改造：**只在 fork 副本里做，主仓一字未动**

| 片 | 内容 |
|---|---|
| 片 1 | `patch`/`verify` 的 `php -l` → `profile.expand_syntax_gate` / `gates.functional_test`；`detect` 的过期 **judge v2 → v3** + 口径红线；三份加"环境与 scope 配对表" |
| 片 2 | `recon` 端点来源 → `profile.plugin_dir` + 实例 surface；`attack` 载荷位置走 profile；`run` 补收敛口径（"收敛到 0"的定义、预算耗尽不得写成收敛） |
| 工具 | `tools/skill_doc_check.py`：扫技能文档引用的 `engine.<mod>.<attr>` / `profile.<字段>` / `AEGIS_*` 是否真实存在 —— **把文档债变成可执行检查**（当前 `OK: 无未解析引用`） |

**主仓安全性（每片都验）**：`git status --porcelain skills/secaudit-*` 为空 = 主仓 7 个 skill **一字节未动**。

---

## 6. 量化轨迹（一眼看进展）

| 指标 | 起点 | 现在 |
|---|---|---|
| 判官训练候选 | 2 条 | **122 条**（6 攻 / 116 良） |
| 可训练卷草案 | — | v4(6 行) → v6-paired(2 行) → **v8-mut(116 行)** |
| 自主发现 | 人工写载荷 | **零样本 21 发命中**（只给端点+参数名） |
| 补丁 | 模板套用 | **模型自写**（`patch_mode=llm_guided`，无源码锚点） |
| 覆盖目标 | edu-lite | edu-lite + **C 工作副本**（+ MiniLedger 曾在本地跑通） |
| 覆盖类别 | sqli（库） | **sqli（阶梯 + 变异 + 零样本）** |

---

## 7. 演示就绪体检（2026-09-27 20:5x 实测）

| 项 | 状态 |
|---|---|
| 本机 `8822/demo` · `8000/demo` · `30002/health` | **200 / 200 / 200** |
| Spark `8000`(Console) `8081`(靶场A) `8082`(靶场B) `30000`(补丁模型) `30002`(判官) | **全部监听中** |
| Console `/demo` · 靶场A `/login` | **200 / 200** |
| 判官 `model_loaded` | **true**（uptime ≈ 11.8 h） |
| run 台账 | **18 轮 succeeded**；最新一轮 `run-20260927-055255-278b` = `succeeded/verified/restored` |
| 结论 | **A/B 演示主链现在就能演**（实时跑或回放都行）；能力线的新东西跑在 fork + C 工作副本上，要演需按 §8 的清单准备 |

---

## 8. 还没做完的（按优先级）

1. **多靶**：MiniLedger 接入（本地已跑，加一份 profile ≈30 分钟）；edu-lite 接入（需隧道 ≈1 小时）⇒ 攻侧正样本翻倍；
2. **多类别阶梯**：照 `vendor/hack-skills` 的 playbook 翻译 idor/lfi/ssrf/upload 等（每类 30–45 分钟，需在 edu-lite 上验）；
3. **封装收口**：`aegis-jevtrain` 陌生 agent 验收；`secaudit-*` 改造后**不降级**的真机多轮验收；**`secaudit-report` 重做或退役**（"总结"那一格目前名不副实）；
4. **训练闭环**：草案 → 训练（5090，单次 2h34m）→ 评估（零样本/微调后分栏）→ 回填 claims；
5. **决赛交付**：**#11 录屏 + 回放包**（唯一必须真人开窗口）、`ten-days.md` 的 D9/D10、代码冻结**核验**。

**估时**：能力线到"我敢说成熟"约 **6–8 小时**（不含训练本体）；决赛交付那三项约 **1.5–2.5 小时**（其中录屏 30–60 分钟需真人）。
