# T12 · 四个 skill 施工书（自主找 bug / 自主渗透 / 自主修补 / 自主进化）

> 交付对象：施工方（GPT-6）。本文自包含：不需要你在场，也不需要读聊天记录。
> 本仓只读约束：`docs/demo-construction-plan.md` 只读；`target/edu-lite/` 只读；不得把宿主路径/凭据写进任何文件。
> 状态记在 `docs/worklog/T8-status-board.md`；本文只写"要做什么、怎么验收"。

---

## 0. 一句话背景

项目目标：把 Aegis **固化成别的智能体可以直接使用的 skill**，实现四件事——① 自主找 bug、② 自主渗透测试、③ 自主修补、④ 自主进化。

**现状是四格里的两格**（见 §1）。你要做的是补齐①④并改造②③，让四个能力都成为"换个目标不用改代码"的 skill。

---

## 1. 现状与地基（**已完成，不要重造**）

### 1.1 引擎侧（在 `aegis-fork`，已真机验证）

| 能力 | 模块 | 关键接口 |
|---|---|---|
| 目标数据化 | `engine/target_profile.py` | `load(name=None)` / `active()` / `available()`；`TargetProfile.primary_source` / `paths_root` / `deploy_argv(inst)` / `expand_syntax_gate(file)` / `functional_test_cmd` / `instances[inst].base` / `instances[inst].surface` / `corpus_keys` / `plugin_dir(cls, default_root)`；支持 `${ENV}` 展开且**未设置变量可见失败**；错误类型 `TargetProfileError` |
| 目标声明文件 | `targets/<name>/profile.yaml` | 字段见附录 B |
| 插件资产随目标走 | `targets/<name>/plugins/<class>/` | overlay 优先；**只有 edu-lite 允许回退**仓内 `plugins/<class>/`，其他目标缺资产必须可见失败 |
| POC 候选归档 | `engine/poc_queue.py` | `PocQueue(path)` / `append(dict)` / `confirm(cid, exchange, markers_hit, origin_run, origin_flow)` / `approve(cid, reviewer, note)` / `reject(...)` / `stats()` / `approved()` / `record_variant(...)` / `normalize_payload_text(text, base)`；默认队列 `dataset/review_queue/poc_candidates.jsonl` |
| POC 写回（人审） | `tools/poc_apply.py` | `--queue --plugins-dir [--apply]`；**默认 dry-run**；只写 `approved + confirmed`；每个载荷配 `.provenance.json`；编号从 90 起（人工载荷习惯占 01–20） |
| 十阶段闭环 | `engine/demo_case.py` | CLI：`--base --case-id --instance --cleanup-policy --strict-llm`；环境：`AEGIS_TARGET` / `AEGIS_PROBE=on|off` / `AEGIS_PROBE_BUDGET_S` / `AEGIS_FAULT_INJECT=llm_unreachable|bad_patch|bad_patch_subtle|post_gate_corrupt|target_down` |
| 事件契约 | `engine/demo_contracts.py` | `VALID_EVENTS` 已含 `fault.injected`、`poc.candidate_queued`、`poc.candidate_failed` |

**run 目录证据（验收时逐项引用）**：`run.json`（`state` / `exit_code` / `stage` / `repair_outcome` / `judge_status` / `learning_status` / `cleanup` / `patch_mode` / `degraded_reasons` / `error`）、`events.jsonl`、`flow.jsonl`、`judge.jsonl`、`verification.json`、`evidence-receipt.json`、`patches/F-001.patch`。

**参考真机结果**（③ 修补能力已被证明可迁移）：`run-20260927-103447-1026` → `succeeded` / `repair_outcome=verified` / `patch_mode=llm_guided`（模板**不含源码锚点**，模型自行定位并把拼接改成绑定参数）/ 三项门禁 `diff_bounds+backdoor+llm_hetero` 全 PASS / 复测 2 条 0 命中 / 业务双断言 + 功能测试过 / 清理还原哈希等于原件。

### 1.2 skill 侧现状（在 `skills/`）

