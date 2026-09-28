# D1 决策锁定（2026-09-23）

> 2026-09-23 D1 复盘后采纳 STRATEGY.md（夺冠策略文档，**不入公开仓库**，已进 .gitignore）；第 5 项决策据此改写，SPEC §11/§13/§16/§17 已同步。

按 SPEC §20 六项决策锁定如下（spec 推荐值；任何一项可被推翻，改名/换向成本已评估）：

| # | 决策 | 结论 | 理由摘要 |
|---|---|---|---|
| 1 | 项目名 | **Aegis（神盾）** | 辨识度好，后续改名零成本（全局替换） |
| 2 | 靶场语言 | **PHP-FPM + SQLite** | 贴合 PDF 语料生态与补丁叙事；aarch64 无障碍 |
| 3 | 漏洞清单 | **8 类**（sqli / upload_bypass / rce_deser / lfi / xss_stored / ssrf / idor / brute_no_lock） | 高中低严重度齐全，演示层次感 |
| 4 | StepFun 集成 | **双点都要**：secaudit-report 中文报告 + Step-Audio 语音触发 | 硬性要求，双点覆盖"使用深度" |
| 5 | 微调 | **降级为 D7 决策门**（原"保留 stretch"作废，STRATEGY §5.1） | 实验 B 后时延论点弱化：125B 关思考秒级出补丁；三列表第三列先放 7B 零样本，差距明确才微调（训练数据=管线自动积累的成功补丁对）；D8 无条件跑 100 步迷你 LoRA 冒烟，保"设备端训练"叙事 |
| 6 | 靶场命名 | **edu-lite CMS** | 迷你校园站，8 类注入点齐备 |

## 第 7 项决策（2026-09-23 晚，用户裁决）：撤销 CPU 专家路线

- **裁决成立**：llama.cpp CPU 推理与 GPU 服务共享统一内存带宽（我此前"CPU 池零冲突"的说法**错误，撤回**），且 CPU 解码不快于 125B 关思考路径——只剩拖累
- 并发由 **mrq=4（实验 A 定版）+ StepFun 云端池（0.57s RTT）** 双路覆盖；实验D 以"无需共存"结案
- 三列对比表第三列改为 **StepFun step-3.5-flash**（本地/本地+RAG/云端异构对比），顺带加深 StepFun 使用（平台适配采分点）
- 架构叙事升级为**双模式**：全本地主权（补丁生成等主链路永不外发）/ 云增强（报告、复核、语音）——数据不出域从约束变成功能
- llama-cpp-python 保留为**休眠回退**（仅当 D7 微调门触发且需在 Spark 服务微调模型时启用），关键路径零投入

## 第 8 项决策（2026-09-23 晚，用户提议，两轮定稿）：智能体骨架 = Step Code

- 候选两轮评估（bench/harness-choice.md）：先议 DeepSeek Harness（插件化优秀），后引入 **Step Code（StepFun 官方开源 CLI）**——**原生支持 Agent Skills/MCP**（直接命中"Skills 设计与融合"25% 采分项）+ StepFun 官方身份（直接命中平台适配 15% 采分项）+ 任意 OpenAI 兼容自定义端点（保住本地主权链路），双重得分胜出；dsh 降为在案备选
- **架构红线（spine/hands 分层）不变**：轮次循环/黑板状态机/scope/policy/verify 门禁留在 engine（Python），harness 只做技能执行（headless 会话 + 受限工具 + provider 路由）
- **skill 驱动机制**：每 skill 附 runner.yaml 契约（黑板 IO 路径、工具白名单、provider、输出 schema）；Runner 双实现 StepRunner/PyRunner，harness 失效整体切换零成本
- provider 路由：`local-qwen`（SGLang 30000，主权链路）/ `step`（api.stepfun.com，报告/复核/语音）
- 部署路径：Windows 侧打包 → scp（npm 源在 Spark 不通）；D2 spike 三项验证（自定义端点/SKILL.md 加载/headless），任一失败即 PyRunner

## 第 9 项决策（2026-09-23 晚，用户提议）：训练 Jev 式判断模型

