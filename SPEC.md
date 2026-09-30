# Aegis（神盾）— 自主红蓝对抗安全审计与自修补系统 · 规格说明书

- 版本：v1.0（决策全锁定稿：9 项决策见 DECISIONS.md；矛盾清理 + 遗漏补齐，评审记录见会话）
- 状态：**定稿执行版**
- 目标赛事：DGX Spark Hackathon（130 队取 10 进决赛）
- 范围哲学：**按最大野心规划，按门控推进**。门控防的是集成风险与演示确定性，不是人力——AI 施工时代，边际成本在架构里，不在工时里。

---

## 1. 一句话定位

在 DGX Spark 上运行的本地多智能体安全审计闭环：**红方智能体对授权靶场发起攻击 → 蓝方智能体实时检测告警 → 修补智能体生成并应用补丁 → 验证智能体重放攻击与功能回归 → 循环迭代直至漏洞收敛清零**。全链路本地推理、数据不出域、每一步留治理证据，前后端完整可交付。

电梯陈述：**"给 Web 应用装一套自主免疫系统"**。

## 2. 为什么必须跑在 DGX Spark（答辩核心叙事）

| 硬约束 | 本项目对应 |
|---|---|
| 客户源码 / 漏洞流量 / 攻击日志不可出域 | 全部推理与编排本地完成，行业真实合规约束，不是避短 |
| 128GB 统一内存 | 125B MoE 推理 + 靶场 + 流量镜像 + 编排 + 前后端服务同机共存 |
| 平台全栈能力（评分明确要求） | 推理（SGLang 125B NVFP4）+ 训练（QLoRA 设备端）+ 应用服务（FastAPI + 控制台）+ 数据（本地向量库）全栈同机 |
| 断网可用 | Spark 现网已是白名单环境，本项目全程在其中开发部署，本身就是验证 |
| 已有实测底座 | Qwen3.8-Flash-Next NVFP4 @ SGLang：解码 23-27 tok/s、262K 上下文、250K 大海捞针全通过 |

## 3. 目标 / 非目标

**目标（G）**
- G1 8 类漏洞的"攻击→检测→修补→回归"全自动闭环（类别清单见 §8）
- G2 多轮对抗收敛演示：漏洞数随修补轮次下降的收敛曲线
- G3 漏洞类别插件化：新增一类漏洞 = 新增一个数据包，不改管线
- G4 治理展柜：scope 授权校验、payload policy、SkillSpector 自扫报告、技能卡全套
- G5 基准报告：官方 BENCHMARK.md 格式的"无技能基线 vs 有技能"提升表 + held-out 评估
- G6 StepFun 模型双集成点（见 §13）
- **G7 Aegis Console 前后端完整看板（新增，对应评分"前后端完整"）**——双视角实况、findings 状态机看板、收敛曲线、补丁 diff、审计时间线

**非目标（明确不做，写进 README）**
- 漏洞链组合利用、fuzzing 式新漏洞发现
- 对任何真实第三方系统的测试（靶场仅限本地 scope 白名单实例）
- WAF / 防御设施部署、多机分布式
- 移动端适配、多用户权限体系（单演示用户足够）

## 4. 总体架构

```
                        ┌────────────────────────────┐
                        │     secaudit-run（编排）     │
                        │  scope校验·轮次控制·报告汇编  │
                        └─────────────┬──────────────┘
      ┌──────────┬──────────┬────────┼─────────┬──────────┐
      ▼          ▼          ▼        ▼         ▼          ▼
 secaudit-   secaudit-  secaudit- secaudit- secaudit-  secaudit-
 recon       attack     detect    patch      verify     report
 攻击面测绘   payload    流量检测   补丁生成   重放+回归   报告+曲线
      │          │          │        │         │          │
      └────┬─────┴────┬─────┴────┬───┴────┬────┴────┬─────┘
           ▼          ▼          ▼        ▼         ▼
 ┌───────────────────────────────────────────────────────────┐
 │ 黑板 workspace/（agent 间唯一契约，状态机驱动）              │
 │ scope.yaml · findings.json · patches/ · verify-results.json │
 │ audit-log.jsonl（全动作审计）· rounds/（每轮快照）           │
 └───────────────┬───────────────────────────┬───────────────┘
                 ▼                           ▼
        ┌────────────────┐          ┌────────────────────┐
        │ edu-lite CMS   │◄─攻击流量─│ mitmproxy 流量镜像  │
        │ 自建靶场(本地)  │          │ 红方出口=蓝方输入    │
        └────────────────┘          └────────────────────┘
                 ▲
                 │ SSE 实时事件流（黑板变更即推送）
        ┌────────────────┐
        │ Aegis Console  │  FastAPI 后端 + React 前端（§12）
        └────────────────┘
```

