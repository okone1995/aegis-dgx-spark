# T14 · Aegis 接手文档（写给下一个 agent）

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

> 目的：让一个**没有参与过本项目**的 agent 能在 30 分钟内知道"东西在哪、怎么跑、怎么验、哪里会踩坑"。
> 纪律：本文只写**有证据**的事实（附 commit / run id / 命令）；未验证的一律标 `未验证`。
> 相关文档：`T12-skill-construction-spec.md`（技能施工书，给施工方）·`T13-skill-construction-log.md`（施工记，含证据与缺口）·`T15-gate-ab-verdict.md`（门禁不降级验收收口 + 执行环境事实）·`T8-status-board.md`（状态板）。

---

## 0. 这是什么 / 两条线

Aegis = **自主安全免疫闭环**：发现漏洞 → 判读 → 修补 → 复测 → 清理 → 沉淀。项目同时跑两条线：

| 线 | 目标 | 现状 |
|---|---|---|
| **A. 决赛交付** | 大屏可跑、口播可对、claims 可核 | 主链可演示（Console `/demo`，**2026-09-27 实测** 台账 42 轮 / 19 轮 succeeded）；**录屏已成片**（`workspace/video/aegis-dgx-spark-demo-20260927.mp4`）；代码冻结**未核验** |
| **B. 能力资产（重点）** | 把"发现/修补/学习"固化成**别的 agent 能直接用**的技能包 + 让攻防数据自动变成判官训练材料 | 4 个新技能包上线（3 个过陌生 agent 验收，第 4 个过两轮）；6 类漏洞阶梯真机命中；判官材料链跑通（队列 122 条） |

**一句话现状**：能力线"能跑、有真机数字、能防自批"已做到；**"成熟"还差**：多靶/多类别的广度、以及一份**值得训练侧接手的卷**（现有卷都带自检告警，不建议直接训）。
（原列第三项"secaudit 的不降级验收"**已于 2026-09-28 收口**：判定逻辑与命令链 A/B 均一致，另修掉一处真降级——见 `T15-gate-ab-verdict.md` §2。）

---

## 1. 仓库与目录地图

| 位置 | 是什么 | 可动性 |
|---|---|---|
| `C:/Users/<user>/.zcode/workspace/default/aegis` | **主仓（演示仓）** | `engine/ demo/ console/ target/ plugins/` **不碰**；`skills/`、`docs/`、`tools/` 可动 |
| `…\aegis-fork` | **改造副本**：引擎去耦合、新阶梯、jevtrain、变异刷量、secaudit 改造都在这里 | 可动（独立 git） |
| `…\demo-range` | **C 靶场留存原件** | **只读**；`docs/integrity-log.md` 只由构建方/负责人落笔 |
| `…\demo-range-work` | C 靶场**工作副本**（在上面跑真机闭环） | 可动 |
| `…\aegis\agent_run\env` | **自包含可运行副本**（引擎+技能+队列，1.3MB）——子 agent 沙箱只能进主仓时用它 | 可动（不入演示链） |
| DGX（`ssh -p 6012 <user>@<SPARK_HOST>`） | **真机**：Console :8000 / 靶场A(edu-lite) :8081 / 靶场B :8082(留存) / 补丁模型 :30000 / 判官 :30002 | 只读使用；不要动 `~/aegis` 之外的东西 |

**关键路径**
```
skills/aegis-{hunt,evolve,jevtrain,self-repair}/   ← 新技能包（SKILL.md + scripts/ + tests/ + references/ + assets/）
skills/secaudit-{recon,detect,attack,patch,verify,run,report}/  ← 老七件套（主仓=原始；fork=已改造）
engine/ladders/{sqli,lfi,idor,ssrf,upload_bypass,rce_deser}.yaml ← 可执行阶梯（数据，不是文档）
engine/{hunt,mutate,jevtrain,poc_queue,target_profile,judge_service,demo_case}.py
engine/{attacker,patcher,verify,detect,runloop}.py  ← secaudit 的 entry.engine 指向它们
tools/{evolve.py, poc_apply.py, skill_doc_check.py, _hunt_cli.py, _jevtrain_cli.py,
       mutate_collect.py, v8_select.py, _ab_secaudit_verify.py, _dbg_hunt_request.py}
dataset/review_queue/{jevtrain_candidates.jsonl, poc_candidates.jsonl, hard_negatives.jsonl}
dataset/judge_v{4-draft,5-skill,6-paired,7-hunt,8-mut,9-integrity}/
docs/worklog/{T8-status-board,T11-skillization-plan,T12-skill-construction-spec,T13-skill-construction-log,T14-agent-handover,T15-gate-ab-verdict}.md
agent_run/{jevtrain-acceptance-*.md, acceptance2-logs/}   ← 陌生 agent 验收报告与证据日志
```