| skill | 覆盖 | 缺口 |
|---|---|---|
| `secaudit-run` | 多轮红蓝闭环编排（attack→detect→patch→verify，直到漏洞数收敛到 0 或预算耗尽）+ 报告 | 绑 edu-lite/PHP 与引擎内部函数 |
| `secaudit-recon` | 枚举端点/参数/上传点 → `surface.json`；自述"不攻击不修补不判定" | **只枚举已知端点，不做漏洞类别假设与发现** |
| `secaudit-attack` | 发射插件载荷集 + 变异，落 `flow.jsonl`，命中转 finding | **库内载荷为主，非自主发现** |
| `secaudit-detect` | 规则优先 + 自训判官 + 125B + 人工升级 | 判官为 advisory（对） |
| `secaudit-patch` | 模板→LLM→`php -l`→三重门禁（fail-closed）→部署 | `php -l` 写死（引擎已数据化，skill 未同步） |
| `secaudit-verify` | 全量重放（含**留出组**）+ 功能测试双门禁 | 同上 |
| `secaudit-report` | 报告 + 收敛曲线（数字必须能追溯） | — |
| `aegis-self-repair` | 桥接 Console 的单注册目标预览版（fail-closed 已实测合格） | 仍以 edu-lite 演示为默认场景 |

### 1.3 四格缺口（你要补的正是这些）

| 能力 | 现状 |
|---|---|
| ① 自主找 bug | ❌ 没有 skill（无"探面→类别假设→造 POC→硬信号确认"） |
| ② 自主渗透测试 | ⚠️ 有（`secaudit-run`），但绑 edu-lite/PHP |
| ③ 自主修补 | ✅ 有且真机验证过，但 `php -l` 写死 |
| ④ 自主进化 | ❌ 没有（只有 `poc_queue` + `poc_apply` + `learning_queue` 零件） |

---

## 2. 四个 skill 的共用规范（**逐条落实，不得豁免**）

### 2.1 桥接模式（照抄 `skills/aegis-self-repair` 的做法）

1. **fail-closed 优先**：任何不确定 → 拒绝并给机器可读错误码，不给"尽力而为"的结果；
2. **操作员 scope 文件 + SHA-256 钉住**（`AEGIS_SCOPE_SHA256`），scope 必须与**目标登记表**条目四字段逐字相符，否则 `target_not_registered`；
3. **只允许回环 origin**（`AEGIS_CONSOLE_ORIGIN` 之类），拒绝非回环、拒绝网络路径、拒绝符号链接；
4. **不跟随重定向**（否则票据/判定会被吃掉）；
5. **幂等**：写操作必须带稳定 `request_id`；重放时校验首次参数，不一致报 `idempotency_parameter_mismatch`；
6. **机器可读输出**：stdout 一行 JSON；错误 `{"status":"error","code":...}` + 非 0 退出码；**"运行失败"是合法结果**，退出码 0 + `claim`/`state` 说明；
7. **结论用 claim 词表**：`verified_restored` / `verified_retained` / `partial` / `unverified`（不得发明"成功"同义词）。

### 2.2 判定口径（红线）

- **硬信号优先**：命中判定只认 ① response marker 命中 ② 状态码变化 ③ 响应长度差分 ④ 功能门禁/复测结果；
- **模型自述不算结论**：LLM 判官（JEV）只能作为 advisory 字段出现，**不得**作为"漏洞存在/补丁生效"的依据；
- 既有的 `poc_queue.confirm()` **要求非空 `markers_hit`** —— 不要把没有硬证据的尝试写成"命中"；
- **口径三档不得混说**：库内变体 / 菜单外新形态 / **新漏洞类别**（第三档现在没有，禁止任何文案暗示已有）。

### 2.3 留痕与可复现

- 每一次发射（含失败的、被策略拒的）都要留证据：请求摘要 + 响应摘要 + 判定 + 时间戳；
- 故障注入必须留痕（`fault.injected` 事件 + 声明"本条不是自然失败"）；
- **缺依赖必须可见失败**，不得静默降级后当成通过；
- 任何人不得在输出里把"静态检查通过""DOM 存在""历史成绩"写成"演示通过"。