**双视角同源**：红方全部 HTTP 经 mitmproxy 出站，蓝方读同一份 flow log——同一场攻击，红蓝两个视角，是演示的核心画面（Console 主屏）。

**运行时分层（spine/hands，第 8 项决策，2026-09-23 定 Step Code）**：编排主链——轮次循环、黑板状态机、scope/policy/verify 门禁、审计日志——由 `engine/`（Python）持有，确定性优先不动摇；**Step Code（StepFun 官方开源 CLI，MIT，原生支持 Agent Skills/MCP/Claude Code 插件，支持任意 OpenAI 兼容自定义端点，本地优先）部署在 Spark 作为技能执行层**：每个 secaudit-* skill 经 Runner 适配器以 headless 会话执行（SKILL.md 指令 + 受限工具集 + provider 路由），产物只落黑板、动作全进审计日志。provider 双通道：`local-qwen` → SGLang 30000（补丁生成等主权链路）、`step` → api.stepfun.com（报告/复核/语音）。Runner 接口双实现（StepRunner 主 / PyRunner 极简回退），harness 不可用可整体切换而不动契约。（评估过的备选：DeepSeek Harness——插件化优秀但与赛事无关联，记录于 bench/harness-choice.md）

## 5. 核心设计原则

1. **确定性优先，LLM 只在决策点**。payload 回放是确定性 HTTP，成功判定是响应特征匹配；LLM 只出现在规划、payload 变异、补丁适配、日志语义判定、报告生成五个点。这是时延工程，也是答辩亮点。
2. **漏洞类别即插件**。管线与类别解耦：每类漏洞 = `payloads/ + signatures/ + patch-template/ + functional-tests/` 一个数据包。类别 1-2 手工打磨，3-8 批量接入——大范围在 AI 施工下的可行路径。
3. **黑板是唯一契约**。agent 之间不直接对话，一切经 findings.json 状态机流转（open → patched → verified / regressed / accepted）。可重放、可断点续跑、报告自动汇编、Console 实时渲染。
4. **一切产出皆证据**。每次运行产出审计日志与轮次快照，评分表要的"完整性"由证据链证明。
5. **治理内建，非口头承诺**。scope 校验、payload policy 是机器可执行代码；SkillSpector 自扫报告与 triage 记录进仓库。

## 6. Skill 簇规格（6+1）

