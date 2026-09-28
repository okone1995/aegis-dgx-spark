> 最终修订：2026-09-29（UTC+8）。当前交付以 RELEASE.md、docs/FINAL-ACCEPTANCE-20260929.md 和 FINAL-BUILD-MANIFEST.json 为准；较早施工日志只代表当时状态。

﻿# Aegis 项目文档（评审版）· 2026-09-28

> **给评审的使用方式**：先读 §1–§2（5 分钟拿到定位与现状），再进 §5（**本轮变更台账**，逐条对应上一轮审阅意见与证据），
> 需要动手时看 §4 与 §10（验证入口与待答问题）。所有数字都带**快照时间**；未验证项一律标 `未验证`。
>
> 快照时间：**2026-09-28T12:14:21Z**（本机 local = UTC+8）。本文档本身是 `docs/PROJECT-DOC-20260928.md`。

---

## 1. 一句话定位

**Aegis（神盾）· 受控 RSI（Recursive Self-Improvement，递归自我改进）**：
在**自建授权靶场**上，让 agent 完成"**红方自挖风险 → JEV 判官独立判定流量 → 蓝方自修补与复测 → 结果回流为训练材料**"的闭环；
目标不是"扫一遍"，而是**可控、可恢复、可审计**的改进循环。面向保险等高敏感业务。

**一句话主链（当前口径）**：本地 125B 生成修补 → 三门禁 → 部署 → 同一注入复测打不动、正常业务仍通过 → 候选入队；
**JEV v3 在 Spark 判定流量是否为攻击**；**StepFun 云端参与候选补丁门禁（不可用即 fail-closed），但不做 JEV 流量分类、不作为补丁生效的 oracle**；修补结果由攻击复测与业务复测共同决定。

---

## 2. 当前状态（最终修订）

| 项 | 实际状态 |
|---|---|
| Spark 台账 | 45 轮：22 succeeded / 17 failed / 4 interrupted / 1 partial / 1 cancelled；新 skill run-20260928-155647-3290：verified_restored，17/17 proof，5 新候选、4 重复 |
| 主演示证据 | run-20260927-140612-399b 的 16 件已入最终包；新 skill 有真实命令日志和工件 |
| 判官材料快照 | 122 条：119 attack / 3 benign，9 auto_ok / 113 needs_review；这是能力线候选快照，不混入新 Spark 轮的候选数 |
| 最终视频 | workspace/video/aegis-final-demo-20260929.mp4，四分钟，保留主闭环/JEV/21→3，替换成真实技能调用 |
| 技能短片 | workspace/video/skills-live/aegis-skills-real-execution-20260929.mp4，44.233 秒，有中文合成旁白、明确 4× 等待压缩 |
| PPT | workspace/ppt-final/aegis-dgx-spark-judge-pitch-rsi-20260929.pptx，9 页；修正 evolve 职责并补真实技能实跑证据 |
| 公开能力验收 | 75 passed / 16 subtests passed；最终解压包以 VALIDATION.json 的独立测试记录为准 |
| 来源冻结 | FINAL-BUILD-MANIFEST.json：主仓/能力线 base commit、每源文件 SHA256 与每交付文件 SHA256；源码与录屏现场版本分开记录 |

本包不含完整靶站、权重、凭据与可直接执行的载荷库，公开离线验收不等于从包中直接跑完整 LIVE。模型训练在 5090、推理在 Spark；StepFun 云端复核 diff。JEV 是分类器，其 p_attack 不是业务风险概率。

---

## 3. 系统怎么跑

```
浏览器 /demo ─► FastAPI（创建 run，统一运行编号）─► DemoCase 编排器（按阶段推进，写事件台账）
      └─► 红方（阶梯探测）· 蓝方（四层检测）· JEV 判官 · 补丁模型 · 三道门禁 ─► 验证与恢复 ─► 收据
```