### 2.4 安全与授权

- 目标必须在 scope 文件里；发射前过 `governance.payload_policy.assert_allowed`；
- **限速 + 预算硬上限**（请求数 / 时长 / 模型调用次数），并在输出里报"试了几发、第几发命中"；
- 只在隔离、授权的靶场运行；不得指向公网目标；
- 任何文件（含注释、fixture）**不得出现凭据形状字面量**；凭据只走环境变量。

### 2.5 命名与目录规范（每个新 skill 必须齐）

```
skills/<skill-name>/
  SKILL.md                 # 触发场景 / 必答问题 / workflow / 工具入口 / 禁止项 / see-also
  skill-card.md            # 能力边界与未验证面（如实写）
  assets/*.example.json    # 操作员模板（scope / registry 等），可提交
  references/*.md          # 结果契约（claim 词表、字段、错误码）
  scripts/<name>.py        # 桥接 CLI（唯一入口）
  tests/test_<name>.py     # 单测（含负例）
```

---

## 3. 逐 skill 施工单

### 3.1 ① `aegis-hunt` —— 自主找 bug（**最缺，优先做**）

**目标**：给"目标 + 端点/参数候选 + 类别候选 + 预算"，**自己找出发动攻击的 payload**，并把**有硬证据**的候选写进 POC 候选队列。

**输入**（命令行/JSON）：`--target <profile 名>`、`--base`、`--path`、`--param`、`--classes sqli,idor,...`、`--budget-requests N`、`--budget-seconds S`、`--scope <file>`；操作员提供端点候选（来自 `secaudit-recon` 的 `surface.json` 或 profile 的 `instances.*.surface`）。

**搜索阶梯（必须先写死，再考虑模型）**：

1. **库内载荷**：`plugin_dir(class)/payloads/attack/*.txt`（经 profile overlay 解析）；
2. **菜单变异**：`engine.attacker.menu_for(cls)` + `apply_transform()`，对第 1 步中"响应异常度最高"的母弹做变换；
3. **差分探测（类别通用，先做 sqli）**：
   - 单引号 / 双引号 / 反斜杠探针 → 观察状态码与长度变化；
   - 注释探针（`' -- `、`'#`、`';--`）→ 确认可截断；
   - 布尔对（`' AND '1'='1` vs `' AND '1'='2`）→ 确认条件可控；
   - **列宽差分**：`UNION SELECT NULL×n` 扫 n=1..12，只有列宽匹配的那一发不报错/不空；
4. **自省**：按目标语言取 schema（SQLite `sqlite_master` / MySQL `information_schema` / PostgreSQL `pg_catalog`）；
5. **取数**：把"正常页面永不出现"的值作为 marker（如 DDL 文本、掩码背面的完整密钥）——**放进响应能在第 3/5 列渲染的列位**（实测过：放数值列会被格式化吃掉）。

**LLM 兜底（A 档，只在阶梯走完未命中时）**：用 `engine.attacker.plan()`（母弹×菜单）或 `plan_free()`（菜单外新形态，需沙箱隔离门票）；**产物同样过 payload policy、同样入队**，并且在证据里标 `source=freeform`。

**停止条件**：命中硬信号（marker）**或**预算耗尽**或**策略拒绝率超阈值**。命中即 `poc_queue.confirm()`；未命中留 `untried`。

**必须输出**：
```json
{"status":"ok","target":"<name>","class":"sqli",
 "attempts":[{"n":1,"step":"quote_probe","status":500,"len_delta":-3,"verdict":"anomaly"}],
 "hit":{"attempt_index":9,"step":"column_count","marker":"CREATE TABLE","evidence":{"run":null,"flow":"FL-001"}},
 "budget":{"requests_used":9,"requests_cap":60,"seconds_used":12.4},
 "candidate_id":"sqli-1a60bdfc1396",
 "claim":"exploited_confirmed" }
```
`claim` 词表：`exploited_confirmed` / `anomaly_only`（有异常但无硬证据）/ `not_found` / `budget_exhausted` / `policy_blocked`。