---

## 2. 环境与运行前提（**最容易卡住的地方**）

**变量**（操作员在 agent 任务之外设置）

| 变量 | 作用 |
|---|---|
| `AEGIS_TARGET` | 载入哪个 target profile（`edu-lite` / `c-demo-target` / `miniledger` …） |
| `AEGIS_TARGETS_FILE` | 目标登记表 JSON（技能包只认登记过的目标） |
| `AEGIS_ENGINE_ROOT` | 引擎仓根（技能包靠它定位 engine；未设则沿技能目录向上找） |
| `AEGIS_JUDGE_URL` | 判官服务（默认 `http://127.0.0.1:30002`） |
| `AEGIS_JEVTRAIN_OPERATOR` / `AEGIS_EVOLVE_OPERATOR` | **操作员闸门**：approve/写回必须与 `--reviewer` 逐字一致 |
| `AEGIS_JEVTRAIN=off` | 关掉闭环自动收集（默认开，失败不阻断本轮结论） |
| `AEGIS_PROBE=on` | 开探针（默认 off；否则 `probe-lineage` 取数 404 是正常的） |
| `AEGIS_HUNT_SCOPE_SHA256` / `AEGIS_EVOLVE_SCOPE_SHA256` | 技能桥接用的 scope 钉扎（回退通用名） |
| `AEGIS_C_WORK` / `AEGIS_C_STAFF` / `AEGIS_C_CODE` | C 靶场工作副本与公开测试账号 |
| `PHP_BIN`（注意：引擎 preflight 读的是**这个**，不是 `AEGIS_PHP_BIN`） | PHP 可执行文件 |

**解释器（本机最容易误判的一条，2026-09-28 实测）**

- 本机 python 在 **conda**，不在 PATH：`C:/Users/<user>/anaconda3/envs/aegis/python.exe`（3.12.14 + pytest 9.1.1，
  与 `requirements.txt` 同集）。Git Bash 里 `command -v python` 为空**不等于本机没有 python**——
  这就是上一个 agent 得出"本机无解释器"的原因（错判）。
- 一律用绝对路径调用：`/c/Users/<user>/anaconda3/envs/aegis/python.exe -m pytest tests/ -q`（引擎面够用）；
  `tests/test_api_param_passthrough.py` 等 console 面测试需 `fastapi`（aegis 环境未装 ⇒ 该环境口径下它是 `1 error`，不是回归失败）。
- 本机 `php` 同样**不在 PATH**（`C:/Users/<user>/tools/php-8.4.26/php.exe`），DGX 也一样（只有 `~/tools/php/php`）
  ⇒ 任何"只认 PATH 上裸 `php`"的门禁改动在两个平台上都会翻车（T15 §2 的真降级就是这条）。
- 控制台默认 **GBK**：打印中文或 `✓/✗` 会 `UnicodeEncodeError` 把结论整段吞掉 ⇒ 脚本启动即
  `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`；读子进程输出必须显式 `encoding="utf-8"`。

**凭据**：只在 `~/.aegis-local-secrets.env`（本机，权限 600），**任何文件不得出现凭据字面量**。提交前跑 `python bench/train/desensitize.py --check`。

**隧道**（本机 → DGX；会掉，验收脚本要自带探活重连）
```bash
ssh -o BatchMode=yes -N -p 6012 -L 8081:127.0.0.1:8081 <user>@<SPARK_HOST>   # edu-lite
ssh -o BatchMode=yes -N -p 6012 -L 30002:127.0.0.1:30002 …                          # 判官
```

