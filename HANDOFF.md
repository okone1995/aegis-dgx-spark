# HANDOFF — Qoder 会话接手施工以来的全部变更（前任编码智能体 zcode 请读此文件）

> 交接时间：2026-09-24（D5 午后 → D6 前夜）。
> 分工变更：用户决定 **Qoder 会话（本文档作者）接手施工，zcode 退场待命；评审职能交给独立评审 agent**。
> 你的 git 历史停在 `0f475a6`；本文档覆盖 `0c94aee` 起至本文件最近一次更新时的全部提交
> （评审 P2 修正：范围口径改"至 HEAD 随批滚动"，精确清单以 `git log --oneline 0c94aee..HEAD`
> 为准，不在此维护静态笔数）。所有变更均经过 "独立评审 → 返工 → 复核"闭环，
> 评审发现记录在 commit message 与 `bench/d5-stepfun-column.md`。

## 1. 你现在接手时的世界变了什么（最重要的先说）

1. **`deploy.sh` 契约变更**：固定 sleep（0.5s/1s）全部替换为就绪探针——端口释放轮询（上限 5s）、
   `/login` HTTP 200/302 轮询（上限 10s、50ms 间隔、curl `-m 2`）、asset_server 启动也纳入等待。
   **失败语义从"静默假就绪"变为"显性 FATAL + 非零退出"**。runloop 的补丁后重部署走同一脚本，
   已确认不破任何 stdout 解析（runloop/harness 均 DEVNULL）。单实例部署时对另一实例做非致命 WARN 交叉检查。
2. **Console `/api/run` 是破坏性变更**：`instance` 只接受 `a`——打 `b` 返回 **403**（评审 P1-A：
   演示面板不得成为 B 集污染通道，held-out 评估永远走命令行 runloop）。非法形状（class/llm/instance
   的错值）先行 400。前端不传 instance，无影响。
3. **Console 后端修了一枚演示日炸弹**：`_run_lock` 原本 acquire 后永不 release（一次触发后全站 409），
   已补对偶释放 + 线程启动失败还锁 + `running` 置位挪进持锁区；SSE 生成器 asyncio 化（同步版会随
   EventSource 重连耗尽 anyio 线程池）。**Spark 上 8000 端口现跑的是新码**（我重启过两次）。
4. **`app.php` 两处微调**：局部变量 `$pdo_sig`→`$sig`（app.php:31，作用域无冲突、零模板锚点引用，
   已实测重部署无碍）；除这两行外 canonical 与你的 `0f475a6` 一致。
5. **远端 `~/aegis/.pristine/` 被我删过一次**（内容含旧 `$pdo_sig` 源，已过时）：下次批量脚本运行会
   按守卫重建——因远端非 git 仓，走 `WARN + 无标记即快照` 降级路径（见 §4 运维铁律）。

## 2. 提交清单（自你的 `0f475a6` 之后，git log 可查全文）

| SHA | 一句话 |
|---|---|
| `0c94aee` | 归一化过闸 `_normalize`（化妆 hunk 不触发窗口拒绝，**窗口规则本身未放宽**）+ 正反两条测试锁 + llm.py 4000→8000 / 复核 400→1500 + 批量脚本初版守卫 + 结果文档骨架 |
| `d0b582b` | 你的 Console 骨架正式入仓（+三参白名单） |
| `916e468` | 独立评审返工：P0 锁泄漏、SSE async、后门扫描补吃原始 diff（归一化盲区：heredoc/字符串内尾随空白在 PHP 有语义）、脚本按类备份、`.pristine` 加 git HEAD 锚定、测试/前端小修 |
| `af2705f` | 二轮复核：`git diff` 显式对 HEAD（裸 diff 漏 staged）、`rm -f` 陈旧备份、测量基线偏差记档 |
| `56c4374` | **StepFun 补丁列初数落盘**：CSV 从 `/tmp` 抢救入仓 `bench/spark/stepfun_column.csv`；脚本 git 守卫适配 rsync 部署环境 |
| `0326427` | Spark 全量 pytest **40/40 绿**验收记录 |
| `59e979d` | 评审 P1-A（instance=b→403）+ P1-B（就绪探针）+ P2（`$sig` 改名） |
| `e8c6780` | 实弹验收：xss 连跑 5/5 CONVERGED 零假阴性、403 实测、注释 pdo_sig 残留清零 |
| `a662c10` | 三轮评审返工：deploy 隐性健康门恢复（WARN 级）、kill `\|\| true`、curl `-m 2`、400/403 语义分层 |
| `9205707` | 停机事故记档 + 运维铁律（见 §4） |

## 3. 新增的硬数据（进三列表的）

**StepFun 补丁列（坏锚点法，7/8 类）**：**5/7 CONVERGED，收敛墙钟 28.9–50.4s，均值 ≈36.1s**
（对照：模板 2.8s、本地 Qwen 单发 57s）。rce_deser 稳定空改写（2/2 复现，待查，疑云端对反序列化
内容的保守行为）；xss_stored 语法错被 php -l 门禁拦截（正面素材）。方法与三轮故障史全文见
`bench/d5-stepfun-column.md`——**该文档 §6 就是当前权威待办清单**，不要另立账。

## 4. 运维铁律（一天内三次实战换来，违反必翻车）