**验收（必须全过）**：
1. 单测：列宽差分在 mock 下扫到正确 n；无硬证据时**不得**输出 `exploited_confirmed`；预算耗尽必须如实报；
2. **真机零样本**：在 C 靶场**工作副本**（`targets/c-demo-target/profile.yaml`，见附录 B）上，**只给端点与参数名，不给任何 C 专用 payload**，跑一轮 `aegis-hunt`；报告：试了几发、第几发命中、预算、命中证据；
3. 与"人工已知答案"对照：命中点与已知注入位置一致（不一致要写清差在哪）；
4. 所有尝试进 `poc_candidates.jsonl`（含失败），且 `poc_apply` dry-run 能列出可写回项。

**禁止**：把模型自述当命中；把"试了很多发"说成"精准发现"；自动写回 `plugins/`；跳过 policy 闸门。

---

### 3.2 ② `aegis-audit` —— 自主渗透测试（改造现有 `secaudit-run` 家族）

**目标**：把已在的 7 件套（run/recon/attack/detect/patch/verify/report）**改成 profile + 登记表驱动**，做到"换目标只改数据"。

**施工内容**：
1. 所有 `php -l` → `profile.expand_syntax_gate(file)`；所有 `target/edu-lite` 路径引用 → `profile.paths_root` / `profile.primary_source`；
2. 会话/认证：字段名与 cookie 名从 `profile.auth` 取（`mode/login_path/user_field/pass_field/cookie`），凭据从 `credential_env` 声明的**环境变量**取；
3. 端面：`secaudit-recon` 的端点来源 = `profile.instances[*].surface` + plugin overlay 的 `plugin.yaml: endpoint`（不再只认仓内 `plugins/*/plugin.yaml`）；
4. 判定：保留"规则优先、判官 advisory"；把"多轮收敛到 0"的计数口径写进 `references/result-contract.md`；
5. 与 `aegis-self-repair` 的分工写清：`aegis-self-repair` 是"跑官（单注册目标预览）"，`aegis-audit` 是"审计编排器（多目标）"，两者共用同一份 scope/registry 规范。

**验收**：
1. 用 `--target` 在两份 profile（edu-lite 与任一第二目标）上各跑通一轮，**不改任何代码**；
2. 每轮的 `evidence-receipt.json` 结构同形；`verification.json` 的 `blocked/auth_valid/replay_hit/business_pass` 可读；
3. 一个**没读过引擎源码**的 agent，只读 `SKILL.md` 就能跑通一轮（记录它的原话作为验收证据）。

---

### 3.3 ③ `aegis-repair` —— 自主修补（改造 `secaudit-patch` / `secaudit-verify`）

**施工内容**：
1. `php -l` → profile 语法门禁；功能测试 → `profile.functional_test_cmd`（含 `functional_test_env` 展开）；
2. 补丁模板路径 → `profile.plugin_dir(cls)/patch-template/*.tpl`；**允许"无源码锚点、只有策略"的模板**（C 场已验证模型能自行定位）——此类运行必须标 `patch_mode=llm_guided`；
3. 结论词表：`template` 与 `llm_guided` **必须区分**，禁止把模板补丁写成"模型写的"（`aegis-self-repair` 已把 proof 项改名 `patch_mode_recorded` 并在 verdict 回传 `patch_mode`，照抄）；
4. 三重门禁与"留出组复测"不得放宽（`secaudit-verify` 的原文即规范：blocking your own payloads is not evidence）。

**验收**：
1. 在第二目标上跑一轮，得到 `repair_outcome=verified`，`evidence-receipt` 三项门禁 PASS、`restore_matches_pre_patch_source=true`；
2. 负例：故意改坏模板/注入 `AEGIS_FAULT_INJECT=bad_patch_subtle`，必须**可见失败**且不得部署；
3. 报告里 `patch_mode` 与实际一致。

---

### 3.4 ④ `aegis-evolve` —— 自主进化（新建）

**目标**：把"跑完→沉淀→下轮用上→给出改前/改后对比"做成可执行、可审计、**必须人审**的一条链。