- **三道门禁**：改动范围 / 危险模式 / **云端异构复核（fail-closed）** —— 任一不过即拒，拒案留痕；
- **JEV 判官**：Qwen3.5-4B + LoRA，**离线 logit 判读**，输入真实交换、输出攻/正常/弃权；与规则层**并行独立**；
- **证据面**：每轮产出 `events.jsonl` / `evidence-receipt.json` / `runtime-attestation.json`（部署身份）/ `verification.json`（复测与业务断言）/ `patches/{patch,original,candidate}`。

---

## 4. RSI 四环节与本轮实证

| 环节 | 做了什么 | 证据 |
| --- | --- | --- |
| ① 自挖风险 | 信号驱动阶梯（引号/注释/布尔差分/列宽/自省/定向取数），命中与否**只由硬信号**决定 | `engine/ladders/{sqli,lfi,idor,ssrf,upload_bypass,rce_deser}.yaml`；六类**均已真机命中**并各带对照组 |
| ② JEV 判定 | 自训 4B LoRA 判官；分歧与"像攻击但没打穿"的样本即高价值材料 | `engine/judge_service.py`；`:30002/metadata`；判官口径见 §7 |
| ③ 自修补 + 复测 | 125B 生成候选 → 三门禁 → 部署 → **同一注入重放打不动 + 正常业务仍通过** | 主 run `run-20260927-140612-399b` 全链 16 件证据（已入评审快照 `evidence/`） |
| ④ 样本回流 | 硬信号打标 → 攻良配对 → **人审闸门** → 导出（带 `training_ready` 与三项自检） | `skills/aegis-jevtrain/`；`dataset/review_queue/`；`tools/_jevtrain_cli.py` |

**主演示 run 的关键读数**（原文）：修补前 marker **命中** → 门禁 **3 项 PASS** → 修补后重放 **0/2** →
业务断言「图书馆」命中 ✓、「不存在关键字xyz」不命中 ✓ → `cleanup=restored` → `state=succeeded`；
`runtime-attestation` 记录补丁模型 `Qwen3.8-Flash-Next-NVFP4-SSD-Stream`。

---

## 5. 本轮变更台账（逐条对应上一轮审阅）

### 5.1 P0-1：不得承诺不存在的运行与核验路径

| 变更 | 证据 |
| --- | --- |
| 评审入口改为**三条准确路线**（看 / 自包含子集 / 授权 LIVE），明确"本包不含靶站与载荷，完整攻击链跑不了" | 评审快照 `REVIEW-README.md`（提交 `b4ab226`） |
| `requirements` 补 `fastapi / uvicorn / httpx`（分面说明：起 Console / API 测试才需要） | `requirements.txt`（主仓 `731401d`） |
| `judge_service` 缺判读模块由"import 崩"改为**明确报错** | `engine/judge_service.py`（fork `563c5a4`） |
| **视频主 run 的 16 件证据入包**（收据/事件/attestation/verification/补丁三件套/flow/judge） | 评审快照 `ccd706c`，`evidence/main-run-run-20260927-140612-399b/` |
| 自包含子集实测 | `pytest tests/test_hunt.py tests/test_jevtrain.py tests/test_learning_queue.py -q` → **20 passed / 2 skipped** |
| **未完成** | ① `claims.yaml` 引用的结果文件（21 个）**未进公开包**（包内无 `bench/`）⇒"数字出处"缺；② **"从零解压到新目录"复验未做** |

### 5.2 P0-2：主文档口径统一

| 变更 | 证据 |
| --- | --- |
| README/JUDGES 统一一句话主链；**去掉"漏洞清零"泛化**，改为"本次受测案例通过复测" | 主仓 `731401d` |
| `EVIDENCE.md` 把 73.6/118.2 ms **逐行标为 v1 历史口径**，并**补参赛 v3 的 80.8/118.5 ms 行**，写明两口径不得换算 | 主仓 `731401d` |
| `JUDGES.md` 的**过期指针修掉**：旧六页 deck 标"仅历史、已作废"，改指最终 MP4 与 RSI 9 页版；`golden-run 回放包`承诺撤回 | 主仓 `731401d` |
| **README 以 RSI 为主线重写**（三分钟入口带媒体哈希、RSI 已完成/下一步分层、JEV 小节、主 run 证据链、技能复用、边界七条） | 主仓 `793b5f6` + `caf9c3a` |
| **未完成** | README 第一屏仍**未直链** MP4/deck（现为文字路径 + 哈希）；"下一版尚未训练"未在第一屏成句（在 §2 与 §8） |