**口径红线（不得混引）**
- 判官 = **v3 参赛卷**（`adapter=judge_v3_mix_lora`）；线上悬置带 **0.40–0.60**、评估带 **0.20–0.80**，两带**不得互引**；
- 时延统一 **80.8 / 118.5 ms**（**73.6 是 v1，禁引**）；GGUF **从未实测**；
- 判官读数**只作分歧分析**，不得当作"漏洞存在/补丁生效"的依据；**不得**说"识别未见过的攻击"。

---

## 3. 技能包：怎么跑、闸门在哪

| 技能 | 干什么 | 入口（可复制） | 闸门 |
|---|---|---|---|
| `aegis-hunt` | **自主找洞**：信号驱动阶梯（基线→探测→差分→定向取数），命中只看 marker | `python engine/hunt.py --path /download --param file --class lfi --base http://127.0.0.1:8081`（CLI 亦见 `tools/_hunt_cli.py`） | 只允许回环 base、目标须在登记表、预算硬上限、`--scope` 文件授权 |
| `aegis-evolve` | **沉淀→人审→写回→下轮用上** | `python tools/evolve.py {report,review,apply,verify-written,compare}` | review/apply 需 `AEGIS_EVOLVE_OPERATOR` + `--reviewer` 逐字一致 |
| `aegis-jevtrain` | **训练材料链**：收集→硬信号打标→配对→人审→导出 | 见 §5 命令总表 | approve/export 需 `AEGIS_JEVTRAIN_OPERATOR`，且**被批样本必须有硬信号标签** |
| `aegis-self-repair` | 跑官：注册目标上前提校验 + 预览闭环 | `skills/aegis-self-repair/scripts/…`（见其 SKILL.md） | 目标登记表四字段逐字相符 |
| `secaudit-{recon,detect,attack,patch,verify,run}` | 蓝/红/修补/复测六件套 | **声明式**：各技能 `runner.yaml` 的 `entry.engine` 指向 `engine.{replay,detect,attacker,patcher.apply_template,verify.gate,runloop.main}`；`runloop` 有 CLI：`python -m engine.runloop --base … --class …` | 与主链同一道 payload policy + scope；verify 是**唯一权威** |

**验收状态**：`self-repair` 首轮 ✅｜`hunt` 两轮 ✅（第二轮"拿来就能用"）｜`evolve` 同一轮 ✅｜`jevtrain` **两轮**（第一轮文档面 11 项 → 修 6+5 项；第二轮**真跑**主链全通并抓出两处真缺陷 → 已修）。
**闸门是署名与流程约束，不是 agent 绕不过的权限边界**（2026-09-28 独立审阅的绕过实验：同进程自设环境变量与署名即可通过）。真正的隔离需要操作员与 agent 处于不同权限域、或审批记录落在 agent 不可改写的外部存储上。演示中**不得**把字符串匹配当作安全隔离的证明。
**四个技能的闸门共同点**：操作员身份**必填且逐字相同**（`--reviewer` 留空会被 argparse 拒；不匹配返回 `operator_gate_not_satisfied`）。

---

## 4. 引擎改造面（fork）

| 模块 | 做了什么 |
|---|---|
| `target_profile.py` + `targets/<name>/profile.yaml` | 目标数据化：路径/门禁/实例/认证/语料关键词 + plugin overlay + `${ENV}` 展开；**缺 profile 或变量可见失败** |
| `hunt.py` | 阶梯执行器；步型 `single/pair/sweep/rotation/introspect/exfil_targeted` + **新增 `request`**（发原始请求文本，覆盖 POST/表单/multipart）+ **LLM 兜底**（`--llm-fallback`，默认关；命中仍只看 marker） |
| `mutate.py` | **变异刷量**：把阶梯每个探测点当母弹过变换菜单（sqli 5 招），一轮 24 母弹 → 106 发 → **入库 101 条** |
| `poc_queue.py` + `tools/poc_apply.py` | 造招产物证据化归档 + 审核状态机（默认 dry-run，只写 approved+confirmed 带 provenance） |
| `jevtrain.py` + `tools/_jevtrain_cli.py` | 收集（真机 run / 外部流 / hunt）/硬信号打标/B 集拒入/配对/报告/人审/导出 + **导出物三项自检** |
| `judge_service.py` | 修掉"脱敏残留字面量导致裸跑 FATAL"，缺省按家目录拼路径 |
| `attacker.py` | **修掉长期悬案**：freeform 造招 0 命中的根因是提示词里的字面词 `METHOD`，模型照抄 ⇒ 被语法闸整批拒 |

