# JUDGES.md — Aegis 评委指南

> **Aegis（神盾）**：DGX Spark 上的本地多智能体安全审计与自修补闭环——红方 agent 攻击授权靶场，蓝方实时检测，修补层生成补丁，回归验证重放打不动才收工；**当前主张限于本次受测案例通过复测**（历史浸泡轮次单列）
>
> 本文档是穿过本项目的**最短评审路径**，不含开发历程（历程见十日谈征文）。本文每个数字旁标注仓内 JSON 出处；`claims.yaml` 断言脚本（tests/test_claims.py）对全部上墙数字逐条兜底——**你可以不信我，但你可以当场检查我**。

---

## 新增入口：单案例闭环（2026-09-26，可当场验）

旧入口（`bash demo.sh`：八类×多轮收敛）**保留可用**；新的单案例纵向闭环把两个最容易被打假的东西显式化：
**模型真的改了代码**，以及**判官与规则各自独立判读同一交换**。

- 入口：`http://<Spark>:8000/demo`（作战大屏），或 `POST /api/demo/runs`，
  或 `python -m engine.demo_case --runs-root "$PWD/workspace/runs" --base http://127.0.0.1:8081 --case-id sqli --cleanup-policy restore`
- 流程：正常业务 → 注入成功 → 规则 + JEV 判官各自判读 → 本地 125B 模型生成补丁 →
  三门禁（改动范围 / 危险模式 / 云端异构复核，fail-closed）→ 部署 → 同一注入复测打不动、正常业务仍通过 →
  候选样本入队 → 靶场恢复；全程同一 `run_id`，41 条事件可逐条 curl。
- **真机战绩（截至 2026-09-26，14 轮，可当场查）**：**8 succeeded / 1 partial / 5 failed**。
  失败不藏：2 轮靶场未起、3 轮云端复核不可用被 fail-closed 拒（部署源 hash 实测未变）。
- **终态六条件（写死在代码里）**：`repair_outcome=verified` + 判官 `status=ok` + 样本已入队 +
  功能测试 passed + 清理完成 + 无降级原因；缺一就降 `partial` 或 `failed`。
- 逐流裁决实测：攻击流 `p_attack=0.9996/1.0`→`attack`；良性流 `0.0`→`benign`；暖态 66–119ms；
  模型身份 `judge_v3_adapter`，设备 `NVIDIA GB10`（`GET :30002/metadata`）。
- 单轮实测耗时：patch ~70s + review ~10–20s 为主，整轮约 2–3 分钟（`summary.json.stage_durations`）。
- 纯本地回放：`⟲ 彩排回放` 选任一成功率 run，逐条重放且**零网络写请求**（不发起攻击、不调模型）。

**本轮诚实边界（延续本文“Honest Scope”纪律）**：

- ✗ 不主张“数据出域 0 字节”在本入口成立：云端异构复核会发出**补丁 diff**（页面已标注边界）；
  全本地复核配置尚未验证。
- ✗ 不主张“判官是多审查者”：复核多票是**同一个模型的两种渲染 + 一次复测**的多数决。
- ✗ 不主张“样本已用于训练”：只进**候选队列**，需人工审核后才可能进新数据集版本。
- ✗ 不主张“演示验收完成”：真机主流程/降级/失败三态有证据，前端数据层有 A08–A14 运行态验收（34 条断言），
  但**真浏览器渲染层未在真机验过**，A04/A06/A07 的真机故障注入未做。
- 已知噪声：判官对 `/login` 这类无上下文流量会判 `attack`（实测 `p_attack=0.9996`），
  按验收项 A03 **如实显示不预过滤**。

> 现场怎么演、屏上怎么读、被问到怎么答：见 `docs/DEMO-HANDBOOK.md`。

---

## 三分钟评审（Three-Minute Review）

1. **看视频**（≤4 分钟，时间戳章节表在视频简介）——高潮前置：收敛曲线归零 + 物理断网跑全闭环的镜头。
2. **读 README 首屏**——一句话结果行 + 数字表（每个数字可跳 EVIDENCE.md 对应行）。
3. **翻 EVIDENCE.md**——"宣称 → 仓内 JSON 路径 → 复算命令"三列映射，随机抽 5 行跑得通。
4. **看最终演示与材料**：演示视频 `workspace/video/aegis-dgx-spark-demo-20260927.mp4`（SHA256 `45514fad…`，见 RELEASE.md）与 **RSI 9 页版 deck**（SHA256 `a4eb9107…`）；仓内旧六页 deck `docs/aegis-judge-deck.pptx` **仅作历史，已作废**。
5. **无 Spark 时的只读路线**：不开 golden-run（该回放包已作废）；请按 `REVIEW-README.md` 的三条入口行事——本条只承诺"能看"，不承诺"能在此环境跑完整攻击链"。