### 5.3 P1-1：标签语义（**本轮最重要的一处纠错**）

**问题**：判官的本职是"**流量是不是攻击**"，而我原先的规则把"**没有 marker**"（这次没打穿）当成了"**良性**"——把**攻击意图**与**攻击成功**混为一谈。

| 变更 | 证据 |
| --- | --- |
| 拆成三字段：`traffic_intent`（是不是攻击）/ `exploit_outcome`（有没有打穿）/ `label_review_state`（能否直接训练）；攻击探针恒为 attack，无 marker 记 `blocked|needs_review`；**交换出错=样本不可用**，保持 unlabelled | fork `b19bc03`；行为探针实测：打穿→attack/confirmed；被挡住→attack/blocked\|needs_review；交换出错→无标签；正常业务→benign/none |
| 旧队列**重标并隔离**：116 条旧 benign → attack + `needs_review`，旧标签留 `legacy_labels`，`review_state` 退回 `pending` | fork `e4d1913`（备份 `jevtrain_candidates.jsonl.pre-retag-backup`） |
| **撤回**"107/122 多为误报"的结论：复核显示这 116 条里判官当时判 **attack 106 / benign 9 / abstain 1** ⇒ **错的是我方标签口径，判官判对了** | 同上；文档更正块已加在 T13/T14 顶部与评审包第 4 问 |
| 导出加 `MANIFEST.label_semantics` 与 **`training_ready`**：有 `needs_review` 行或存在单一标签/不平衡/未配对/切分不独立 ⇒ **false** + `training_blockers` | fork `e4d1913`；实跑 `training_ready=false` + 4 条阻塞项 |
| **二次纠错（qcode 报告）**：重标时把**基线探针**与**差分对照假值**也标成 attack（它们不是攻击载荷）⇒ 按 `flow_id` 重判探针种类（`baseline` / `*:false` = benign，其余阶梯步骤 = attack），并**强制 `pair_role` 与 `intent_label` 同源** | fork（"修 qcode 报告第 2 条"）；现 122 行 = **118 attack / 4 benign**，`report()` 与 `pairs()` 同源校验 **True** |

**口径分歧（请评审裁定）**：qcode 报告称"122 行里 103 行载荷没有任何攻击形态"。我按"**探针是不是攻击载荷**"重判只翻回 **4 条**；
其余约 99 条是 `depth2:true`、`../../../`、`UNION SELECT`、编码变体等**真攻击载荷但未打穿**。两者口径不同，但被标成 attack 的样本都有攻击载荷，欢迎给反例。

### 5.4 P1：闸门定位与真洞

| 变更 | 证据 |
| --- | --- |
| 表述纠正：闸门是**署名与流程约束**，**不是** agent 绕不过的权限边界（写明同进程自设变量即可通过；真正隔离需权限域分离或外部审批记录） | 主仓 `731401d`；评审快照 §6 |
| **真洞修复（qcode 报告第 1 条）**：`aegis-evolve` 原先 `reviewer is None` 也放行 ⇒ 省略 `--reviewer` 可静默过闸、署名为 null；现收紧为**必填且逐字相同**（空值给 `operator_reviewer_required`），`review`/`apply` 两处改 `required=True` | fork `e0726c2` / 主仓 `4cb1aaa`；**新增回归测试**（None/空串/空白必须被拒 + 匹配放行），该技能 **7 条全绿**；行为实测四种输入均正确 |

### 5.5 P2 与其余