- 概念：TypeSafe AI 2026-09-15 发布的 "System One 模型" Jev——非生成、类型化决策 + 校准置信度、Accept-Confident/Escalate-Uncertain；我们自训 **Jev 式**判断模型（受概念启发，不称等价）
- **为什么这次微调能成**：判断是纯分类/校准任务（小模型主场，非代码生成）；训练数据由管线自产（marker 标注回放、红方 finding+良性发生器标注流量、verify 结果标注补丁）——红蓝对抗副产品即监督信号
- **四层决策栈**：规则 → judge（毫秒级）→ Qwen-125B → 悬置/人工；插槽：蓝方规则未命中道 / 红方 marker 未命中道 / 补丁预检。**门禁权威不变，judge 只分流不终审**
- 形态：embedding+MLP 头（~100-600MB，CPU 前向，不违第 7 项裁决——那是针对 7B 生成模型抢带宽）；训练在 5090；D5 v0 → D7 混淆矩阵+校准曲线 → D8 与补丁微调同过决策门
- 评分命中：25% 调优深度（校准曲线是最硬证据）+ 差异化（System One 进安全闭环，全场唯一）+ 演示（置信度仪表）
- **基座定版（2026-09-23 补）：Qwen3.5-4B**（Apache-2.0，与 125B 同为 GatedDeltaNet 家族）+ LoRA + **logit 判读不生成**（单次前向读类别 token logits → softmax+温度校准，构造上零幻觉）；服务 = **GPU bf16 合并权重 + LoRA 适配器（transformers 单次前向读首 token logits）**；（以下路线叙述已作废，仅存历史）~~原拟 GGUF Q4 经 llama-cpp-python CPU 前向（每次判定≈10-15ms 带宽，批量后处理）~~ —— **GGUF/llama.cpp 路径从未实测，任何“GGUF 单发时延”数字均不得引用**（参 EVIDENCE.md）；训练数据下载：Windows 侧 hf-mirror，服务权重走 ModelScope（unsloth GGUF）；回退 embedding+MLP

附加锁定（D1 实验定版，2026-09-23，详见 bench/d1-experiments.md）：

- **服务并发参数**：`--max-running-requests 4` + `--cuda-graph-max-bs-decode 4`，绑定 127.0.0.1（原配置回滚：`~/bin/rollback_ssd.sh`）
- **思考模式策略**：agent 决策点默认 `enable_thinking=false`（13 倍墙钟差：17.4s→1.3s，且思考开时 512 上限内正文为零）；仅难例补丁升级思考模式并放宽 max_tokens
- **吞吐认知**：并发不扩展吞吐（~23 tok/s 恒定），系统时延靠"减 token + 双模型分池"
- **specialist 服务路线**：~~SGLang fork 仅支持 ssd-stream 格式 → 改用 llama.cpp CPU 路线（Q4_K_M GGUF + llama-server @ 30001），D2 验证~~（**已被第 7 项取代**；llama-cpp-python 后被第 9 项以"judge 单次前向 logit 判读"形态重新启用）
- **附带修复**：模型服务公网暴露（0.0.0.0:30000）已改为回环绑定，外部访问走 SSH 隧道

## 追加：红方 agent 归位（2026-09-24 D6 深夜，用户质询"红队怎么成脚本了"后拍板）

- **漂移根因**：SPEC §1/§9 一直要求红方智能体 + LLM 变异，实现侧长期只做
  确定性回放（变异引擎被挪去离线产 judge 数据），且**偏离未回写文档**——
  制度教训：实现偏离设计必须当日挂账请用户拍板，不许静默降级
- **归位设计=两层**（详见 SPEC §9 改写与 `bench/d6-redagent.md`）：R1 确定性
  基线不动（收敛曲线数学锚点）；R2 起红方 agent **选招不造招**：预制变换菜单
  （预验证 policy/语法）+ LLM 批量决策（每类每轮一次、≤20s 预算、失败静默
  回退轮换）+ 严格 schema `{p,t,why}` + 全谱系落盘
- **验收即护栏**：①菜单外决策必丢（测试锁）②变体必过 payload policy（测试锁
  全菜单零拒）③演示不停摆（回退路径已实弹自发触发过一次：upload_bypass）
  ④理由先验写入（防事后贴金）——四条全绿才进 D7 冻结
- **首战账**：22 变体 0 绕过（补丁顶住自适应对抗）；125B 实时选招 7/8 类；
  直播徽章 `⟡R2·<招>` 上 Console 红栏
- 范围锁死：在线蓝方签名自学习**不做**（保持 D5 离线蒸馏叙事）；红方 agent
  仅打 A 实例（B 集门禁纪律不破）