---

## 5. 判官训练材料链（命令总表）

```bash
BR=skills/aegis-jevtrain/scripts/aegis_jevtrain.py
python $BR selftest        # 只读：环境/队列/引擎
python $BR report          # 只读：队列统计 + 判官分歧/误报/漏报（误报漏报最多各列 20 条）
python $BR pairs           # 只读：攻良配对覆盖（未配对家族会训出偏标签的判官）
AEGIS_JEVTRAIN_OPERATOR="署名" python $BR approve --id <sid> --reviewer "署名"
AEGIS_JEVTRAIN_OPERATOR="署名" python $BR export --version vX-name --reviewer "署名"
# 对比用（engine 侧，不过闸门，别拿它做人审）：python tools/_jevtrain_cli.py {report,pairs,export}
```

**三条纪律（写进代码）**：① 标签只来自**硬信号**（判官读数仅作分歧分析）；② **B 留存家族拒入**且已封家族桶不重切；③ **队列 ≠ 已训练**。
**导出物**：`train.jsonl / holdout.jsonl / stats.json / MANIFEST.json / TRAINING.md`；`MANIFEST` 里带 `balance_warnings`、`traceability`、`split_independence`、`b_sealed_registry`。
**当前卷与结论**：`v7-hunt`（21 条）、`v8-mut`（116 行，6 攻/110 良）、`v9-integrity`（自报三条告警：**切分不独立**、**holdout 单侧缺标签**、严重不平衡）⇒ **现有卷都不建议直接训**（v8 更适合当"误报专项集"）。
**队列**：`dataset/review_queue/jevtrain_candidates.jsonl`（**122 条**，含攻良配对）。

---

## 6. 靶场：端点事实表（edu-lite，实测）

| 类别 | 端点（方法） | 关键参数（profile surface） | 判定 marker |
|---|---|---|---|
| sqli | `GET /news/search` | `search_param q / keyword` | 语法泄漏特征 |
| lfi | `GET /download` | `dl_param file / path` | `EduBackLogger` `avatar_url_field` `logFile` |
| idor | `POST /admin/user-edit` | `uid_param uid / target`、`sig_field signature / motto` | `"updated":"bob"` / `"updated":"admin"` |
| ssrf | `POST /profile/avatar-url` | `avatar_url_field avatar_url / pic_url` | `"bytes":` `"head":` |
| upload_bypass | `POST /profile/avatar` | `avatar_field avatar / picture` | `VULN_CONFIRMED` |
| rce_deser | `POST /admin/import`（**JSON 体**） | `import_param backup / restore` | `VULN_CONFIRMED_D1/D2/DV1/DV2` |
| brute_no_lock | `POST /login` | `user_field username`、`pass_field password` | `bad credentials` |

**六个阶梯的真机结果**（全部有对照）：sqli 零样本 21 发命中；lfi `../src/app.php` 命中（假值 404）；idor 扫到 uid=3=bob（改自己命中 admin）；ssrf 拉回本站内容（不存在路径 502 对照）；upload 四个被漏扩展名全过（`.php` 400 对照）；rce_deser D1/D2 命中（换 form-urlencoded **不中** ⇒ 该端点只认 JSON 体）。

**C 靶场**：留存原件只读；工作副本上跑通过真机闭环 `run-20260927-103447-1026`（`patch_mode=llm_guided`、三项门禁 PASS、`restored_sha256` == 留存原件）。第三层"修补移植"的**独立复核未做**；`integrity-log.md` **仍过期**（写着"first blind session has not happened"，而盲评已跑两场 + 已记录披露）——**由构建方/负责人落笔**。

---

## 7. 怎么验证（照抄即可）