| 变更 | 证据 |
| --- | --- |
| `training_ready` 不只是"说明问题"：已作为机器可读状态**阻断训练侧自动消费** | 见 5.3 |
| 新增 `RELEASE.md`：入口/媒体哈希/主 run ID/模型与协议/口径/测试环境/已知缺口，未核验项**逐项标 `待补`** | 主仓 `731401d` |
| 长期悬案修复：freeform 造招 0 命中的根因是提示词里的字面词 `METHOD`（模型照抄 ⇒ 语法闸整批拒） | fork `9212c3c`（accepted 0→3） |
| **未完成** | 1. `JUDGES.md:26`「零网络写请求」与 `:30` 自否定并存、`EVIDENCE.md:43` 同形 100% vs GGUF 未实测 ⇒ 待逐行收口；2. `aegis-hunt/SKILL.md` 列了 `operator_gate_not_satisfied` 但脚本无该闸门（文档越界）⇒ 待删并注明"发现动作不需闸门"；3. RSI"已完成/下一步"分层已在 README §2，但 deck 与 README 的措辞需再对齐一次 |

---

## 6. RSI 走到哪了（**已完成 / 下一步**，分层）

| | 内容 |
| --- | --- |
| ✅ 已完成 | ① 历史对抗材料上的 v3 训练与评估并在 Spark 部署（留存 B 卷 acc **0.9992**、混合域 holdout **0.9955**、零样本对照 **0.8431**）；② Spark 推理、代码修补、三门禁、复测与恢复；③ **候选材料回流**与四个技能入口；④ 主演示闭环一次通过并留下可审计证据；⑤ 六类漏洞阶梯真机命中 |
| ⏭ 下一步（**尚未发生**） | ① 用**正确的人审标签**重建训练材料（当前能力线候选良性侧仅 3/122，**稀缺侧已翻转**）；② 可训练数据质量门禁；③ **下一版权重重训**；④ 独立留存评估；⑤ 升级部署 |

> **主张边界**：我们主张"**受控 RSI 工作流已跑通并留证**"，**不主张**"本轮已自动训出更强的下一版"。

---

## 7. 数字表（口径与出处）

| 宣称 | 值 | 备注 |
| --- | --- | --- |
| 判官 **v3（参赛）** 留存 B 卷 | acc **0.9992** / 攻击召回 **1.0** / 精确率 0.9987 / 弃权 0（n=7266，**离线带 0.20–0.80**） | `claims.yaml: b_*_v3` |
| 判官 v3 混合域 holdout | acc **0.9955**（n=4244） | deck 第 8 页同源 |
| 判官 **v1（对照）** 同卷同口径 | acc 0.9989 / 召回 0.9996 / ECE 0.0006 | 仅历史对照 |
| 同卷底座零样本 | acc **0.8431** / 召回 0.7618 | `b_zeroshot_*` |
| 判官推理时延（**v3**） | median **80.8 ms** / p95 **118.5 ms**（batch=1） | **v1 为 73.6/118.2，不得换算** |
| 主演示闭环 | 命中 → 3 门禁 PASS → 重放 0/2 → 业务断言通过 → restored | `run-20260927-140612-399b` |
| 独立案例（C 靶场工作副本） | `llm_guided` 修补 + 三门禁 PASS + 文件恢复到该 run 的补丁前字节；跨副本 CRLF/LF 需区分 | `run-20260927-103447-1026` |
| 红方自由造招 | 准入 47 / 环境命中 17 / 双闸拒案 22 | `wave5_*` |

**线上悬置带 0.40–0.60 与离线带 0.20–0.80 不得互引**；判官读数**只作分歧分析**，不得作为漏洞存在或补丁生效的依据。

---

## 8. 技能包（复用面，11 个）