---

## 评分映射（100 分 Map）

| 官方评分项 | 分值 | 状态 | 最快证明 |
|---|---|---|---|
| 实用性、行业落地价值与技术创新性 | 25% | **Implemented + Measured** | 自建靶场与"修补到打不动"的收敛闭环；README 首屏数字表；收敛曲线（workspace/rounds/） |
| 智能体与模型优化技术深度 | 25% | **Measured** | judge 自训三臂对照：**参赛 v3** 留存 B 卷 **0.9992**（召回 1.0 / 弃权 0）vs **v1 对照组 0.9989**（CI 0.9976–0.9998）vs 零样本 **0.8431**（`bench/train/results/d7_b/{v3_eval_b_clean,eval_b_clean}.json`，均为 **0.2–0.8 离线口径**）；三级证据阶梯；ECE 0.0006；训练峰值 30.8 GiB 一手记录。**注意**：v3 的 B 卷请求面按改名表归一后与训练面同形 ⇒ 只证命名/响应形态鲁棒，不称未见攻击泛化 |
| 项目完整性 | 20% | **Implemented + Measured** | 前后端完整 Console（FastAPI + 三栏工作台）；demo.sh 一键演示；**soak 1653 轮连续运行 0 失败**（bench/results/soak_5h/soak_summary.json）；本地单测 + Spark 40/40 |
| 平台适配性 | 15% | **Implemented + Measured** | 推理（SGLang 125B NVFP4 @ 30000）+ 训练（LoRA）+ 服务（Console:8000）+ 数据（本地向量库/SQLite）全栈同机；StepFun 双轨：云端报告/异构复核 + 补丁难例对比列（两次采样 5/7、4/7 CONVERGED，bench/spark/stepfun_column.csv + D6 复测） |
| 演示效果 | 10% | **Implemented**（视频 D9 交付） | Console 三栏工作台实时录屏 + 时光回放；分镜脚本已定稿（docs/video-script.md） |
| 赛事征文（十日谈） | 5% | **Implemented**（D9 交付） | 每日"问题→实验→数据→决策"四段式 + 每日一败（docs/ten-days.md），全部带 commit 锚点 |

> 注：`Implemented` / `Measured` 仅描述现有证据所在，不预先给分——评分权完全在评委。

## 建议检查点（What To Inspect）

1. **拒绝镜头**：payload policy 与自由造招双闸的拒案留痕（dataset/raw/gen_freeflows*.jsonl）——自动攻击系统的可信度在于它拒绝打什么。
2. **修补门禁**：LLM 产物必过三重审查（diff 边界 + 后门模式 + 异构复核，fail-closed）——越界 diff 被当场否决的实录在 runloop 归档。
3. **防泄漏纪律**：judge 训练集家族级切分（零重叠 + 同族标签无冲突双硬断言，dataset/build_judge.py）；级 1 开卷证据主动归档为反面教材（bench/train/results/tier1_sample_split_leaky/）。
4. **held-out 纪律**：B 集已"永封"（评估只读、不再采集）；Console 拒打 B 实例（/api/run instance=b → 403）。
5. **治理代码**：scope.yaml 校验与 payload policy 是机器执行的代码而非 README 承诺（engine/scope.py、governance/payload_policy.py）。

## Honest Scope（诚实边界）

- ✓ 主张：**改名鲁棒性、未见响应形态、第二实例闭环可复现**（**参赛 v3** 留存 B 卷 acc **0.9992** / 召回 1.0 / 弃权 0；**v1 对照 0.9989**；均 0.2–0.8 离线口径）。
- ✗ 不主张："未见攻击类型泛化"——B 集与训练集同分布，此言不实。
- ✗ 不主张："内存无漂移"——soak 内存列采集为空（零失败 ≠ 测过内存）。
- ✗ 不上墙：级 1 证据（切分含孪生泄漏，100% 是开卷成绩）。
- StepFun 补丁难例列为对比实验列（两次采样 5/7、4/7 CONVERGED），rce_deser 一类云端列失败（已破案：全文件长输出模式），不藏账。

## 五分钟亲手验证路径（有 Spark）

```bash
git clone <repo> && cd aegis
bash demo.sh          # 自检→重置→8类×2轮收敛→战果摘要（~1 分钟）
# Console: http://127.0.0.1:8000 —— 三栏工作台 + ⟲ 时光回放
pytest tests/ -q      # 全量门禁（含 claims 断言）
```

## 五分钟验证路径（无 Spark）

`dataset/raw/golden-run/`（golden-run 回放数据包）：本机起 Console 指向该目录 → 时光回放逐轮拖动一场完整战役（8 类发现→修补→verified，红蓝双视角同源）。构建方法见 dataset/raw/golden-run/README.md。