```bash
cd …/aegis-fork
export AEGIS_TARGET=edu-lite AEGIS_ENGINE_ROOT=$PWD AEGIS_JUDGE_URL=http://127.0.0.1:30002
python -m pytest tests/ -q                      # 引擎回归（跑全量；最近一次与本文相关的子集 `test_hunt*`+`test_jevtrain*` 10 条全绿）
python engine/hunt.py --path /download --param file --class lfi --base http://127.0.0.1:8081
python tools/_jevtrain_cli.py report | python -c "import sys,json;print(json.load(sys.stdin)['by_truth'])"
python tools/skill_doc_check.py --also-read ../aegis/skills     # 文档债检查（应输出 OK: 无未解析引用）
python tools/_ab_secaudit_verify.py                              # secaudit 门禁来源 A/B（见 §9 的坑）
```

---

## 8. 已知缺口（谁接手都要先知道）

1. ~~**secaudit 不降级验收未完成**~~ **已收口（2026-09-28，见 `T15-gate-ab-verdict.md`）**：探针两处自伤已修并加了"不可比就不许打印一致"的守门；
   语法门禁 A/B 判定逐字一致（合法 PHP 两侧 True / 坏 PHP 两侧 False，`php_lint` 源码哈希两侧相同），
   **并修掉一处真降级**——改造后 `runloop` 只认 PATH 上的裸 `php`、把 `--php` 丢了，而本机与 DGX 的 `php` 都不在 PATH
   ⇒ 补丁轮会整轮崩溃而非可见拒绝；功能门禁在**靶机本机 11 passed / 0.08s**，原记的 `2 failed / 9 errors`
   经实弹复现钉死为**旧探针把死地址 `http://127.0.0.1:1` 真跑了 pytest**（`EEEEEEEEEFF` 一模一样），不是门禁也不是靶机状态问题。
2. **brute_no_lock 阶梯未写**：判定是"第 6 发仍被处理"，而 marker 第 1 发就出现 ⇒ 套"marker 即命中"会假阳性，需专用步序判定。
3. **LLM 兜底"能打中"未证**：接线与真机通路已验（有命中不叫模型；无命中则调用模型、提议走闸门），但还没有"阶梯失败、模型成功"的实例。
4. **没有值得训练的卷**：见 §5；且 `E2/E3 近似重复` 未做（需训练侧 overlap 工具，本侧宁标 `not_computed`）。
5. **主仓 `engine/attacker.py` 的 METHOD bug 未修**（演示仓受保护）：补丁在 `docs/worklog/method-prompt-fix.diff`，要动由负责人决定——**注意与已拍成片的口径一致性**。
6. **决赛项**：代码冻结**未核验**（不由我宣布）；C 仓三项登记（integrity-log / 第三层复核 / oracle 复核）需构建方落笔。
   **另需先修闸**：主仓 F5 三道闸里有两条现在是红的，但红的都不是材料内容而是闸自身——
   脱敏闸漏扫反斜杠 Windows 路径（`RULES` 里第二条 raw 串的 `\+` 是字面加号；按跟踪文件数：fork 漏 17 处/7 文件、主仓 14 处/6 文件）、
   语言闸把 `workspace/ppt-build-*/node_modules/**` 里的第三方 `TODO` 当对外措辞（该目录未被跟踪 ⇒ 本机工作树上必红、干净 clone 不红），
   外加 `tests/test_claims.py` 五处 `subprocess.run(text=True)` 不给 `encoding` 会把失败原因吞成 `TypeError`。
   **同类编码缺陷 fork 已全仓扫清 11 处（含 `engine/verify.py` 复测门禁本体、`bench/train/assert_claims.py` 宣称数字来源）；主仓 `bench/`、`tests/` 越权未动。**
   证据、口径与一行改法见 `T15-gate-ab-verdict.md` §7 与 §4-4（改面超出本文 §1 授权的动笔面，等负责人拍板）。
7. **`collect_from_run` 允许"空交换"入队**（待拍板，本轮发现未动）：靶机 fixture 场景下它会收进
   `path` 与 `body` 皆空、同 `input_hash` 的样本（committed 的 122 条里空样本为 0）。
   要不要按"空即拒入并计数"——**会改已导出卷的口径**，请负责人定了再动（详见 T15 §4-1）。

---

## 9. 踩过的坑（照做能省几小时）