| Skill | 职责 | 输入 | 输出 | LLM 用途 | 确定性部分 |
|---|---|---|---|---|---|
| `secaudit-run` | 编排主 skill：scope 校验、轮次循环、汇总 | scope.yaml | 轮次快照、报告触发 | 轮次规划（轻） | 校验、调度、状态机 |
| `secaudit-recon` | 攻击面测绘：端点/参数/上传点/角色枚举 | 目标 URL | surface.json | 页面语义理解 | 爬取、去重、参数抽取 |
| `secaudit-attack` | payload 回放引擎 + 受限变异 | surface + 插件包 | findings（含证据） | 同类语法内变异 | 回放、特征判定 |
| `secaudit-detect` | flow log 实时分析、攻击分类、链还原 | flow.jsonl | alerts.json | 规则未命中流量的语义判定 | 规则特征、session 关联 |
| `secaudit-patch` | 模板补丁 + 上下文适配，git 分支应用 | findings + 插件包 | patches/*.patch | 插入点定位、变量适配 | 模板、lint、diff 生成 |
| `secaudit-verify` | 门禁：攻击重放必须失败 + 功能测试必须绿 | patches + 靶场 | verify-results.json | 失败原因归因（可选） | 重放、pytest、判定 |
| `secaudit-report` | 中文修补报告 + 收敛曲线图 | 黑板全量 | report.md/.pdf | 报告叙述（StepFun 模型） | 数据表格、图表生成 |

每个 skill 按官方规范交付：`SKILL.md`（含 frontmatter、Required questions、防御性禁令）+ `skill-card.md` + `evals/evals.json` + references 渐进披露。命名遵循官方动词/产品前缀风格。

**Runner 契约（skill 驱动机制）**：每个 skill 附 `runner.yaml`——输入/输出黑板路径、允许工具白名单（http/git/files/llm:local-qwen/llm:step）、headless 执行参数与输出 schema。engine 经 Runner 适配器调度（StepRunner 为主，原生加载 SKILL.md），执行产物落黑板、逐动作审计；harness 层不可用时切 PyRunner，skill 契约不变。

## 7. 数据契约（schema v1）

**scope.yaml**（运行前强制校验，不在清单内即拒绝并记审计日志）
```yaml
targets:
  - name: edu-lite-a
    base_url: http://127.0.0.1:8080
    bind: local-only        # 仅回环/本地容器网段
allowed_classes: [sqli, rce_deser, upload_bypass, lfi, xss_stored, ssrf, idor, brute_no_lock]
forbidden_actions: [destructive_write, persistence, outbound_exploit, dos, worm]
max_rounds: 5
```

**findings.json**（条目）
```json
{
  "id": "F-001",
  "class": "upload_bypass",
  "plugin_version": "1.0.0",
  "endpoint": "POST /profile/avatar",
  "evidence": {"replay_ref": "replays/F-001-01.req", "marker": "shell.php uploaded"},
  "severity": "critical",
  "detected_by": {"red": true, "blue_alert_ref": "A-017"},
  "status": "open",
  "history": [{"round": 1, "event": "found"}, {"round": 2, "event": "patched", "patch": "patches/F-001.patch"}]
}
```

状态机：`open → patched → verified`（重放失败+测试绿）；`patched → regressed`（任一不通过，回滚分支，带反馈重试 ≤2 次）；终态 `accepted`（书面接受理由）。

## 8. 靶场规格：edu-lite CMS（自建，命名已定）

一个 ~500 行的迷你校园站，故意复刻 PDF 语料中的漏洞类别。**自建靶场四重收益**：无网络依赖（白名单环境友好）、aarch64 原生、零披露合规问题、功能测试套件自持（"修补不破坏功能"从冒烟升级为有测试集证明）。

- 功能面：登录/会话、新闻列表+搜索、头像上传、个人资料、管理端导出——够 8 类漏洞的注入点，够写 3×功能测试
- 部署：PHP-FPM + SQLite（已定：PHP 贴合 PDF 生态与补丁故事，aarch64 无障碍）
- 双实例：A 集开发调试用；**B 集仅评估用**（不同端点/参数命名），防训练即测试
- git 管理：靶场代码入库，修补只打分支，可一键重置

**8 类漏洞注入点规划**（已确认）：

| # | 类别 | 注入点 | 严重度 |
|---|---|---|---|
| 1 | SQL 注入 | 新闻搜索参数 | high |
| 2 | 任意文件上传绕过 | 头像上传（黑名单校验缺陷） | critical |
| 3 | 反序列化 RCE | 导入/导出功能（序列化 cookie 或文件） | critical |
| 4 | 路径穿越读取 | 附件下载参数 | high |
| 5 | 存储型 XSS | 个人资料昵称/签名 | medium |
| 6 | SSRF | 头像 URL 远程拉取 | high |
| 7 | IDOR 越权 | 资料页 id 参数（改他人资料） | high |
| 8 | 登录暴破无锁定 | 登录接口无速率限制 | medium |

（CSRF、信息泄露作为超额备选）

## 9. 红方规格

- 回放引擎：HTTP 层 requests，占位符 `{{TARGET}}` `{{SESSION}}` 对应官方 "Required questions" 模式；PDF 语料脱敏后作为类别插件的 payload 素材来源（**原始请求包不入公开仓库**）
- **两层攻击设计（D6 归位，实现曾长期只做第一层——见 DECISIONS 附记）**：
  ①**确定性基线**：R1 全类固定弹库重放——保证首轮必 HIT，收敛曲线的数学基线可复现；
  ②**在线红方 agent（R2 起）**：`engine/attacker.py`，**选招不造招**——LLM 只能从
   预制变换菜单（双重编码/关键字混淆/注释插入/参数污染/十进制 IP/序列化配平…）
   选 (母弹, 招) 并给一句话理由，输出严格 schema `{parent, transform_id, rationale}`；
   菜单外决策一律丢弃，每条变体发射前与母弹同过 payload policy，全谱系落
   flow.jsonl/`redagent_ledger.json`（防事后贴金）。每类每轮**一次**批量决策调用
   （关思考、max_tokens 钉死、≤20s 预算），LLM 失败/超时静默回退确定性重放——
   演示永不停摆。provider=qwen（红方同吃本地主权叙事）
- **payload policy（机器校验，非 README 承诺）**：禁破坏性写（rm/drop/truncate）、禁持久化（webshell 落盘目标机）、禁外联利用（回显仅限本地采集端口）、禁 DoS（并发与大包限制）
- 成功判定器：随插件包提供（响应特征正则 / 状态码差异 / 回显标记）
- **prompt-injection 姿态**：靶场抓取的页面/响应内容一律按不可信数据处理，不进入指令通道（recon/detect 实现内容隔离，STRATEGY §5.3）

## 10. 蓝方规格

- mitmproxy addon 落盘 flow.jsonl（红方出站流量全量）
- **四层检测栈**：规则特征（快、确定性，覆盖已知插件签名）→ **Jev 式判断模型**（带校准置信度，接管规则未命中道；时延目标：较 125B 判定低一个量级、可批量异步——**实测数字 D5 出，不提前承诺**；置信度低于阈值则升级）→ Qwen-125B 语义判定（关思考，仅低置信度流量）→ 悬置/人工
- 攻击链还原：按 session 关联请求序列，还原"侦察→尝试→命中"链条
- 指标：检出率、误报率（对 B 集报告）；判断模型另出混淆矩阵 + 校准曲线

## 11. 修补与回归规格

- **三层补丁**：类别级模板（预先验证过，如上传绕过→白名单+随机重命名+目录去执行权限）→ LLM 适配（仅喂漏洞函数±30行小上下文：定位插入点、适配变量名、输出 diff）→ 语义检查（语法 lint + diff 自查）
- **生成路由（本地主权优先、云端增强）**：补丁生成走本地 Qwen-125B（关思考，审计主链路数据不出域）；verify 失败或难例 → 同模型开思考重试；**StepFun 云端池**（0.57s RTT）承担报告生成、轮末复核、语音触发与对比实验列。对外叙事：**双模式**——全本地主权模式 / 云增强模式（涉密场景一键切换）。诊断与修复策略在模板层/黑板层，LLM 只承担适配切片
- **部署形态（第 7 项决策后）**：不再部署本地第二生成模型——并发由 mrq=4 + StepFun 云端承担；判断模型（第 9 项）以 GGUF CPU 前向形态接入（D5）
- 应用：git 分支 `patch/F-xxx`，产出 .patch 入黑板
- **门禁（verify）**：①插件包 payloads 分 **attack / verify 两组**（held-out 组 attack 阶段不使用），门禁对**两组全部重放且必须全部失败**——防"补丁只挡自家 payload"的过拟合（安全评委必问项）②功能测试套件全绿 ③无新增扫描风险。任一失败 → regressed，回滚，带失败反馈重试 ≤2 次，仍败则 `accepted`（附理由）转人工
- 演示保险：一键切换"agent 出方案 + 人工确认应用"降级路径

## 12. Aegis Console（前后端看板，新增正式交付物）

对应评分"项目完整性：前后端完整"与"演示效果"——一份投入，两处得分；演示视频的主要录屏对象。

**后端**（FastAPI，Spark 上常驻）
- REST：黑板读模型（findings/alerts/rounds/patches/audit-log）
- SSE 事件流：黑板每次变更即推送，前端实时渲染
- 控制接口：启动/暂停审计、切换降级模式（人工确认应用补丁）、scope 展示

**前端**（Vite + React + ECharts）
1. **主屏·双视角**：左红右蓝——红方请求流（含 payload 摘要、命中标记）与蓝方告警流（含 **judge 置信度仪表**，第 9 项）同屏滚动，同一场攻击两个视角
2. **findings 看板**：状态机看板（open/patched/verified/regressed 列），卡片显示类别/严重度/证据/轮次
3. **收敛曲线**：轮次-剩余漏洞数 + 攻击成功率下降双线图（ECharts）
4. **补丁 diff 查看器**：语法高亮 diff + verify 门禁结果
5. **审计时间线**：全部 agent 动作流水（治理证据可视化）
6. **语音触发入口**：Step-Audio 说话启动审计（演示开场，见 §13）

## 13. 模型层规格

- **底座**：Qwen3.8-Flash-Next-NVFP4-SSD-Stream @ SGLang（现状即资产，量化与长上下文实测数据直接入 BENCHMARK）
- **时延工程（D1 三实验，决定多 agent 并发策略）**：
  - A：`max-running-requests` 2/4 的吞吐-延迟曲线（MoE 激活 6B，并发内存代价可能小）
  - B：工具调用关思考模式（reasoning token 占比大，是时延主源之一）
  - C：LLM 决策点输出预算（补丁 diff 行数上限、判定输出结构化短格式）
  - D：~~4-bit 7B 专家模型与大模型服务共存验证~~（**已结案**：无需共存，mrq=4 + StepFun 覆盖，见 bench/d1-experiments.md）
- **训练算力：本地 RTX 5090 32GB**（judge LoRA 主力、补丁微调若 D7 触发；Windows 侧 hf-mirror 下载基座；Spark 侧仅 D8 做 ≤1.7B 小模型训练冒烟，大模型服务不中断）
- **CVE 知识库 RAG**：本地向量库（CVE 修复 commit 摘要 + 类别修补模式），供 secaudit-patch 检索；评估方法对齐官方 rag-blueprint / rag-eval skill
- **StepFun 集成（已定案：双点）**：① secaudit-report 用 Step 文本模型生成中文修补报告 ② Step-Audio 语音触发（Console 入口，"开始审计 edu-lite"→ 流水线启动）作演示开场；另任三列表第三列（step-3.5-flash 补丁列对比，A 集初数 D4 出）
- **微调决策门（D7 投票，取代原 Stretch；2026-09-23 晚二次修订）**：①三列对比表第三列为 **StepFun step-3.5-flash**（本地 Qwen125B 零样本 / Qwen125B+RAG / 云端 StepFun 异构对比，A 集先出初数）；②D7 若补丁主链路（本地 Qwen+模板+RAG）可应用率达标 → **不微调**；若格式合规率崩且差距明确 → 微调（LoRA 靶子明确，训练数据用 D2-D7 管线自动积累的成功补丁对）；③**无条件项**：D8 用 Spark 上 sglang venv 跑 100 步迷你 LoRA 冒烟（loss 下降即可），保住"设备端训练"叙事
- **CPU 专家路线已撤销（用户裁决）**：llama.cpp 与 GPU 服务共享统一内存带宽（争用实证风险），且 CPU 解码无时延优势；并发由 mrq=4 + StepFun 云端双路覆盖。llama-cpp-python 仅作**休眠回退**（D7 微调触发且需在 Spark 服务微调模型时启用，SGLang fork 不支持密集模型已证）
- **判断模型（Jev 式 typed-decision judge，第 9 项决策；基座 Qwen3.5-4B）**：受 TypeSafe "System One 模型"概念启发（非生成、类型化决策 + 校准置信度，Accept-Confident/Escalate-Uncertain）。**基座 Qwen3.5-4B（Apache-2.0，2026-02，GatedDeltaNet 混合架构——与 125B 主力同门）+ LoRA + logit 判读**：prompt 停在选择槽，单次前向读类别 token logits → softmax + 温度校准，**构造上零生成零幻觉**。**四层决策栈**：确定性规则 → judge（单次前向、无解码）→ Qwen-125B（关思考）→ 悬置/人工；三个插槽：①蓝方规则未命中道 ②红方 marker 未命中道（治 marker 过拟合）③补丁 apply 前预检。**门禁权威不变**——judge 只做分流提速，永不推翻规则与 verify。服务：GGUF Q4_K_M（~2.5GB）经 llama-cpp-python CPU 前向，**主要成本是 prefill 计算**（对策：短摘要 prompt + 公共前缀缓存 + 蓝方道批量异步）——**时延数字 D5 实测后回填，不提前承诺**；与第 7 项裁决不冲突（禁的是持续解码）；回退：embedding+MLP。**训练数据由管线自产**（飞轮：攻击回放由 marker 标注、蓝方流量由红方 finding+良性发生器标注、补丁由 verify 结果标注）。训练在 5090（LoRA，Windows 侧 hf-mirror 下载基座），LoRA adapter 可随仓库发布（Apache-2.0）。D5 出 v0（蓝方道单任务），D7 出 B 集混淆矩阵+校准曲线，D8 与补丁微调同过决策门。对外表述"Jev 式（受 System One 概念启发）"

## 14. 评估与基准

- **held-out 纪律**：每类漏洞 A/B 双实例，B 集只在 D7 全量评估跑一次；所有对外数字只对 B 集报告
- **D7 前置门禁**：采集任何 B 集数字之前，必须先对 B 集跑验收 harness 且 12/12 全 HIT——"B 集漏洞确实在场"是强制检查而非假设（防 canonical 残留补丁静默污染 B 集，D3 评审 P1）
- 指标：红方检出率、蓝方检出率/误报率、修补成功率、回归通过率、收敛轮次、端到端时延
- **BENCHMARK.md**：官方 uplift 格式——同一评估任务集上"agent 无技能 vs 有 secaudit 簇"对比表
- **收敛曲线**：轮次-剩余漏洞数，随轮次攻击成功率下降曲线——演示核心素材

## 15. 治理与合规

1. README 固定章节：目标隔离说明 + 负责任披露声明（自建靶场、原始 payload 不入库、仅本地授权目标）
2. scope 校验与 payload policy 是代码，可审计
3. SkillSpector 对自家 skill 簇自扫 → 报告 + 逐条 triage（接受理由+缓解）入 references/——用 NVIDIA 自家治理流水线自我举证
4. 技能卡全套（官方 15 字段模板）；尝试 OMS 签名（model-signing 可装则做）
5. audit-log.jsonl 记录全部 agent 动作，报告自动引用，Console 时间线可视化

## 16. 里程碑（最大范围规划，门控推进）

| 日 | 内容 | 出口条件（gate） |
|---|---|---|
| D1 | 决策锁定；仓库骨架+契约代码；时延三实验 | 并发/思考模式参数定版 |
| D2 | **Step Code 部署 spike（Windows 侧打包 → scp → 四项验证：①自定义端点接 SGLang ②SKILL.md 原生加载 ③headless 脚本化 ④**token/墙钟开销对比**：同一任务 harness vs 直连 API，>2 倍则 harness 只走展示面技能（recon/report）、热路径直连；失败即 PyRunner 回退）**；edu-lite A/B 双实例（3 类漏洞+测试套件）；黑板+Runner 联调（含 **judge 飞轮埋点**：回放/流量/补丁的标注字段进 schema） | spike 四项有结论（含开销数字）；B 集就绪 |
| D3 | **分水岭：第 1 类漏洞端到端闭环** | attack→detect→patch→verify→re-attack 清零 |
| D4 | 插件化改造，类别 3-5 批量接入；Console 后端骨架；良性流量发生器；StepFun 补丁列初数 + stepaudio 语音回环 | "类别即插件"验证：新增类别 <0.5 天；三列表之一有初数 |
| D5 | 类别 6-8；蓝方签名自学习（离线蒸馏：红方确认而蓝方漏检→规则入库，"红蓝共演化"）；**判断模型 v0（Qwen3.5-4B LoRA，蓝方规则未命中道，5090 训练）**；RAG（若进度健康） | 8 类全通单轮闭环；judge v0 **训练跑通+出混淆矩阵**（性能门槛留 D7） |
| D6 | **Console MVP 三屏**（双视角/findings 看板/收敛曲线；diff 与时间线为 stretch）+ 时光回放模式 + 多轮收敛循环 + demo.sh + 报告 | 回放一次完整历史运行；demo.sh 一键闭环 |
| D7 | **功能冻结**；B 集全量评估；三列对比表 + **判断模型混淆矩阵/校准曲线**；BENCHMARK.md | 全部对外数字齐 → **微调决策投票（judge / patch 两个对象）** |
| D8 | 门控执行（微调 或 治理展柜）+ 100 步迷你 LoRA 训练冒烟（**≤1.7B 小模型**，Spark 大模型服务不中断） | SkillSpector、技能卡、签名终稿 |
| D9 | 演示视频（Console 录屏为主）、"十日谈"征文、提交四件套 | 提交清单逐项核销 |
| D10 | 缓冲 | — |

## 17. 风险登记册

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| 单并发推理时延拖垮多 agent 演示 | 高 | 高 | 确定性优先架构；时延三实验；视频分段录 |
| 补丁破坏功能 | 中 | 高 | 模板+门禁+测试套件；降级人工路径 |
| 前后端工期挤占主线 | 中 | 中 | Console 用最稳技术栈（FastAPI+React+ECharts）AI 批量生成；D4 起并行推进 |
| 内存挤压 | 低 | 中 | judge 走 GGUF CPU 前向（~2.5GB）；D8 训练冒烟用 ≤1.7B 小模型；大模型服务常驻不中断 |
| 白名单环境依赖拉取受阻 | 中 | 中 | 靶场自建零外部依赖；gitcode/ModelScope 镜像预案；npm 依赖提前打包带入 |
| ~~StepFun API 不可达/无法本地化~~ | **已销账**（2026-09-23 spike：Spark 直连 200/0.57s，见 bench/d2-stepfun-spike.md） | — |
| 演示中 accepted/失败当众发生 | 低 | 高 | 现场只跑 A 集已验证快段（语音触发+首轮攻击）；长流程走时光回放（audit-log 驱动，标注"历史运行"） |
| Step Code 预览版动荡 / npm 源不可达 / **harness token 开销过大** | 中 | 中 | Windows 侧打包 tarball+scp；锁定版本；Runner 适配器 + PyRunner 回退；**spike 第④项测开销，>2 倍则 harness 只走展示面技能、热路径直连 API**；dsh 备选在案 |
| 判断模型数据不足/校准差 | 中 | 中 | 飞轮数据量不足则退化为"仅排序不做终审"；门禁权威在规则与 verify，judge 失准无系统性风险 |
| 评委对"自动攻击"观感 | 低 | 高 | 防御叙事定位"自主免疫"；治理展柜前置 |

## 18. 提交物对照

仓库结构（公开部分）：
```
aegis/
├── README.md            # ≥500字：定位/部署/技术栈/负责任披露
├── skills/secaudit-*/   # 6+1 skill 簇（SKILL.md/skill-card/evals/references）
├── target/edu-lite/     # 自建靶场源码+测试套件
├── plugins/<class>/     # 8 类漏洞插件包（脱敏）
├── engine/              # 黑板、回放、mitmproxy addon、编排
├── console/             # Aegis Console（FastAPI 后端 + React 前端）
├── bench/               # BENCHMARK.md、评估脚本、收敛曲线
└── governance/          # scope 校验、payload policy、SkillSpector 报告
```
比赛四件套：GitHub 仓库 ✅ / B 站演示视频（Console 双视角+收敛曲线+语音触发）✅ / CSDN 或知乎征文（每日开发日志拼装，对应"十日谈"历程）✅ / 团队合影 ✅

## 19. 评审标准采分点映射

| 评审项 | 权重 | 本项目对应证据 |
|---|---|---|
| 实用性、行业落地价值与技术创新性 | 25% | 安全审计数据不出域是行业硬痛点；"自主免疫闭环"突破传统"扫描器出报告、人工去修"思路；§2 平台优势叙事 |
| 智能体与模型优化技术深度 | 25% | 多智能体协同=红/蓝/修补/验证四方经黑板状态机协作；模型调优=量化实测+RAG 对比+（门控）QLoRA 三列表；Skills=官方规范 6+1 簇+治理展柜；差异化=漏洞类别即插件+收敛循环 |
| 项目完整性 | 20% | Aegis Console 前后端完整；一键部署脚本；功能测试套件；audit-log 证据链；README 详实 |
| 平台适配性 | 15% | 推理（SGLang 125B NVFP4）+ 训练（设备端 QLoRA）+ 服务（FastAPI）全栈同机；NVIDIA 栈（SGLang/官方 skills 规范/SkillSpector）；StepFun 双集成点 |
| 演示效果 | 10% | Console 双视角主屏 + 收敛曲线 + 语音触发开场；分段录制保流畅 |
| 赛事征文 | 5% | 每日开发日志 20 分钟投入，直接拼装"十日谈"历程 |

## 20. 决策记录（⚠️ 全部已锁定，详见 DECISIONS.md 第 1-9 项）

1. **项目名**：Aegis / 神盾 ✅
2. **靶场语言**：PHP-FPM + SQLite ✅
3. **8 类漏洞清单**：§8 表已确认（CSRF/信息泄露备选）✅
4. **StepFun 双集成点**：报告生成 + Step-Audio 语音触发（另任三列表第三列）✅
5. **微调**：降级为 D7 决策门 + D8 无条件训练冒烟 ✅
6. **靶场命名**：edu-lite CMS ✅
7. **撤销 CPU 生成模型路线**，双模式架构定案 ✅
8. **智能体骨架 = Step Code**（dsh 备选在案）✅
9. **Jev 式判断模型**（Qwen3.5-4B 基座 + logit 判读）✅

---

## 21. 附录A：比赛官方要求（2026-09 确认版）

### A.1 评审标准（DGX Spark Hackathon）

| # | 评审项 | 权重 | 官方描述 |
|---|---|---|---|
| 1 | 项目实用性、行业落地价值与技术创新性 | 25% | 技术实现、架构与方案具备创新性，充分体现 DGX Spark 平台优势，突破传统思路并解决技术痛点 |
| 2 | 智能体与模型优化技术深度 | 25% | 多智能体协同、模型调优深度、Skills 设计与融合、差异化技术方案 |
| 3 | 项目完整性 | 20% | 功能完整、运行稳定，**前后端完整**、文档规范详实，实现逻辑清晰，可顺利完成演示 |
| 4 | 平台适配性 | 15% | 充分发挥 DGX Spark 平台的**全栈能力**，合理运用 NVIDIA 技术栈、开源模型和 SDK 等工具以及 **StepFun 阶跃星辰模型**的使用 |
| 5 | 演示效果 | 10% | Demo 视频演示流畅、展示清晰、逻辑严谨，直观呈现作品价值 |
| 6 | 赛事征文 | 5% | 参赛成果记录，DGX Spark 黑客松**"十日谈"**开发历程 |

采分点逐条映射见 §19。

### A.2 提交要求（据赛事说明整理）

- **GitHub 公开仓库**：README ≥500 字（项目说明、部署说明、技术栈说明），包含 skill markdown 文件
- **B 站演示视频** + 链接
- **CSDN/知乎征文**链接（"十日谈"开发历程）
- **团队合影**
- 红线：仓库严禁 API key/密钥；AI 生成内容须标注；git 提交历史保持连续（十日谈真实性佐证）

## 22. 附录B：运行底座现状（2026-09-23 实测）

### B.1 设备

- 机型：DGX Spark（GB10 Superchip），ARM aarch64，20 核
- 驱动 580.142（aarch64 Open Kernel），CUDA 13.0
- 统一内存 121GiB（模型服务占用 ~101GiB，**余 ~20GiB = 实验 D 的预算**）
- 磁盘 916G：已用 382G，可用 488G
- 已连续运行 21 天，负载 ~1.0，稳定
- 管理通道：SSH 免密（**公网 IP/端口/API key 公开前必须脱敏**，见 B.6）

### B.2 模型服务（主推理底座，已就绪）

- **模型**：Qwen3.8-Flash-Next（Qwen4 架构首个开放预览）——125B MoE + 51B PLE N-gram 嵌入表，激活 ~6B/token，Gated DeltaNet 线性注意力，多模态
- **量化与布局**：NVFP4；SSD-Stream（总 150GB，48GB PLE 表放 SSD 懒加载 io_uring，运行时常驻仅 ~64MB）
- **服务**：SGLang v0.3.0（Spark 定制 commit + ssd-stream 扩展），OpenAI 兼容 API 端口 30000，强制 API key 鉴权，262K 上下文，MTP 投机解码 3/1/4。**D1 起定版配置：`--max-running-requests 4`、`--cuda-graph-max-bs-decode 4`、绑定 127.0.0.1（原 0.0.0.0 已修）**
- **关键路径**：venv `~/.local/share/sglang-ssd-stream/venv-0.3.0`；权重 `~/models/ssd-stream`；启动脚本 **`~/bin/start_ssd.sh`（D1 持久化，回滚 `rollback_ssd.sh`）**；日志 `~/logs/serve_d1.log`；`HF_HUB_OFFLINE=1`
- **性能实测**：解码短上下文 23-27 tok/s、>32K 约 13.5 tok/s；prefill 2100-2500 tok/s；250K prompt TTFT 115.8s；大海捞针 8K/32K/64K/128K/250K 全通过
- **备用资产**：NVIDIA 官方 NVFP4 权重 132.7GB（`~/models/Qwen3.8-Flash-Next-NVFP4`，可供 vLLM）；unsloth GGUF UD-IQ4_XS 约 94GB

### B.3 训练算力

- 本地 RTX 5090 32GB（Windows 主机，可代理访问 GitHub）：承担微调数据构建与 QLoRA 训练（§13），产物上传 Spark

### B.4 网络连接矩阵（2026-09-23 复测，修正旧"白名单"认知）

- **可达**：api.stepfun.com（**直连可用，chat 0.57s RTT，疑为比赛专门放行**——StepFun 集成零障碍）、gitee.com、pypi.tuna、mirrors.aliyun、modelscope.cn、百度
- **不可达**：GitHub、HuggingFace、registry.npmjs.org
- 对策：pip 一律走 aliyun/tuna 源；llama.cpp 走 pip 的 llama-cpp-python（源码编译，自带 OpenAI 兼容 server；gitee `mirrors/llama.cpp` 不存在已排除）；前端 npm 依赖在 Windows 侧打包带入；模型权重走 ModelScope；详见 bench/d2-stepfun-spike.md

### B.5 运维待办（状态更新 2026-09-23）

1. ✅ ~~模型服务监听 0.0.0.0:30000~~——D1 已改回环绑定，外部访问走 SSH 隧道（安全审计项目自己的底座不能带伤，答辩可讲）
2. ✅ ~~启动脚本在 /tmp~~——已迁移 `~/bin/start_ssd.sh` 并备 `rollback_ssd.sh`（原配置一键回滚）
3. ✅ ~~长上下文基准脚本迁移~~——已复制至 `~/bin/longctx_bench.py`

### B.6 公开前脱敏清单

SSH 公网 IP/端口/用户名、API key、日志中可能含的请求内容、BENCHMARK 中的内网地址——README 与公开 spec 逐项过一遍