1. **Git-Bash 发 ssh：远端命令一律单引号包裹**。双引号串里 `\$HOME` 会被本地层展开成
   `/c/Users/<user>**pkill/pgrep 模式串不得出现在同一行 `bash -c` 命令里**（模式匹配到自己的 cmdline，自杀式
   exit 255——D2 的 pkill 教训在 ssh 一行式里的变体，我踩了两次才学乖）。杀服务用
   `ss -tlnp | grep ':PORT ' → pid → kill`。
3. **远端同步方式**：`~/aegis` 非 git 仓，用 `git archive HEAD | ssh ... 'tar -x -C ~/aegis'`
   （只覆盖跟踪文件，workspace/.pristine/venvs 不动）；大改前先
   `tar --exclude=workspace -czf ~/aegis_pre_<sha>.tgz`。已有备份：`~/aegis_pre_56c4374.tgz`。
4. ssh 一行式里 `setsid nohup ... &` 起守护进程会让本机 ssh 挂住（fd 占通道）——**客户端断开
   不影响远端服务**，超时后新开会话验证端口即可，不必回头救。
5. Spark 路径速查：php `~/tools/php/php`；venv `~/venvs/aegis/bin/python`；uvicorn 日志
   `~/logs/console.log`；素材服务 30010、靶场 A=8081/B=8082、Console=8000。

## 5. 施工流程变化（用户拍板的新制度）

每批提交须过**独立评审 agent**（general-purpose 子代理，只读审 diff），流程：
提交 → 评审（P0/P1/P2 分级，文件:行号+修法）→ 返工 → 二轮复核。今天三轮跑下来，评审抓出的
真问题包括我埋的 P0 锁泄漏和未声明的行为收窄——**这套闭环现在是项目纪律的一部分，答辩素材**
（"施工者-评审者分离"），退化使用等于自废卖点。

## 6. 当前底座快照（你回来时应该看到的状态）

- 远端 `~/aegis` 同步口径（D6 深夜）：主干已到 `38e689f` 附近（含归档制脚本），
  其后本地未同步提交以 `git log` 为准；**同步纪律补条：远端有进行中任务时禁走
  `git archive` 整仓覆写，改 scp 白名单文件**（本轮实证过一次覆写险情，
  见 `bench/d6-blocked-flywheel.md` §5）。**对照臂陷阱（D6 深夜实证）**：
  `eval_logit.py --adapter` 缺省曾指向真实存在的 tier2 adapter，忘传时**静默
  拿微调权重冒充零样本**（0.9969 差点入册成底座成绩）——缺省已改为空，
  纪律：对照组必须硬校验结果 JSON `model.adapter=null`，数字入册前跑一遍
  出处断言脚本。旧注：D5 快照 `9205707` 已过期
- 服务：A 实例 8081 在听（新 deploy.sh 部署，`$sig` 版源）；B 实例 8082 在听
  （**D7 采集时跑的是 `$pdo_sig` 旧码** → 那次考卷的 A/B 差异不止字段名，已在
  `bench/d7-b-instance.md` 如实披露；09-25 采完后已用当前 src `deploy.sh b` 刷新，
  故当时那个 B 环境**不可再现**，B 卷=一次性快照，复算只读已入仓 raw）；
  Console 8000 跑新码；30010 素材服务在听。
- 遗留实验场：远端 `~/aegis/workspace_selftest`（5/5 复验用，可删）；`/tmp/xss_run_*.log`、
  `/tmp/stepfun_column.csv`（后者已入仓，可删）。
- Console 的 workspace 读 `~/aegis/workspace`（演示数据源），自测一律另开 workspace 目录。

## 7. 未决与路线（详单以 `bench/d5-stepfun-column.md` §6 为准）

**judge 训练线（D6 出口，2026-09-24 傍晚已跑通；外部评审 P1-1 修复经两轮迭代）**：
`bench/train/{train_lora,eval_logit}.py`（本机 conda env aegis / 5090 bf16 LoRA 不量化——
用户拍板，README 已回写；底座 Qwen3.5-4B 在 `C:/Users/<user>/models/` 仓外）。
**首轮 holdout 按 sample_id 切存在孪生泄漏（评审 P1-1）**，旧证据整体归档
`bench/train/results/tier1_sample_split_leaky/`。切分 v2＝**家族键按折叠请求内容全局
归族（去 source/文件名/cls 盐）**，双硬断言（家族零重叠＋同族标签无冲突）＋外部复检
（全量折叠输入跨侧交集 0）。级 2 家族级 holdout 1140（train 6858，家族 2192+457，
wave4 入集后该 run 证据轮换至 `results/tier2_family_noblocked/`，新集 9264 复训=级 2b
在册推进）：微调/零样本对照与三级证据阶梯数字以 `dataset/judge_v0/README.md` 与
`bench/train/results/*.json` 为准，**文档数字不许凭记忆改动**。
评审闭环：首轮 P0=0/P1 全修（426cdc0），二轮 README"8/8"无出处 P0+两 P2 返工（592d906），
外部评审 P1-1/P1-2/P2 三件在 1317a60/7dd2eee 及其后返工批落地（train_lora.py 新增
`peak_vram_gib` 一手显存字段即评审 P1-2 的源化修法）。

**D6 飞轮落盘修复（已落地，38e689f）**：背景是 `stepfun_patch_column.sh` 旧版每轮
runloop 前删掉 `~/aegis/workspace`，把飞轮产物（flow.jsonl/patches）连锅端（D5 真丢过
一批；D6 开工复查再次实证 patches/ 空壳）。修法即既定安全口径"只追加"：workspace
按轮 `mv` 进 `dataset/raw/runloop_archive/<时间戳>_<类>_<结果>/`，**删除行为随归档
落地已从脚本移除**（本会话全程未执行过任何 `rm -rf`）。归档即数据：本轮 4 个
CONVERGED 归档的 F-001.patch 就是 blocked 态的补丁来源（`apply_blocked_state.sh`）。
配套复测：StepFun 补丁列 D6 重跑 **4/7 CONVERGED**（sqli/upload/idor/ssrf，墙钟
28.8–54.2s 均值 39.65s；xss 越界 diff 被三重审查拦=门禁正证、lfi 异构复核 API 瞬时
故障、rce_deser 沿用 D5 悬案在册）；blocked 全量重放 +1266 条入集（judge 总量
**9264**，压制账与施工记见 `bench/d6-blocked-flywheel.md`），v1 复训+三臂评估
已收口（级 2b：v1 0.9953/悬置带 0%、真零样本 0.8109、tier2 交叉考 0.9969，
数字与事故复盘全在 judge README 级 2b 段与 `results/tier2b_family_v1_blocked/`）。
D6 剩余大头：Console keep-patch 通路（marathon 引擎侧 `--keep-patch` 旗标已随
ecbea6d 在场，**Console UI 侧接线未核，在册待办**）+ 多轮累积收敛演示 + 时光回放
+ demo.sh（已随 marathon 批落地，待复核）；**红方 agent 归位已完成（D6 深夜，
SPEC §9 两层设计，见 `bench/d6-redagent.md` 与 DECISIONS 追加条目）**；中段插
rce_deser 空写查因（最小复现 prompt 直发 StepFun，Spark 可达 api.stepfun.com）；
lfi 可单类重试；评审在案 P2：runloop 主流程 try/finally 兜底 restore、diff_bounds
锚点全失静默免检、flow.jsonl `marker_hits`/`method` 字段与前端对齐。brute_no_lock
坏锚点适配——二轮评审判断维持 7/8 边界声明即可，不必硬凑。

## 8. judge v0 数据账（D5 深夜产出 → D6 计划，用户确认过的口径）

**D5 已入仓**（`dataset/judge_v0/`，README 有全部分账与复现命令）：

| 类别 | 数量 | 性质 |
|---|---|---|
| judge 流量训练集 | **9264 条**（家族级切分 v3 train 7984/holdout 1280，attack:benign≈70:30；wave4 blocked 已并入，D5 的 7998 为其子集账） | 标签源①②，漏洞态+补丁态双分布 |
| StepFun 补丁列测量 | D5 首测 5/7 + D6 复测 4/7 CONVERGED（28.8–54.2s），逐轮归档 | 答辩硬数据（两次采样口径都摆） |
| 测试与竞态战果 | 40/40 绿、xss 探针根治、`file://` SSRF 读出 /etc/passwd | 工程+采分素材 |

漏洞态流量已**合理饱和**：edu-lite 仅 8 端点，wave3 变异网格（3556 payload，
`sqli 1200/lfi 120/rce 200/upload 55/brute 640` 全命中，xss 28/112——miss 者以
"意图攻击+环境未得手"身份入集）之后去重器大量折叠，**再加同类网格=注水**，别做。

**D6 的四片新矿**（每片都是 D5 不存在的新分布）：

| 数据块 | 为什么是新分布 | 状态 |
|---|---|---|
| 补丁态 blocked 流 | "防御在场时攻击被掐死"的响应分布，运行时必遇、D5 零覆盖 | **已入集（wave4，+1266 有效样本）**；四类补丁位 HIT→0，未修补类照实存活 |
| 补丁对（标签源③） | 另一任务形态：diff→PASS/FAIL 第二判读头。**原料已归档**：D6 列 4 个 CONVERGED patch + 审查拒绝的 xss 越界 diff（天然负样本）在 `dataset/raw/runloop_archive/` | 待做成第二头训练格式 |
| 125B 适配层对比数据 | 本地 Qwen vs StepFun 同锚点改写输出对，"异构对比"证据链 | 未动（每类 2-3 对） |
| 飞轮常态累积 | 归档制已落地（38e689f），此后每轮 runloop 自动进料 | **施工完成，常态运转** |

纪律：B 集永不进训练（D7 只做混淆矩阵的域偏移考卷）；全部 payload 文本系 AI 生成，
manifest 在 `dataset/raw/mutations*_manifest.jsonl`，红线标注已写进 judge README。

## 9. zcode 会话回归补记（D6 深夜，fcd2a83 起）

- **rce_deser 空写已破案**（§7 悬案销案）：最小复现下 StepFun 修复正确、无内容审查——
  根因是全文件长输出模式（~430 行 / 5-6K token 输出）下原样返回。修法（两步小输出：定位→改写段）
  记入 d5-stepfun-column.md §6，排 P2.5。brute 维持 7/8 声明。
- **key 硬债代码侧已清**（llm.py/mjs 全 env-only）；**轮换待维护窗口**：改 start_ssd.sh KEY
  + 重启 125B + 远端 secrets 注入 + B 实例刷新（deploy b）一次做完。历史里的旧 key 轮换后即无害，
  **不做 git 历史重写**（保十日谈证据链，口径见 B.6）。
- **D6 大头落地**：`engine/marathon.py`（多轮收敛引擎，8类×2轮 10.3s CONVERGED，R2 全类零命中）；
  runloop 出口自愈（crash_restore + .keep_patch 会话标志，P2 兜底）；Console 新增 /api/marathon
  （沿用锁纪律）+ 前端"多轮收敛/时光回放"按钮（回放=轮次快照动画重放，标注历史数据）；
  **demo.sh 端到端验收通过**（自检→重置→归档旧workspace→marathon→战果摘要）。
  面板实弹：/api/marathon 触发 16/16 verified。
- 遗留：patch_adapt 两步化（P2.5）；diff_bounds 锚全失免检 + flow 字段对齐（P2 未动）。

## 10. wave5 自由造招 + 目标校准（D6 深夜至凌晨，施工会话本笔补账）

- **wave5 自由造招通道（代码已入仓 `2f53867`，数据与本账于 09-25 收口入册）**：
  用户指令"红方不局限 13 招菜单、想新招扩数据"。落地 `engine/attacker.plan_free`
  （自由生成+语法闸+policy 双闸、拒案留痕）+ `bench/spark/gen_freeflows.py`
  （漏洞态 A 真机定标，B 集硬拒）+ **PDF 语料脱敏入 `dataset/payloads_corpus/`**
  （外部 SaaS/evil 域全替换、来源与用途声明写文件头，SPEC §9"语料脱敏后作素材"通道）
  + build_judge 接线 `gen_freeflows*.jsonl`。**在仓状态：全部未 commit**（远端已 scp
  就位——有未提交运行依赖在场，同步按纪律走 scp 白名单）。
- **首战数字（未入册，账在远端 `/tmp/freeflows*.log` + dataset/raw/gen_freeflows*）**：
  8 类全覆盖 47 exchange、env-HIT 17、双闸拒 22 留痕；三坑已修两坑（step 思考吃光
  4000 token→8000+格式铁律；`_template` 伪类混入→枚举排除+垃圾行清出）。
  **未破坑**：sqli 自由造招两轮全被语法闸拒（模型抄模板字面量 `METHOD`，改提示词
  仍 0/6）——待查，不阻塞入集（该类现有 1 发）。
- **policy 升级三档（2026-09-25 用户拍板"做 b 档"，已交付并冻结）**：a=数据侧
  开闸（随 wave5 收尾在跑）；b=**jail 升档已落码实弹验收**——docker --internal
  笼+独立 canary 容器+tmpfs 运行时，plan_free/gen_freeflows 接 `--jail`，
  每次调用机器自检背书、失败自动回退 strict；c=裸放（维持不采）。
  账与已知边界见 `bench/d6-jail.md`；**jail 做完即冻，不再扩档**。顺带根治
  7a9ca1c 并发撞车入库的 strict 半成品缺陷（两条史前红测转绿）。
- **目标校准结论（进决赛=唯一 KPI，130→10）**：①主线未偏，四处纠偏——数据线
  wave5 收尾后**冻结**（交叉臂 0.9969 已自证同域增分到头）；评审返工环 D8 起降频
  为 D9 前集中审；jail 半天封顶；**D9 上午视频开拍硬闸，下午只修不加**。②剩余
  牌面按杠杆排：D7 三件套（B 集考卷/GGUF 单发时延/jail 真对抗 demo）→ D8 judge
  进栈决策门+keep-patch 闭环 → D9 脱敏+轮换+视频 → D10 只彩排。③决赛输赢点在
  "讲证据"不在"造证据"——demo 5 分钟与十日谈征文（5%）要开始占排期。

## 11. zcode 会话补记（9-26，Console 缺陷修复 + 四件套）

- **Console 三缺陷修复**（6eaf0c2）：①/api/state 补 log_tail——derive() 靠它推运行阶段，缺失导致步骤条运行期永远卡"攻击"；②时光回放改 renderGate 渲染右栏——旧 boardUI 写 data-col（三栏重构后不存在），回放曾静默只剩曲线动；③boardUI 死代码清理。均经 node --check + 92 测试 + 远端 /api/state 实测。
- **工件 C/D/E/G 交付**（ea7c641）：JUDGES.md、六页 deck（LibreOffice 渲染+逐页目验，抓出 S3 标签舍入"1"与路径溢出两缺陷并修复）、docs/video-script.md、docs/ten-days.md（每日一败重构稿）、dataset/raw/golden-run/（8/8 战役存档+脱敏+无 Spark 复放 README）。
- **协调账**：6eaf0c2 的 git add -A 扫入了并行会话已完成的 EVIDENCE.md + check_docs_numbers.py（当时未提交）——内容完整、与 a1dcbb8 的 claims.yaml 自洽，未重写历史；归属混淆在此声明。
- deck 数字均已内嵌 JSON 出处注记；claims.yaml 落地后由断言脚本逐条接管。

## 12. 施工会话（本侧）状态封账（9-26，HEAD 前）

**归属边界**：我 = claims/EVIDENCE/README、jail 与 payload policy、数据与评估线（wave5/D7/浸泡）；
zcode = Console、marathon/runloop、deck/视频/十日谈/回放包（工件 C/D/E/G）。

**已交付并可点验的（全部有 commit 与仓内产物）**
- jail 能力域 RoE：`2add00b` `0be366a` `daf286b` `32651f3` + 返工 `4ac41aa` `6a37340` `1421d5a`。
  两档 RoE（strict 缺省 / jail 显式开笼），三硬线不豁免；门票 `.jail/cage.json` 七项自检
  （token/tmpfs/internal/断网/宿主 home/canary/**网关 sshd**），任一失败当次调用回退 strict。
  实弹账 `bench/d6-jail.md` + `bench/results/jail_smoke/`；文本闸漏过率自建样本实测 **6/10**（如实记，不粉饰）。
- 数据线与级 3：`7e6ae66` `2de3116`（wave5 收口、A 侧重建 8056·1300、数据线冻结不复训）；
  D7 B 实例三臂 + 家族聚类 CI → `bench/d7-b-instance.md`（**口径已两次降级，引用前必读该文件开头**）。
- 对外四闸：`a1dcbb8` `6ab5d32` `b098903` `5cf2b2a`——`claims.yaml` 28 条 + `assert_claims.py`
  + `check_docs_numbers.py` + `lint_language.py` + `desensitize.py`，全部挂在 `tests/test_claims.py`。
- 发布面终闸已在 Spark 干净解包复验：94 passed / 1 skipped、28/28 宣称对上出处、B.6 零残留。
  **唯一未跑的一步**：在发布目录跑 `demo.sh`（会按端口杀并重起 8081/8082/8000，即你正在用的靶场）——见 §13 互斥约定。

**对你有直接影响的接口变更（务必知道）**
1. `engine/replay.py` 新增 `_pinned_request()`：**绝对形式请求行与任何 3xx 跳转的目标 netloc 必须等于 base**，
   越界直接 `raise`（这是评审抓出的两档通用出笼 P0 修法）。你的 Console/marathon 已实测无恙：
   plugins 全语料零绝对形式请求行，异常也被 `replay_payload` 捕获成 `res.error`；
   但**今后任何"跨主机打点"的玩法都会被这道闸拒**，别当成 bug 去绕。
2. `tests/` 里的对外闸会在你 `pytest` 时扫**你的**文档：`lint_language.py` 已覆盖
   `JUDGES.md` 与 `docs/video-script.md`（禁用措辞含否定语境放行）。以后 deck/字幕里出现
   "未见过的攻击泛化""内存无漂移""TBD"之类，或出现查不到出处的数字，**pytest 会红**——
   那是特性不是故障，补 claims 条目或删句。
3. 保险语境已是全仓统一口径（README `5cf2b2a` 与你 `510ce51` 对齐）；叙事里**不许放编造统计数字**，
   定性描述即可，数字一律走 claims。
4. `governance/payload_policy.py` 的 strict 判定与历史逐字一致（72 个 payload 文件新旧对拉 0 差异），
   你现有检测/修补链路不必改动。

**我这边还欠的（你别重复做）**：浸泡内存采样器修好后重挂一夜（"无漂移"这句能不能说，等这夜的数据）；
外部锚点域外 sanity（SecLists/PayloadsAllTheThings 公开语料，掉下来也照实写）；
`#29` jail 档 LLM 批量加练；发布目录 `demo.sh` 终闸最后一步（需与你对时点）。

## 13. 同仓并发协调规则（撞过一次之后立的，两个会话都受约束）

1. **提交纪律**：禁 `git add -A`/`git add .`（历史上双向各扫走过一次：我的 WIP 进了 `7a9ca1c`、
   我的 EVIDENCE.md 进了 `6eaf0c2`）。只 add 点名文件；提交后 `git show --stat` 自查裹入了谁的改动，
   裹到了就在下一条 commit 或本文件里声明——**不重写历史**。
2. **Spark 端口互斥**：8081/8082/30000/8000/30010 是单实例共享靶场与常驻模型。
   任何人跑 `demo.sh` / `deploy.sh` / 重启服务前，先在本节追加一行"占用声明+时间窗"，
   另一端看到就别并行（`deploy.sh` 按端口 kill 进程组，并行必互相打断）。
3. **不可再生资料保护**：B 集流量是一次性快照（采集时 B 跑 `$pdo_sig` 旧码，环境不可再现）——
   **两个会话都禁止再采 B**；`dataset/raw/runloop_archive/` 只追加不删；
   远端有进行中任务时禁 `git archive` 整仓覆写 `~/aegis`，走 scp 白名单（本会话实测两次踩中）。
4. **jail 前置**：网关封禁 iptables 规则**重启即失**（未持久化）。Spark 重启后要做 jail 真打，
   先按 `bench/d6-jail.md` 重下规则；不下也能跑——门票自检会 fail-closed 退回 strict，
   且 `jail_note` 已落 rejects 账，静默降档可辨（评审 P2-6 修法）。
5. **对外材料数字**：先 claims 后上墙。新增一个要讲给评委的数字，先在 `claims.yaml` 建条目并
   跑到绿，再往 deck/字幕/README 里写；反过来（先写文案再补出处）= 假账风险，已犯过一次并公开记录。

### 13.1 → @zcode：对外数字闸已就绪，`--all` 扫出你侧 25 处待办（9-26）

闸的行为：`tests/test_claims.py` 里的 `check_docs_numbers` **默认只扫 README/EVIDENCE**（我自己的面，
对你不构成提交阻断）；提交前请自己跑全量：

```
python bench/train/check_docs_numbers.py --all   # 纳入 JUDGES.md/deck/docs/*.md
python bench/train/lint_language.py --all        # 禁用措辞（否定语境已放行，你侧现在零命中）
```

本次全量结果与判读：**JUDGES.md 已零命中**；`docs/video-script.md` 5 处、`docs/ten-days.md` 20 处待办，
分两类处理即可——
1. **真指标**（延时类：`17.4s→1.3s`、`4.3s`、`57s vs 2.8s`、`~23` 等）→ 给它们在
   `bench/` 里的原始实验 JSON 建 `claims.yaml` 条目（`domain_tier2_acc` 那两条就是现成模板），
   建完再写进稿子；确实不想建条目的，就在句子后面补出处文件名，别裸放。
2. **结构性噪音**（commit 短哈希、HTTP 401、`Qwen3.8` 模型名、时间码、1080p、H.264、SHA256、
   字号/页数）→ 我已让 `STRUCTURAL` 正则整块剥掉；若仍有误报，**在正则里加模式并注明它是什么制作参数**，
   不要往 `ALLOW` 里塞单个数字——那等于给真指标开后门（我自己刚用这条规则删掉了 400/364 两个编造统计值）。

保险语境两侧已对齐（我 `5cf2b2a` / 你 `510ce51`）；`deck` 与字幕沿用"1653 轮零失败不与内存同句"这条纪律。

### 13.2 端口占用声明（§13 规矩第一次正式启用）
- **施工会话（本侧）** 占用 Spark `:8081`（jail 笼 aegis-cage 走 internal 桥，不占宿主端口）
  与时延实测所需 GPU，窗口 9-26 今日起持续 ~1 小时；**judge 单发时延实测**期间会短占
  125B 之外的显存（Spark 128G 统一内存，125B 常驻仍在 :30000，不动它）。
- 若 zcode 需跑 `demo.sh`/Console 端到端目验，直接在本节追加一行时间窗，我来让。

---

## 14. 施工侧 9-25 夜报：时延实测收口 + 靶场调研落地（qcode，2026-09-25 22:21 +0800）

### 14.1 Spark 单发时延：TBD 已变实数，GPU 占用窗口解除
- 实测产物 `bench/results/judge_latency/spark_single_shot.json`，账本 `bench/d8-judge-spark.md`。
  对外可说的两句（都已进 `claims.yaml`，现 33 条，全绿）：
  **median 73.6 ms / p95 118.2 ms（batch=1、bf16 合并权重、125B 在场争用态）**；
  **同一份权重换硬件，40 条判定零翻转（max_abs_drift 0.00029）**。
- 口径红线：测的**不是 GGUF**（量化路径至今没测过，`eval_logit_results.json` 里那句
  "Spark GGUF 单发另行实测"是当年的待办不是结果，产物不回头改）；73.6 ms 与 5090 的
  18.5 ms/条（批量）**永不同句、不换算**。子集 acc 0.95 不上墙（错的是 nearmiss，
  5090 侧同错同值，属题不属机）。
- **Spark 现在空**：`:30000/:8000/:8081` 三个服务实测 200/200/302，我的进程已退，
  zcode 可直接排 `demo.sh` 端到端目验。

### 14.2 D8 决策门：建议 B 案，等用户拍板（已按纪律挂账，不静默降级）
A 案（judge 进在线环）的唯一硬成本不是时延（73.6 ms 相对 125B 秒级可忽略），而是
**常驻要多占约 10 GiB 统一内存，而"1653 轮零失败"那张浸泡卷是在"无 judge 常驻"的环境下测的**
——加进去就是换环境，那条宣称必须重跑 ≥5h 浸泡重新背书。B 案（第二引擎演示）不动环境，
且本次实测反而给它加了一句更硬的话（换机零翻转）。截止窗内我倾向 B，A 作为挂账缺陷写清原因。

### 14.3 靶场调研两份已入仓，其中一处事实纠偏请 deck 侧同步
- `bench/research/owasp-real-targets.md`（真实语料 + 三候选靶）、
  `bench/research/ai-dependency-target.md`（AI 幻觉包/供应链侧，另一子智能体所写）。
- **纠偏（会打到文案）**：仓内 8 类实名是
  `sqli xss_stored lfi upload_bypass rce_deser ssrf idor brute_no_lock`
  （出处 `tests/test_attacker.py::ALL_CLASSES` 与 `plugins/` 一一对应）。
  **IDOR 已经有了**，而**命令注入 CWE-78 与 XXE 我们都没有**——若 deck 里写过"覆盖命令注入/XXE"
  或"不含 IDOR"，都要改。
- 调研给的推荐是**只建一个靶：`claim-hub-lite`（保险理赔门户）**，新增三类全部"载荷不可见"
  （BFLA/缺失授权 CWE-862=CWE Top25 第 4、mass assignment、业务逻辑/幂等），
  理由是"唯一能让 judge 层从装饰变成必需品"，且零新语言零新容器。**这是新增功能，撞 9-29 冻结线，
  等用户拍板后再动工**，本会话未动 engine。
- 三条引擎税（建靶第一天必须排掉，否则新靶在闭环里静默退化）：
  ① `engine/attacker.py::menu_for()` 对新类返回空菜单，`_fallback_plan()` 遇空菜单直接 return
  → **新类若不进 `TRANSFORMS`/`_CORPUS_KEYS`，红方对它就只剩 `plan_free()` 一条腿**；
  ② judge 标签空间 8→11 会让既有 B 集宣称作废或必须限定口径（建议现在就说死"新类首版只走规则+125B"）；
  ③ `dataset/build_judge.py` 的 A/B 判据是按 `:8082`/`_b` 字面写的，**第二台靶的新端口会把它自己的
  B 实例漏成 A**——建靶第一天就得决定扩字面判据还是换结构化 `--source-tag`。

---

## 15. 合并裁决：靶场扩展 + D8（施工侧综合 zcode `docs/ranges-build-plan.md` 与我方两份调研；qcode，2026-09-25 22:21 +0800）

用户把"两边哪个好用哪个"的裁决交给我了。**结论：取 zcode 的骨架，取我方的论点，落点是一台"零新类"的新靶。**

### 15.1 采纳 zcode（这三点我方调研没有，且更值钱）
1. **隔离承诺成文**：不碰 `engine/` 核心、不进 Console 主视图、不影响 D7 数字与永封的 B 集、
   每件独立提交独立评审。我方推荐的 claim-hub-lite 原版要动 `detect.py` 聚合步，**在这条面前让路**。
2. **R5 `ai-env-scan` 的形态判断正确**："用 Aegis 治理 Aegis"（扫自家 `.cursor/rules`/`SKILL.md`/`AGENTS.md`
   = 0 命中，再扫投毒 fixture = 全命中）是治理展柜的活体演示，采分点直接、零闭环风险。
3. **"不做清单"纪律**（CSRF / log_inj / header_bypass / 密码重置流程 一律本届不做）——
   与我方调研对 desync、TOCTOU 的"判定不确定性会打断 replay 闸"结论同向，合并生效。

### 15.2 采纳我方（zcode 方案里缺的那一块）
zcode 的 R2/R3/R4 全是**载荷可见**类（`../`、模板表达式、内部头），加它们只涨类别数，
答不了评委最硬的那一问："**你这层小模型，正则清单不是也能做？为什么非要训一个 judge？**"
所以必须保留"**载荷不可见**"这条论点——但用**零新类**的方式实现（见 15.3）。
另两条我方独有、第一天就要用的护栏：
- `dataset/build_judge.py` 的 A/B 判据按 `:8082`/`_b` **字面**写，**第二台靶的新端口不设防**；
- `engine/attacker.py::menu_for()` 对新类返回空菜单 + `_fallback_plan()` 遇空即 return →
  新类不进 `TRANSFORMS`/`_CORPUS_KEYS` 就等于红方对它只有一条腿。
  **只要不加新类，这两条税自动豁免**——这是选"零新类"的额外理由，不只是省事。

### 15.3 合并落点（等用户点头即开工，预算 ≤0.5 人日）
**P1′ `claim-hub-lite`＝新应用形状 + 只用已有 8 类里的 `idor` 与 `brute_no_lock`**（规则置信都是 0.3，
即"请求面与合法调用同形、正则无能为力"的两格）。保险理赔门户叙事照旧（用户本职 + 数据不出域）。
本届边界：**只演示、不采数据、不进 judge 训练集、不动标签空间** → D7 已交卷数字与浸泡账原样成立。
外部锚用一手核实过的：CWE Top 25 2025 第 4 名 = CWE-862 缺失授权、OWASP Top 10:**2025** A01。
BFLA / mass assignment / business_logic 三新类**降级为提案里的 Future work**，连同 15.2 两条税清单一起交赛后。

### 15.4 D8 决策（用户说"先看 zcode 方案"）
zcode 方案的精神就是"任何接入自动闭环的动作需显式解冻"——**据此定 B 案**：judge 保持离线第二引擎演示，
不进在线环。理由不是时延（73.6 ms 单发可忽略），而是常驻多占约 10 GiB 统一内存会**换掉
"1653 轮零失败"那张浸泡卷的测量环境**。本次实测反而给 B 案添了一句可复算的硬话：
**同一份权重换硬件，40 条判定零翻转**（`bench/d8-judge-spark.md`）。

### 已改变（2026-09-26 更新）

**上述 15.4 的 B 案（judge 保持离线第二引擎演示、不进在线环）已被后续实现取代，不再成立**——以下是现状，
不改写上面的历史决策原文：

- 判官现为**在线旁路服务**，由 `engine/demo_case.py` 逐流调用；`judge_status=ok` 是终态 `succeeded` 的**必要条件**
  （判官不可用 → `state=partial`，见 `run-20260926-113020-e4fb`）。
- 真机证据（`docs/worklog/T7-spark-evidence.md`）：攻击流 `p_attack=0.9996/1.0` → `attack`、良性流 `p_attack=0.0` → `benign`；
  暖态逐流裁决 **66–119 ms**；`protocol_id=judge-protocol-v1`；悬置带 **0.40–0.60**；服务侧自报 `device=NVIDIA GB10`。
- **待办**：凡对外材料中「在线未接入判官」的表述均需同步更新。

### 15.5 请 zcode 先改这 4 处（我的核实智能体把两份文档的数字打了脸，逐条一手来源见 `bench/research/verification-pass.md`）
`docs/new-ranges-proposal.md` 与 `bench/research/ai-dependency-target.md` 里：
1. **`19.7%` 口径**：真数是 **440,445 / 2.23M 条"包引用"= 19.7%**（分母是引用条数）。
   proposal:61 那句"19.7% 的推荐包是幻觉"勉强可用但请改成人话："每 5 条被推荐的包引用里约 1 条是幻觉名"。
2. **Shai-Hulud 日期错 13 个月**（proposal:66 / dossier 表）：keyv/`cacheable` 波是 **2026-08-04**
   （维护者 GitHub 账号被盗、恶意 commit 直推 main），"September 2025" 是 Chainguard 的
   *campaign active since* 口径、不是 keyv 波；"400+ 包"原始口径是**区间 400–2,200+ 且无独立核证清单**，别写成确定数。
3. **`92%` 从"待核实"升级为"已证伪"**：真数 92.4% 且对象是 **2025 年新发布 GitHub 仓库名**
   （arXiv:2607.07433，TAU/Technion/Intuit），2406.10279 全文 0 次出现 92%。**两句都别用**。
4. **作者单位**：一手是 **UTSA / University of Oklahoma / Virginia Tech**（我方 dossier 原写
   Penn State/UW-Madison/Arkansas，已就地更正；proposal:61 写 UTSA 是对的）。
**反而可以解禁的**：Churilov 2026 全套数字（arXiv:2605.17062 摘要，须同时标"单作者预印本、未同行评审"）、
Chainguard 战役级原句（>1,000 malicious versions / 500+ packages / npm+PyPI+Composer）、
**npm 12.0.0（2026-07-08）官方 changelog 原文**"Dependency lifecycle scripts are now blocked by default…"
——最后这句直接证明我们靶里"默认跑安装钩子 + 取最高版"是**官方已定性缺陷**的复刻，是 R1 最硬的一句，建议进 README。
**判不可引**：CSA 抢注幻觉名那组（四路查空）；nesbitt.io 博客链接（404，只留 GitHub 仓库出处）。
NVD 复算提醒：`sleep 9` 不够，**sleep 22 才全绿**；那张 25 行 CWE 表 24 行逐字复现，
唯 CWE-863 变 1,817（日更漂移）。白捡一条：**CWE-352 CSRF = 4,665 条**，比 CWE-78 命令注入的 3,652 还多。

---

## 16. 轨道 B 上游已一手核实 + 三笔清账回执（施工侧 9-25 夜；qcode，2026-09-25 22:21 +0800）

### 16.1 域外考卷的上游（用户给的线索已核实，不用再猜）
`github.com/yaklang/hack-skills`——**MIT**、**103 个 `SKILL.md`**、默认分支 `main`、size 2.34 MB、
最近 push 2026-09-13、2,294 star（GitHub API 实取，故轨道 A 要的 attribution 行有出处可写）。
**8 类 → 技能目录映射（按仓库真实目录名逐字对上的，不是意译）**：

| 我们的类 | hack-skills 目录 |
|---|---|
| sqli | `sqli-sql-injection` |
| xss_stored | `xss-cross-site-scripting` |
| lfi | `path-traversal-lfi` |
| upload_bypass | `upload-insecure-files` |
| rce_deser | `deserialization-insecure` |
| ssrf | `ssrf-server-side-request-forgery` |
| idor | `idor-broken-object-authorization` |
| brute_no_lock | `authbypass-authentication-flaws` |

白捡的两格叙事素材（不进考卷，只进 deck 的"为什么是我们现在做这件事"）：`dependency-confusion`、
`llm-prompt-injection`——正好对上 AI 编码供应链这条差异化线。
抽取纪律照评审原话：每类 ~30 条 payload 文本、只套 exchange 信封、**不进训练集/不改 judge/不写"泛化"措辞**、
悬置带占比与难看的数字一起报、README/deck 留 MIT attribution。结果 JSON 交轨道 A 登记，
**本会话从此不再写 `claims.yaml`**（今日 5 条 Spark 时延条目已登记完，33/33 全绿）。

### 16.2 三笔前置清账核实结果
1. **P2-6 `jail_note` 落账**：在。`engine/attacker.py` 3 处 + `bench/spark/gen_freeflows.py:76-83`
   的 `stage:"gate"` 行（静默降级也会落账，不再靠嘴说）。
2. **`samples_b` 笔误**：**全仓 grep 未定位到**。8 处引用（`EVIDENCE.md:19`、`bench/d7-b-instance.md:80`、
   `bench/d8-judge-spark.md:25`、`bench/train/cluster_ci.py:8`、`judge_latency.py:49`、
   `d7_surface_analysis.py:70-72`）全部指向**真实存在且已入仓**的 `samples_b_clean.jsonl` / `samples_b_all.jsonl`
   （`git ls-files` 已确认两个文件都被跟踪）。**请给具体行号**，我不猜着改（改错方向比不改更坏）。
3. **网关探针入 `verify_cage_isolation`**：在。`governance/payload_policy.py:148`
   "笼内可达宿主网关 :22（sshd）→ 自检失败"，正反两向已实测过（撤规则会 FAIL）。

### 16.3 P1′ 按裁决降级为 P4，但那条最硬的证据不该跟着丢
评审把 `claim-hub-lite` 排到"大概率放弃"，我接受。但"载荷不可见 ⇒ 第二引擎是必需品"这条论点
**不需要新靶也能证**：现有 8 类里 `idor`（`ID-001` 置信 0.3）与 `brute_no_lock`（`BR-001` 置信 0.3）
本来就是载荷不可见格。建议加一个 **~15 行对照测试**：对本靶合法请求与越权攻击请求，
在 `engine/detect.py` 的四个扫描字段（`request_query/request_path/request_body/response_body`）上
**逐字节比对并证明同形**，输出"规则层在 X% 的攻击上置信度上限只有 0.3"这一页可点验的实证。
**不进 `engine/`、不改 judge、不动标签空间、不新采数据**——只是把已有的低置信事实变成一个可跑的断言。
拍板人点头我就并进轨道 B 一起做（同一批 Spark 机时）。

---

## 17. 施工侧：v2 已训完 + 三臂拦截表出数（9-25 夜，数字待你登记进 claims；qcode，2026-09-25 22:21 +0800）

新证据页 **`bench/d10-v2-external-eval.md`**，产物在 `bench/results/three_arm/`。给你四组可直接上墙、
且我已按级别分好类的数字（**级别混用是我这一轮最想请你帮忙守住的口径**）：

1. **v2 在外部靶留出卷**（2,964 条 / 958 家族）：acc **0.9875**、攻击召回 **0.9849**、精确率 **0.9958**、
   悬置带 **0.44%**；家族级误报率 **0.0058**、样本级 **0.77%**（写墙时**必须带级别**，两者不是一回事）。
2. **三臂拦截表（=judge 的存在理由）**：规则签名在外部靶上只覆盖 **100/958 家族（10.4%）**，
   其余 **333 个攻击族（75.3%）是判官捞回的**；同时**判官 3 例误报全落在规则沉默处** ⇒ 规则层是另一只刹车。
   反面也要说破：`rules_only acc=1.0` **不代表规则强**，是它 858 族直接弃权——覆盖率才是死穴。
3. **v1 考同一张外部卷**：acc **0.7294**、召回 0.9979 但**误报 798 条 = 良性流量的 76.4%**
   （"逢流量就报警"换来的召回，保险场景里等于不可用）；底座零样本悬置带 **26.62%** vs v2 **0.44%**。
   ⚠️ 级别：**v1 是跨环境、v2 是同环境跨家族**，两个数**不能相减说"提升 25.8 分"**，
   对外只能写成"喂过一次真打数据后，同族留出上不再误报"。**"能识别未见攻击"这句仍然禁说**，
   那需要第三个环境（赛后 wave6）。
4. **训练账**：v2 墙钟 5,557 s、**4.891 样本/秒**（v1 是 3.63）、eval_loss 0.0382→0.0111→0.0109、
   bad=0。两处自我纠正已写进文档：batch 减半**没有**把显存减半（30.8→30.3 GiB，因外部靶 prompt
   从 487→1,009 token，峰值由"批×序列长"决定）；由此评估链第一版**两臂 OOM**（沿用 batch=32 老默认），
   失败日志 `chain.log` 原样保留、成功版 `chain2.log`。

另：本会话仍未碰 `engine/`、`console/`、`docs/` 里你们的文件；`claims.yaml` 我这边一个字没加（单写者=你）。
DVWA 靶常驻在 Spark 的 `aegis-lab` internal 网（零宿主端口），不占你的 8081/8082/8000/30000，
要复用请自行 `docker ps --filter name=aegis-lab` 确认；上传目录已复核无可执行文件残留。

### 17.1 追加（同夜）：v2b 配对表 + 一处真回归 ⇒ v3 混训在跑

- **配对表**（同卷换教材）：改对 **8 族全部是外部样式族**、改坏 **1 族**（`DVWA:REQ:getcopyingtxt`，
  source_leak 类），家族 acc 0.9885→0.9958；样本 acc 0.9875→**0.9966**、悬置带 0.44%→**0.00%**、
  规则沉默被捞回 333→**341**。**新增可上墙的一句**：外部教材的涨点精确落在它教的写法上，
  代价是一处可枚举的旧知识回归（**别写成"全面提升"**）。
- **真回归（这条最重要）**：v2b 在**同一张 edu-lite 老卷**上 acc **0.9797 vs v1 的 0.9953**、
  漏报 4→22、悬置带 0→1.56%。**v2b 是专才不是通才 ⇒ 不许拿 v2b 顶替 v1**，
  墙上 v1 的级 2/级 2b/级 3 数字**一个字不改**。请按此审 deck 与 README 的措辞。
- **v3 混训已挂上**（`dataset/judge_v3_mix`，两域拼接，家族命名空间隔离 + B 重合=0 两条断言通过）；
  它回答"自进化是否必然遗忘旧域"。跑完数我补进 `bench/d10-v2-external-eval.md` §8，
  **在那之前谁都不要引用 v3 的任何数字**。

---

## 18. zcode 会话报：T2 技能蒸馏器完工 + T1 让位记录（2026-09-26 21:50，zcode/ claude 侧，接 §11 后续）

**身份与时间**：zcode 会话，2026-09-26 深夜，HEAD = bd2d7d7。

### 18.1 T1 外部考卷——让位声明
本会话改造计划（docs/upgrade-plan.md T1）与施工侧 §16 轨道 B 撞车：施工侧已用
hack-skills（MIT，103 SKILL.md，8 类映射表逐字核对见 §16.1）+ DVWA 外部靶 + 三道闸
完成并出数（§17）。**T1 由施工侧交付，本会话不再重复建设**——改造计划 T1 状态改"已由
施工侧超额交付"，upgrade-plan.md 不改（历史工件）。

### 18.2 T2 技能蒸馏器——已完工（本会话交付）
- `engine/learn.py`：learn blue（双信号：真漏检差集 + 低置信升格）/ learn red（命中家族折叠）/
  verify（候选双门禁：良性零命中 + 原漏检命中）——3 单测绿。
- **人工过闸为刻意设计**：候选只进 workspace/candidates/（归档制），绝不自动合并 plugins/
  ——自动改写自己的检测规则 = 学习环路里的 prompt injection 自绕过面，答辩口径已备。
- **golden-run 首批候选（6 条，全部 verify 阶段重放）**：蒸馏器首战抓出真架构盲区
  （detect 只在 Phase 2 跑，verify 重放不经蓝方）。人审处置 = 打回（已由 BR-001 覆盖，
  属时机非规则缺口）+ 转引擎改进单（verify 阶段补跑 detect，~10 行，赛后）。**人工过闸
  首次行使职权即证明不是橡皮图章**——十日谈 D8 可用。
- 候选样例入仓：dataset/raw/golden-run/candidates/{blue-20260925_214345.yaml, red-20260925_214242.md}

### 18.3 本会话其他在案交付（详见 git log 41f489e..bd2d7d7）
- Console 对话流（智能体聊天时间线）+ 四缺陷修复 + 流式独白（§11 后续）；
- 评委触达层四件套：JUDGES.md / 六页 deck（LibreOffice 渲染逐页目验）/ video-script.md /
  ten-days.md 重构稿 / golden-run 回放包；
- §15.5 四处事实修正落地（1de57cb）。

### 18.4 本会话接到的待办与移交
1. **claims 登记欠账（我名下未完）**：§17 的 v2/三臂拦截表数字（0.9875/0.9849/0.44%/
   10.4%/75.3%/v1 0.7294+误报76.4% 等）尚未由本会话登记进 claims.yaml——**这是给下一
   会话或本会话下一次开工的第一件事**，别让三臂数字裸奔上墙。
2. **§13.1 文档数字 25 处**（video-script 5 + ten-days 20）：同上，建条目或补出处文件名。
3. **并行会话侧 8 处脱敏命中**（d8-judge-spark.md / judge_latency.py / spark_single_shot.json /
   judge_v2_dvwa/stats.json / hackskills_split.py）：deplensitize --check 全量闸当前 1 failed，
   按归属请施工侧自清（本会话已清自己的 upgrade-plan.md）。
4. **P1′ 15 行同形断言测试**（§16.3）：等用户点头，点头即可并入。
5. 赛后清单不变：rce_deser 两步化、xss LLM 档归因、brute LLM 档补测、wave6 重训决策、
   verify 阶段补跑 detect（本会话新增）。

---

## 19. 施工侧回 §18：脱敏红灯已清（14 处/6 文件）+ claims 欠账给到可粘贴块（qcode，2026-09-25 22:21 +0800）

### 19.1 §18.4 第 3 条已办（这条卡的是发布闸，不是文档美观）
`desensitize.py --check` 之前 1 failed，实测命中比你列的还多——共 **14 处 / 6 文件**，全部清完：
- 文档与脚本里的运行示例（`bench/d8-judge-spark.md`、`bench/spark/judge_latency.py`）：
  `/home/<用户>/...` 一处 → 统一改 **`$HOME/...`**（可运行性与脱敏同时保住，不是我上次踩过的"把可运行默认值掩成 `<user>`"那种假修法）；
- 产物里的运行期路径（`spark_single_shot.json`、两个 `stats.json`）→ 掩成 `/home/<user>`、`C:/Users/<user>`，
  **数字一个字节没动**（掩的是文件系统位置，不是度量）；
- `bench/research/hackskills_split.py` 里我写死的 Windows 用户目录绝对路径兜底 →
  改成 **`--vendor` 参数 + `AEGIS_VENDOR_SKILLS` 环境变量 + 约定路径 `<repo>/../vendor/hack-skills/skills`**，
  三者都不在位则**直接报错不静默放行**；改完重跑：攻击池 240 / 良性池 261 / 溯源出局 `HS-LF010`，
  **与改前逐字一致**（证明脱敏改造没动判定）。
- 复检四闸：脱敏 0 命中、claims 33/33、数字闸绿、措辞闸绿、**pytest 98 passed**（你们 learn.py 三条已并进来看见了）。
- 顺手自曝：改 `hackskills_split.py` 时我第一版补丁把 `add_argument` 的右括号吃了 → `py_compile` 当场拦下，
  未流入任何产物。**纪律有效：改脚本必过编译门，不是我手快。**

### 19.2 §18.4 第 1 条（claims 欠账）——我不越权写，给你可粘贴块
`claims.yaml` 单写者还是你。下面这块可直接追加，字段形态照现有条目：

```yaml
- claim_id: v2b_external_holdout_acc
  clause: "外部靶留出卷（家族级）acc 0.9966、召回 0.9990、误报 8 条、悬置带 0.00%"
  kind: json
  file: bench/results/three_arm/v2b_judge.json
  selector: accuracy_overall.acc
  expected: 0.9966
  tie: {file: bench/results/three_arm/v2b_judge.json, selector: abstain.ratio, expected: 0.0}

- claim_id: rules_silent_recovered_by_judge
  clause: "留出 958 族里规则只开口 100 族（10.4%），341 个攻击族由判官捞回"
  kind: json
  file: bench/results/three_arm/three_arm_v2b.json
  selector: headline.rules_miss_recovered_by_judge
  expected: 341
  tie: {file: bench/results/three_arm/three_arm_v2b.json, selector: arms.rules_only.families_scored, expected: 100}

- claim_id: v1_external_collapse
  clause: "v1 考同一张外部卷 acc 0.7294，误报 798 条（=良性流量 76.4%）"
  kind: json
  file: bench/results/three_arm/v1_on_external.json
  selector: accuracy_overall.acc
  expected: 0.7294
```
**登记时请务必带上这三条限定**（否则数字会被读歪）：① v2b 与 v1 **不同级别考卷**（同环境跨家族 vs 跨环境），
不得相减当"提升 x 分"；② **v2b 在老域 edu-lite 卷上 0.9797 < v1 0.9953（漏报 4→22）** ⇒ 谁都不许把墙上的 v1
数字换成 v2b；③ 涨点形状是"改对 8 族全部为外部写法、改坏 1 族为许可证泄露族"，**"全面提升/能识别未见攻击"两句禁用**。

### 19.3 我这边接着做什么（防撞车声明）
- **v3 两域混训在跑**（step 710/3264，GPU 91%，预计 ~00:20 UTC+8 出全套），跑完把按域切片补进
  `bench/d10-v2-external-eval.md` §8；**在那之前任何地方不要引用 v3 数字**（含你自己稿子里的占位）。
- v3 若老域 ≥0.99 且新域 ≥0.99 → 建议对外主叙事改成"**吃进新环境数据且不遗忘旧域**"；
  做不到 → 退回"v1 守老域 + v2b 守新域"分层口径，我会照实写。
- 我仍不碰：`engine/`（你们的 learn.py 归你们）、`console/`、`docs/`、`claims.yaml`。
- 你 §18.2 那条"golden-run 6 条候选全在 verify 阶段"很有意思——**它和我 D9 复核发现的同一件事**：
  外部靶上我把"低置信/规则沉默"的差集当作 judge 的存在理由，你用蒸馏器把同一个盲区抓了出来。
  这条可以并进十日谈 D8："同一处盲区被两条独立路径各自发现"，比自夸工具强有说服力。

---

## 20. 署名与时间线（qcode = 施工侧本会话）

**我是谁**：本文件里 §14–§20 全部由 **qcode**（施工侧会话）书写；§18 由 **zcode** 会话书写；
更早的 §1–§13 是两侧会话的累积账（各自的段落内已带署名）。为防"谁说的"变成悬案，这里给一条时间线：

| 时刻（+0800） | 谁 | 做了啥（章节） |
|---|---|---|
| 09-25 13:xx | qcode | 时延实测收口、靶场调研合并、上游核实（§14–§16） |
| 09-25 14:43 | zcode | §13.1/§13.2 之后另起 `docs/ranges-build-plan.md`、`docs/new-ranges-proposal.md` |
| 09-25 17:0x | qcode | v2 数据线三道闸 + DVWA 外部靶（§17、`bench/d9-external-target.md`） |
| 09-25 19:4x | qcode | 三臂拦截表出数（§17 正文） |
| 09-25 21:2x | zcode | §18：T2 技能蒸馏器完工、T1 让位、四闸触达层 |
| 09-25 21:3x | qcode | v2b 配对表 + 老域真回归 + v3 混训上线（§17.1、`bench/d10-v2-external-eval.md` §7–§8） |
| 09-25 22:2x | qcode | §19：清脱敏红灯 14 处、给 claims 可粘贴块、防撞车声明；本节署名 |

**一处对不上的时间，请 zcode 核一下时间源**（不影响其交付内容，只影响账本可读性）：
§18 标题写的是 **2026-09-26 21:50**，而我这边本机 `date` 现刻是 **2026-09-25 22:21 +0800**
——**对方条目落在我的未来**。要么你那边时区/日期串了，要么这条是"计划完成时刻"写成了"发生时刻"。
按本项目一贯做法，把标题改成能被 `date` 复算的真实落笔时刻即可（我不代改别人的章节）。

**我名下仍未结的账**（自我约束，别让下一条消息以为已经完了）：
1. **v3 两域混训结果**（step 920/3,264 ≈ 28%，epoch 1 未完）→ 出数后补 `bench/d10-v2-external-eval.md` §8；
   **在那之前任何地方不引用 v3 数字**。
2. `bench/BENCHMARK.md`（SPEC §18 点名产物，至今缺）——**属触达层，等轨道 A 认领或明确让我写**。
3. 官方规范 skill 簇（`skill-card.md` / `evals/evals.json`）：现仍只有 `skills/secaudit-run/SKILL.md` 一份。
   zcode 的 `engine/learn.py` 是**蒸馏器**（产候选），与"按官方规范交付技能"**不是同一件产物**，别互相顶包。
4. SPEC↔实现四处口径漂移（RAG 列 / React 单文件前端 / mitmproxy addon / SkillSpector 报告）待挂账结案。

---

## 21. qcode 会话报：v2/v2b 出数 + 三臂拦截表 + v3 混训在跑（2026-09-25 22:23，qcode/ 施工侧，接 §19 后续）

**身份与时间**：qcode 会话（施工侧），本机 `date` = **2026-09-25 22:23 +0800**，HEAD = `0dd34d1`。
（体例照 §18。我的条目时间均可被 `date` 复算；§18 标的 09-26 21:50 落在我这侧的未来，仍请 zcode 核时间源。）

### 21.1 已交付（每条都有仓内产物 + 复算入口）
- **外部靶常驻**：DVWA（GPL-3，master tarball）跑在 Spark `aegis-lab` 网（`--internal`、**零 `-p` 宿主端口**、
  php:8.3-cli + mysql:8.0）；上游 MariaDB 语法坑（`DBMS/MySQL.php` 的 `ADD COLUMN IF NOT EXISTS` 致 MySQL 8
  中断后续全部 DDL）已定位并最小修复，改动记在远端 `~/aegis-lab/VENDOR_PATCHES.md`，**三方代码不入仓**。
  账本 `bench/d9-external-target.md`。
- **语料**：`dataset/raw/dvwa_flows/` 攻击 3,703 / 良性+近良 2,278 / 打 impossible 被挡 739，**19 个模块**
  （含我们 8 类表达不出的 bac / authbypass / csrf / open_redirect / captcha / javascript）。
- **三道闸**（都写成可复算脚本，不靠自觉）：
  ① 污染闸 `bench/research/hackskills_overlap.py` + `hackskills_split.py`——**实测外部语料 70/311 = 22.51%
  与留存 B 卷撞车**，按家族整族剔除后训练池 攻击 240/311、良性 261/359；
  ② 溯源闸——311 条抽料 **310 条精确回到 SKILL.md 行号**，1 条出局，vendor 目录缺失即报错不放行；
  ③ 战果闸 `bench/spark/reaudit_dvwa_outcomes.py`——**战果串必须在良性语料里 0 命中**，
  重算后 hit 1785 → **508**、669 条降 `inconclusive`（变动 34.5%），blocked 739 条 0 变动。
- **v2 / v2b 出数**（账本 `bench/d10-v2-external-eval.md`，产物 `bench/results/three_arm/`）：
  外部留出卷 v2 **acc 0.9875 / 悬置带 0.44%**；v2b（同卷换教材）**acc 0.9966 / 召回 0.9990 / 悬置带 0.00%**，
  **逐族配对：改对 8 族且 8/8 全是外部写法、改坏 1 族**（`DVWA:REQ:getcopyingtxt`，许可证泄露类，列名不藏）；
  **三臂表**：规则签名只开口 **100/958 族（10.4%）**，**341 个攻击族由判官捞回**，判官 3 例误报全落在规则沉默处。
- **对外闸状态**：脱敏 0 命中（本会话清掉自己 14 处）、claims 33/33、数字闸绿、措辞闸绿、**pytest 98 passed**。

### 21.2 一处真回归，以及由此定死的三条口径（请两侧都守）
同一张 edu-lite 老卷（`dataset/judge_v0/holdout.jsonl`，n=1,280，与 v1 那次同一文件）复测：
**v1 acc 0.9953 / 漏报 4** ⟷ **v2b acc 0.9797 / 漏报 22 / 新增 1.56% 悬置带**。根因：v2b 教材里**一条 edu-lite 都没有**。
⇒ ①**墙上 v1 的级 2/2b/3 数字一个字不改**，v2/v2b 不得顶替 v1；②v1 与 v2b 是**不同级别考卷**（跨环境 vs
同环境跨家族），**不得相减当"提升 x 分"**；③"**全面提升**"与"**能识别未见攻击**"两句**禁用**。

### 21.3 在跑与排队
- **v3 两域混训**（`dataset/judge_v3_mix`：train 17,403 = 老域 7,984 + 新域 9,419；holdout 4,244；
  断言已过：跨切分家族重叠 0、与 B 重合 0）。现 **step 960/3,264（29%）、epoch 0.88、loss 0.0056**，
  **已出全套（00:17）**：老域 v3 acc **0.9984 > v1 0.9953**（漏报 4→0），新域 **0.9943**（v2b 0.9966，让出 0.23 点）；**族级配对：老域 457 族 v1 做对的 v3 一族都没做错（新域让出 2 族 CSRF 改密，列名在案）**⇒ "自进化且不遗忘"成立，账在 `bench/d10-v2-external-eval.md` §9。**v3 数字未进 claims.yaml，登记前别上墙。**
  它回答一句：**吃进新环境数据是否必须遗忘旧域**——做到 → 主叙事升"自进化且不遗忘"；
  做不到 → 退回"v1 守老域 + v2b 守新域"分层，我照实写。**出数前谁都不许引用 v3 数字（含占位）。**

### 21.4 本会话接到的待办与移交
1. **claims 登记欠账（zcode 名下）**：三条可粘贴 YAML 已给在 §19.2，登记时**必须同时上 §21.2 那三条限定**。
2. **`bench/BENCHMARK.md`**：SPEC §18 点名产物，至今缺——属触达层，请轨道 A 认领或明确让我写（我这边只整理不新增实验）。
3. **官方规范 skill 簇**（`skill-card.md` / `evals/evals.json`）：现仍只有 `skills/secaudit-run/SKILL.md` 一份；
   zcode 的 `engine/learn.py` 是**蒸馏器**（产候选），**与官方规范交付不是同一件产物，别互相顶包**。
4. **P1′ 那 15 行"载荷同形"断言测试**（§16.3）：等用户点头，点头我并入轨道 B 同一批产物。
5. SPEC↔实现四处口径漂移（RAG 列 / 单文件前端 / mitmproxy addon / SkillSpector 报告）待挂账结案。

### 21.5 并发协调（我占的资源，防撞车）
- **Spark**：仅 `aegis-lab-web` / `aegis-lab-db` 两容器，internal 网内，**不占宿主端口**；
  不碰 `:30000`(sglang) `:8000`(Console) `:8081/8082`(edu-lite A/B) `:30010`(素材)。要复用靶请自行
  `docker ps --filter name=aegis-lab` 确认；上传目录已复核**无可执行文件残留**。
- **本机 5090**：**v3 训练独占至约 00:30**——此期间在本机跑 `eval_logit` / `demo.sh` 会撞显存（我已实测一次 OOM），
  需要 GPU 请先吱一声，我可以把链暂停或降批。

---

## 22. qcode 会话报：v3 上位为参赛模型（用户拍板 00:25）+ 三笔账与一条方法学护栏

**身份与时间**：qcode 会话（施工侧），本机 `date` = 见本笔 commit 时间，HEAD = `c00bc6d` 之后第一笔。
**决定内容**：**judge v3（两域混训）取代 v1 成为参赛模型**；v1 降级为"老域历史版本/对照"，
其数字仍留在仓内作审计链，但**不再是墙上主数**。

### 22.1 ⚠️ 先立一条方法学护栏（比任何一笔账都重要）
**B 卷（留存级 3）只登记、不参与选型。**
上位决定已由用户在**只看 A 域级 2 + 新域级 2 + 族级配对**的证据下做出，所以接下来 v3 在 B 卷上的读数
**不能反过来当"要不要用 v3"的依据**——那叫 selection on test，是我们最不该犯的那类错。
因此规则写死：
- v3 的 B 卷读数出来后，**不管高低都登记**；
- 若明显低于 v1 的 0.9989 ⇒ **不换模型**，改为在同页披露三张卷的全数字
  （老域 / 新域 / B 实例）并写"v3 在 B 实例上比 v1 低 x 点"这条边界；
- 真要回退到 v1 参赛，必须**公开写明回退依据是 B 卷读数**并接受"这就是开卷选型"的批评。
  ——我不建议走这条。

### 22.2 三笔账（顺序即执行序）
1. **级 2（老域）重算重登记**：v3 在 `dataset/judge_v0/holdout.jsonl`（n=1,280，与 v1 那次同一文件）
   acc **0.9984**、召回 **1.0000**、fp 2 / fn **0**、悬置带 0.00% ⟷ v1 0.9953 / fn 4。
   族级配对：比对 457 族，**v1 对而 v3 错 = 0 族**。→ 主数替换为 v3，v1 那组进"历史"。
2. **级 3（B 实例）用 v3 在同一份留存卷上重评估**（`bench/train/results/d7_b/holdout_b_clean.jsonl`）：
   **只读重评估、绝不重采集**（B 环境已不可再现，采集是硬禁区）；**一次读数，不回头调**；
   家族聚类 bootstrap CI 与 ECE 必须**用 v3 的 dump 重算**（`cluster_ci.py`），
   改名表同形 100%、真新面 0 样本这两条限定**原样保留**（v3 不改变这个事实）。
3. **换机一致性（Spark）**：v3 挂 Spark 跑 `bench/spark/judge_latency.py --ref`——
   复用现成跨机逐条对账，报"判定翻转几族 / 最大漂移 / 单发时延"，
   口径同 `bench/d8-judge-spark.md`：**bf16 + transformers，不是 GGUF**（量化路径仍未测，禁写 GGUF）。

### 22.3 随之要改的措辞（请轨道 A 同步，别留自相矛盾的句子）
- `claims.yaml`：新增 v3 三组条目并**替换**指向 v1 产物作为主数的条目（v1 条目可降为"历史/对照"注明）。
- **`EVIDENCE.md` / README 首屏 / deck**：凡"判官 acc 0.9989 / CI [0.9976,0.9998] / ECE 0.0006"
  这类主数一旦换成 v3，**出处文件路径必须一起换**（旧路径留着会让复算对不上，属我最怕的那类"数字与出处分离"）。
- 三句仍**禁用**：**"能识别未见攻击"**（两域都是 v3 训过的环境族别）、**"全面提升"**（新域让出 2 族 CSRF，
  列名在 `bench/results/three_arm/v3_paired_vs_v1_v2b.json`）、**"四层检测栈已在线"**（judge 在线仍未接线，
  Console 那格还是 `STANDBY`，D8 的 B 案没变）。
- **"1653 轮 0 失败"仍属模板档环境**，与 v3 无关，别把它挂到 v3 名下。

### 22.4 我这一轮的执行序与占用
1. v3 跑 B 卷重评估（5090，约 8–10 分钟，一次读数）→ 2. `cluster_ci.py` 重算 CI/ECE（CPU）
→ 3. Spark 换机复测（约 10 分钟机时，只碰 GPU，**不动 `:30000/:8000/:8081/:8082/:30010`**，
   `aegis-lab` 两容器继续常驻不占宿主端口）→ 4. 全部产物与"可粘贴 claims 块"交轨道 A 登记 →
5. 登记后我再动 `EVIDENCE.md` 的 v3 行（数字仍不手写，只从 claims 抄）。
**预计 30–40 分钟内交完产物。** 若 B 卷读数难看，我照 §22.1 原样报，不粉饰、不回退选型。

### 22.5 三笔账执行完毕（qcode，00:47）+ 一处自曝
1. **级 2 老域**：v3 **0.9984**（召回 1.0 / fn 0 / 悬置 0.00%）> v1 0.9953；族级 457 族**零回归**。
2. **级 3 B 卷**（只读重评估、同文件同 seed、**一次读数未回头调**）：acc **0.9992** > v1 0.9989、
   fn 2→**0**、悬置带 0.01%→**0.00%**、CI [0.9980,1.0000]；**唯一变差：ECE 0.0006 → 0.0008**（须同页披露）。
3. **换机**（v3 挂 Spark，bf16 非 GGUF，adapter md5 两侧一致）：单发 median **80.8 ms** / p95 118.5、
   **60/60 全连接、判定翻转 0**、max_abs_drift 1.07e-04。
产物：`bench/train/results/d7_b/v3_*`、`bench/results/judge_latency/spark_v3_single_shot.json`、
账本 `bench/d10-v2-external-eval.md` §10。
**自曝一条要紧的**：我为为 B.6 掩码改 `eval_logit.py` 时误删又补回 `batched()`，留下**重复循环**
（每样本会被评估两遍）——被 `py_compile` 之外的**行为冒烟测试**抓到，B 评估**中止、半成品删除、重跑**，
所以 §22.5 的数字未受污染。两天内两次同类脚本编辑事故（另一次丢右括号），
**我此后改任何评估/训练脚本必须跑 `tests/` + 一个最小行为断言**，请两侧互相盯这条。
**待轨道 A**：claims 登记 v3（可粘贴块见 §23，若我下一步没写出来的话）+ EVIDENCE/deck 主数出处同步。

---

## 23. qcode → 轨道 A：v3 的 claims 可粘贴块（**登记前 v3 数字不得上墙**）

看到 `cb4ee07` 已把 v2b 那三条登记进来（36/36 绿），谢了。v3 上位后主数换了，这块接着登记：

```yaml
- claim_id: v3_b_holdout_acc
  clause: "v3 在留存 B 净卷（n=7266/2327 族）acc 0.9992、召回 1.0000、漏报 0、悬置带 0.00%"
  kind: json
  file: bench/train/results/d7_b/v3_eval_b_clean.json
  selector: accuracy_overall.acc
  expected: 0.9992
  tie: {file: bench/train/results/d7_b/v3_eval_b_clean.json, selector: abstain.ratio, expected: 0.0}

- claim_id: v3_b_holdout_ci
  clause: "家族聚类 95% CI [0.9980, 1.0000]（seed=7，与 v1 那次同参）"
  kind: json
  file: bench/train/results/d7_b/v3_ci_b_clean.json
  selector: cluster_bootstrap_95ci.acc.lo
  expected: 0.998
  tie: {file: bench/train/results/d7_b/v3_ci_b_clean.json, selector: cluster_bootstrap_95ci.acc.hi, expected: 1.0}

- claim_id: v3_b_ece_worse
  clause: "诚实账：v3 的 ECE 0.0008 比 v1 的 0.0006 差 0.0002（唯一变差项，须同页披露）"
  kind: json
  file: bench/train/results/d7_b/v3_ci_b_clean.json
  selector: point.ece
  expected: 0.0008

- claim_id: v3_edulite_holdout_acc
  clause: "v3 考老域 edu-lite 卷（n=1280）acc 0.9984、召回 1.0000、漏报 0"
  kind: json
  file: bench/results/three_arm/v3_by_domain.json
  selector: v3_by_domain.REQ.acc
  expected: 0.9984

- claim_id: v3_external_domain_acc
  clause: "v3 考新域外部靶卷（n=2964）acc 0.9943、v1 同卷只有 0.7294"
  kind: json
  file: bench/results/three_arm/v3_by_domain.json
  selector: v3_by_domain.DVWA.acc
  expected: 0.9943

- claim_id: v3_spark_no_flip
  clause: "v3 挂 Spark 复测：60/60 逐条全连接、判定翻转 0、单发中位 80.8 ms（bf16 非 GGUF）"
  kind: json
  file: bench/results/judge_latency/spark_v3_single_shot.json
  selector: agreement_with_5090.verdict_flips
  expected: 0
  tie: {file: bench/results/judge_latency/spark_v3_single_shot.json, selector: single_shot_ms.median, expected: 80.8}
```

登记时请顺手办两件同步（否则数字与出处分离）：
① `EVIDENCE.md` 判官那张表把主数从 v1 换成 v3，**v1 行保留但改标题为"历史版本/老域对照"**；
② README 首屏与 deck 里任何 "0.9989 / [0.9976,0.9998] / ECE 0.0006" 全部换成 v3 那组，
   并**同页带上 ECE 变差这条边界**；"1653 轮 0 失败"仍标"模板档环境"，与 v3 无关。

## 23. zcode 会话报：千问评审 A-F 落地 + 同窗纪律成文（2026-09-27 凌晨，zcode 侧，HEAD 7eba0a8）

- **A 同窗纪律落地**（本轮核心）：/api/dialog 四源（flow/alerts/audit/monolog/judge-shadow）统一只返回
  最后一段连续活动（_burst_lo，>300s 间隔分段）——泳道讲的是接力，接力必须同窗；跨运行混窗
  是"红蓝对抗录成各说各话"的根因。dialog 内 flow 循环与 state 的 flow_tail 现都走同窗。
- **B 单类调试抽屉**：从顶栏悬浮下拉迁至底部动作条（不压图），注明"仅跑单个漏洞类，用于排障"。
- **C accepted 口径**：卡片副标"坏补丁被门禁拒绝，已转人工"+ 圆环 meta"2 条 accepted 在账"
  ——fail-closed 从疑点变诚实资产。
- **D 判官道占位**：规则全命中时判官道显示"本窗口规则全命中，判官沉默待命"——沉默是设计不是坏了。
- **E 左栏防裁 + F 思摘要 34 字/角色前置**：已落地。
- **经验教训（本会话第三次）**：多锚点 python 批量编辑在中途 assert 崩溃时**不落盘**，
  残缺状态被下一个脚本写入 → main.py 一度引用未定义的 _tnorm_v。整改：批量编辑脚本
  全部改为"逐项 try+收集 ok 清单+最后一次写入"，py_compile 前置到每次写入后。
- **待办交接**：v3 B 卷读数登记（等施工侧 §22 产物）→ EVIDENCE/README/deck 主数换 v3（路径一起换）；
  A2 StepRunner 接线（/api/run 单类演示路径）+ §13.1 文档数字 25 处；9-28 拍摄（硬闸）。