**施工内容**：
1. **沉淀**：`poc_queue`（POC 候选，需 `confirm` 有硬证据）+ `learning_queue`（判官硬负例）+ **语料蒸馏**：把"真打穿"的交换（脱敏后）追加进 `dataset/payloads_corpus/`，供 `attacker.corpus_shots()` few-shot（新增 `tools/corpus_distill.py`，默认 dry-run）；
2. **入库**：只经 `tools/poc_apply.py`（dry-run 默认；只写 approved+confirmed；sidecar provenance 必带）；
3. **用上**：下一轮的 `aegis-hunt` / `aegis-audit` 必须能读到新入库载荷（验证：入库前后各跑一轮，后者能用上新增载荷）；
4. **对比**：同一套评估卷，**改前 / 改后分栏**输出（命中数、试发数、预算、判官指标分栏），**严禁合并成一条曲线**；口径标注"零样本 / 适配后"；
5. **人审闸门不可绕过**：`approve` 必须有人名（reviewer）；无 reviewer 一律拒绝。

**验收**：
1. 端到端演练：跑一轮 → 产生候选（含硬证据）→ 审核通过 → `poc_apply --apply` 写入 → 下一轮真机用上 → 给出改前/改后同卷对比；
2. 负例：① 未命中候选 approve 必须被拒；② 未审核候选 `poc_apply --apply` **一个字节都不写**；③ 蒸馏工具 dry-run 不落盘；
3. 报告明确写出"本轮新增载荷 N 条、其中真正被下一轮用上 M 条"。

---

## 4. 验收总则（怎么算做完）

对**每个** skill，缺一条不算完成：

1. `tests/` 自带测试全绿，且含**负例**（fail-closed 的每一类都要有一条）；
2. `selftest` 子命令：检查 scope/registry/依赖，**缺依赖必须红**；
3. **陌生 agent 实测**：一个没读过引擎源码的 agent，只读 `SKILL.md` + `--help` 跑通一轮，并把它的原话（含卡点）记进 `skill-card.md`；
4. **真机证据**：run id + 证据路径 + 关键数字（不得只有静态检查）；
5. `skill-card.md` 如实写"限制与未验证面"，不许写"已验证泛化"；
6. 输出物命名与目录规范（§2.5）齐备。

**交付物清单（每次提交）**：skill 目录 + 测试 + 真机 run id + `skill-card.md` 更新 + 状态板一行（写明"完成/未完成/证据"）。

---

## 5. 施工顺序与依赖

```
① aegis-hunt  ──► ④ aegis-evolve（用 ① 的候选当输入）
② aegis-audit ──► ④（用 ② 的一轮当"改前"基线）
③ aegis-repair（独立，可并行；已被真机验证，改造量最小）
```

建议顺序：**③（最小）→ ②（薄改造）→ ①（最关键）→ ④（依赖 ①）**；每完成一个就出阶段性结论，不要四个一起并进。

---

## 附录 A · 引擎接口速查

```python
# 目标
from engine.target_profile import active, load, available, TargetProfileError
p = active()                      # 受 AEGIS_TARGET 影响
p.name, p.kind, p.paths_root      # 目标身份与根
p.primary_source                  # 正本（修补对象）
p.deploy_argv("a")                # ["bash", "...deploy.sh", "a"] 之类
p.expand_syntax_gate(p.primary_source)   # ["php", "-l", "<file>"]
p.functional_test_cmd             # 门禁命令字符串
p.instances["a"].base / .surface  # 实例 base 与表面参数
p.plugin_dir("sqli", ROOT/"plugins")     # overlay 优先
p.corpus_keys                     # 造招语料关键词

# POC 候选
from engine.poc_queue import PocQueue, PocQueueError
q = PocQueue()                    # 默认 dataset/review_queue/poc_candidates.jsonl
res = q.append({...})             # 返回 {"duplicate":bool, "candidate"/"existing":{...}}
q.confirm(cid, exchange, ["MARKER"], origin_run="run-...", origin_flow="FL-002")
q.approve(cid, reviewer="<人名>", note="...")   # 必须已 confirmed
q.stats(); q.approved()

# 发射/判定
from engine import attacker, replay, verify
attacker.menu_for(cls); attacker.apply_transform(tid, cls, parent_text, base)
attacker.plan(cls, parents, defense_note, provider="qwen", max_variants=2, budget_s=25, base=...)
attacker.plan_free(cls, parents, defense_note, provider="step", n=8)
replay.replay_payload(base, req_text, name, ctx, markers)
verify.functional_tests(base, instance)          # 走 profile 门禁
```