| 坑 | 症状 | 做法 |
|---|---|---|
| 内联 `python -c "…"` 带引号 | 参数被吞、语法错 | **一律写 `.py` 文件执行** |
| PowerShell 无 heredoc | `<<'MSG'` 报"缺少文件规范" | 提交信息/长脚本**写文件**，用 `git commit -F <file>` |
| 提交信息里带 `"` | 命令被截断、commit 静默不落 | 信息**不用双引号**，或走 `-F` |
| `rg` 对带反斜杠的通配路径 | **静默无输出** | 关键搜索用 `Select-String` 显式路径，且**先用已知存在的串做正向对照** |
| `Select-String` 同链静默空 | 误判"没有该字样" | 同上：**负结果必须正向对照**（我自己栽过两次） |
| 后台进程配额满 | `background_process_limit_reached` | 用 `Start-Process … -WindowStyle Hidden` 分离进程 |
| 隧道会掉 | `ConnectionError` / 登录失败 | 脚本自带探活 + 重开；长验收前先 `curl /login` |
| `.format()` 处理请求模板 | JSON 花括号/`{{...}}` 占位符被吃 | request 模板**不过 format** |
| PHP 序列化长度 | gadget 不执行、无 marker | `s:<len>:"…"` 的 len 必须精确（`uploads/x.txt`=18） |
| PowerShell 写 JSONL 带 BOM | 首行 `json.loads` 失败 | 读取一律 `utf-8-sig`（写入仍无 BOM） |
| 目标未登录 | 返回 **HTTP 200** 的 login-required 页（`html()` 不设状态码） | 别把匿名 200 当"不需要登录"，看响应体 |
| 子 agent 沙箱 | 只能进主仓，`aegis-fork` 不可达 | 用 `agent_run/env/`（自包含副本，含引擎+技能+队列） |
| Git Bash 里 `command -v python` 为空 | 误判"本机没有 Python 解释器" | python 在 conda（见 §2）；**别的 agent 说"没有"要先问它跑在哪个 shell**，PATH 差异不是机器事实 |
| 探针/测试自己写坏 | "两侧同为 None"打印成"一致 ✓"；`TypeError` 兜底把死地址真跑了 pytest；cp936 解码把 `stdout` 变空串伪装成验收失败 | 探测失败**必须**走到"不可比/大声失败"分支，不许退化成一致；负结果一律配正向对照 |
| `subprocess.run(text=True)` 不给 `encoding`（**一类，不是一处**） | 子进程输出 UTF-8 时 `r.stdout` 变 `None`，消费方退化成 `TypeError`/`AttributeError`，真实失败原因被吞；fork 侧扫出 14 处调用点 | 见到这类红先怀疑解码；修的时候**全仓扫一遍同类的**（`grep text=True` 且无 `encoding`），别只修眼前那一处（我第一版就只修了单点，被复核抓出来） |
| 数"漏过面/命中面"用了混合口径 | 我把 ripgrep 的"匹配行数"且含未跟踪文件的结果当成"跟踪文件出现次数"报成 22 处/9 文件，真实是 17 处/7 文件 | 计数前先说清口径（跟踪表？出现次数还是行数？非 utf-8 文件算不算），并用 `git ls-files` 逐文件数 |
| 跑一次回归改证据队列 | `jevtrain_candidates.jsonl` 122 → 124（两行空样本） | `tests/conftest.py` 已钉 `AEGIS_JEVTRAIN=off`；对外报数前 `grep -c .` 复核 |

---

## 10. 待办（按建议顺序）

1. ~~修好语法门禁 A/B 探针 + 把功能门禁搬到靶机本机跑，给 §8-1 定性~~ **已完成（2026-09-28，`T15-gate-ab-verdict.md`）**：
   定性=判定逻辑未降级 + 一处真降级（`--php` 被丢）已修；`2 failed / 9 errors` 钉死为探针自伤。
   **仍欠一步端到端**：`--php` 修复只到 A/B 与单测，**没有**在 DGX 上用 fork 跑一整轮补丁闭环（fork 未部署到靶机）——`未验证`，要接就接这条。
2. `brute_no_lock` 的步序判定（或明确记为"本类不支持"）；
3. 多靶（MiniLedger / edu-lite profile 化已具备）→ 攻侧正样本翻倍；
4. 训练侧接手一份**平衡且有独立 holdout** 的卷（需要 3 之后）；
5. 决赛：冻结核验 + C 仓三项登记（**均需人**）。