| 技能 | 作用 | 闸门 |
| --- | --- | --- |
| `aegis-hunt` | 授权目标上探测并归档可复现发现 | 登记表 + scope + 预算硬上限（**发现动作无操作员闸门**） |
| `aegis-evolve` | 候选→取证→人审→写回→下轮复用 | `AEGIS_EVOLVE_OPERATOR` + `--reviewer` **必填且逐字相同**（本轮修洞） |
| `aegis-jevtrain` | 判官候选材料：硬信号打标、配对、人审后导出 | `AEGIS_JEVTRAIN_OPERATOR` + 标签复核状态 |
| `aegis-self-repair` | 修补验证工作流 | 目标登记表四字段逐字相符 |
| `secaudit-{recon,detect,attack,patch,verify,run}` | 内部执行组件，完整独立链未验收 | profile/policy/scope；`secaudit-report` 从推荐流程退役 |

验收状态：`self-repair` 首轮 ✓ · `hunt` 两轮 ✓ · `evolve` 同一轮 ✓（+ 本轮闸门修洞）· `jevtrain` **两轮**（第二轮真跑，主链全通并抓出两处真缺陷 → 已修）。

---

## 9. 收口结果与能力边界

已修：hunt 的 GET/POST 交换保真与不完整旧样本隔离；标签确认/纠错、样本审批、审核留痕；严格训练门禁与固定内容 legacy；家族独立切分；真实媒体验收、完整来源哈希；错标签契约、命令示例与 report 推荐。

四个新技能支持范围见 docs/SKILLS-SUPPORT.md。真实 skill 已走 start→status→verdict，17/17 证据通过。旧六件套完整 standalone runloop、brute_no_lock 阶梯、LLM 兜底命中、任意目标桥接、下一版训练升级仍未完成；不作为本次完成主张。

十日谈正文已完成。C 追加登记与两份独立报告支持适配后的效果级修补，严格 oracle 规程仍缺工件；不称盲修复。官方上传及征文发布由负责人手动完成，本地材料不冒充官方凭证。

---

## 10. 给评审的问题（请逐条给结论 + 依据）

1. **闸门**：`evolve` 已收紧 None/空值；是否还有其他"看似有闸门、实则可绕"的路径？`hunt` 无需闸门这个判断是否成立？
2. **标签口径**：`traffic_intent / exploit_outcome / label_review_state` 三分是否够用？基线探针与差分对照假值算 benign 是否正确？qcode 的"103 行无攻击形态"是否成立（请给反例 sample_id）？
3. **v8/v9**：确认不可训吗？如果要重建材料，良性侧该怎么补（多靶？多类别？合成业务流量？）
4. **判官分歧**：重标后 106 条由"分歧"变为"一致"，是否存在我"用新标签掩盖真分歧"的风险？请独立复核若干条原始交换。
5. **引擎去耦合**：profile 化是否彻底？还有残留硬编码/静默回退（尤其门禁与认证面）？
6. **`secaudit-report`**：重做还是退役？
7. **公开包边界**：21 个结果文件该全带还是"摘要 + 私有侧"？怎样的"数字出处"才算够？
8. **RSI 表述**：§6 的"已完成/下一步"分层是否足够克制（不暗示已训出更强权重）？
9. **冻结**：正式提交采用主仓还是 fork？哈希清单怎么定（`RELEASE.md` 的结构是否够）？
10. **估时**：把 §9 的 1–8 做完需要多久？有没有你认为优先级排错的地方？

---

## 11. 边界声明（我们不说什么）

- **不主张"漏洞清零"**：只主张"本次受测案例通过复测"；
- **不主张"数据出域 0 字节"**：判定不依赖外部网络，**StepFun 云端只复核候选补丁 diff**；
- **不主张"已自动训练出更强的下一版"**（见 §6）；
- **不主张"识别未见过的攻击"**；判官读数不得作为漏洞存在/补丁生效的依据；
- **不说 GGUF 实测**（从未实测）；**不混引** v1/v3 时延口径；
- **不把闸门说成安全隔离**；
- 攻击只指向操作员 scope 白名单内的**本地自建靶场**；本仓不含凭据与原始敏感语料。