事件名（必须登记进 `VALID_EVENTS` 才能发）：`stage.started` / `flow.captured` / `business.checked` / `judge.completed` / `gate.completed` / `patch.applied` / `verification.completed` / `learning.queued` / `cleanup.completed` / `run.finished` / `run.failed` / `fault.injected` / `poc.candidate_queued` / `poc.candidate_failed`。

---

## 附录 B · 数据结构

**`targets/<name>/profile.yaml`**
```yaml
kind: php                     # 语言/运行时家族
name: <name>
display: <人类可读名>
paths:
  root: ${SOME_DIR} | <repo 相对路径>
  canonical_sources: [src/index.php]
  deploy_cmd: bash targets/<name>/deploy.sh        # 会附加实例名
  runtime_dir: <dir>
gates:
  syntax: ${PHP_BIN} -l {file}
  functional_test: python targets/<name>/tests/test_functional.py -q
  functional_test_env: {MINILEDGER_BASE: "{base}"}
  business:
    positive: python targets/<name>/tools/business_probe.py --base {base} --case present
    negative: python targets/<name>/tools/business_probe.py --base {base} --case absent
auth:
  mode: none | form_login
  login_path: /signin
  user_field: staff_code
  pass_field: access_code
  cookie: dtk
  credential_env: {user: AEGIS_X_STAFF, pass: AEGIS_X_CODE}
instances:
  a: {base: "http://127.0.0.1:8093", label: work-copy, surface: {warehouse_param: warehouse}}
corpus: {keys: {sqli: [union, select]}}
marker_semantics: no_error_echo | error_echo
```

**目标登记表 `assets/targets.json`（skill 侧）**
```json
{"schema_version": 1,
 "targets": [{"target_id": "edu-lite-a", "case_id": "sqli",
              "target_base": "http://127.0.0.1:8081", "source_root": "target/edu-lite"}]}
```
规则：scope 声明的目标必须与本表某条**四字段逐字相符**，否则 `target_not_registered`；`AEGIS_TARGETS_FILE` 指了但文件不在 ⇒ `targets_file_unreadable`。

**POC 候选行（`poc_candidates.jsonl`）关键字段**：`candidate_id` / `class` / `source`(menu|freeform) / `parent` / `transform_id` / `intent` / `request_text`（Cookie→`{{SESSION}}`、绝对 URL→`{{TARGET}}`）/ `poc_hash` / `evidence_state`(untried|confirmed|miss) / `evidence`（含 `markers_hit`、`status`、脱敏响应摘要、run/flow）/ `review_state`(pending|approved|rejected) / `reviewer` / `reviewed_at`。

**写回 sidecar（`<payload>.provenance.json`）**：`candidate_id` / `class` / `source` / `parent` / `transform_id` / `evidence` / `reviewer` / `review_note` / `reviewed_at` / `written_at` / `queue` / `queue_sha256`。

---

## 附录 C · 红线清单（违反即返工）

1. 不得把宿主绝对路径、用户名、凭据写进任何受跟踪文件（用 `${ENV}`）；仓内有自动闸门会拦；
2. 不得自动写 `plugins/`（写回必须经 `tools/poc_apply.py` 且默认 dry-run）；
3. 不得用模型自述作为命中/修复成功的依据；
4. 不得把"库内变体"说成"新形态"，更不得说"发现新漏洞类别"；
5. 不得在缺依赖时静默降级后报成功；
6. 不得把 A/B 靶场与跨靶指标合并成一条曲线；
7. 不得改动 `docs/demo-construction-plan.md`、`target/edu-lite/`、以及已留存的评估卷；
8. 不得在无人审的情况下让候选 POC 进入攻击集。
