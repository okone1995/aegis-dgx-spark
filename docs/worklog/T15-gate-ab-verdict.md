# T15 · 门禁不降级验收收口（§8-1 定性）+ 执行环境事实

> 接手自 `T14-agent-handover.md` §10 待办第 1 项：修好语法门禁 A/B 探针 + 把功能门禁搬到靶机本机跑。
> 纪律沿用 T14：只写有证据的事实（附 commit / 命令 / 输出），未验证的标 `未验证`。
> 时间：2026-09-28（本机 conda env aegis + DGX 真机）。改动全部落在 `aegis-fork`，**主仓 `engine/`、`skills/` 一字节未动**（`git status --porcelain engine/ skills/` 为空）。

---

## 0. 一句话定性

**T14 §8-1 的那条"未定性"现在定了，而且是两头结论**：

1. `2 failed / 9 errors` **不是门禁的问题，也不是靶机状态的问题**，是上一轮探针自己写坏——
   它在 fail-closed 用例里把死地址 `http://127.0.0.1:1` 当真 base 跑了靶场 pytest。靶机本机同一条命令
   **11 passed / 0.08s**；把 base 换成那个死地址，立刻原样复现 `EEEEEEEEEFF` = 2 failed + 9 errors（11 条全灭）。
2. 语法门禁的**判定逻辑未降级**（同输入同解释器下两侧判定逐字一致），但**确实存在一处真降级并已修**：
   改造后 `runloop` 走 profile 展开，把 `--php` 丢了、只认 PATH 上的裸 `php`。本机与 DGX 上 `php` 都不在 PATH
   ⇒ 补丁轮会**整轮崩溃**而不是一次可见的门禁拒绝。

---

## 1. 上一轮探针的两处自伤（都已修，且加了防再犯的守门）

| 错 | 症状 | 修法 |
|---|---|---|
| 按 `patcher.syntax_ok` / `patcher.php_lint` **猜函数名** | 两侧都取到 `None`，`None + "."` 抛错被外层 `except` 吞掉 ⇒ `syntax_valid` 两侧同为 `None` ⇒ 打印出"一致 ✓"的**假一致** | 改为**从 `engine/runloop.py` 源码抓真实门禁调用行**（结论里连行号一起打印），并按该侧自己的代码路径解析 argv；结论行加**可比性守门**：任一侧不是真 `bool` 就打印 `不可比（…）✗ 探针需修`，**不得**打印"一致" |
| fail-closed 用例把替身 profile 当**关键字参数**传给 `verify.functional_tests`（它没有 `profile` 形参） | `TypeError` 兜底走了无参分支 ⇒ **真跑了靶场 pytest**，`2 failed / 9 errors` 就是这么来的（T14 里那句"附带实测(意外跑到)"） | 改为 monkeypatch `target_profile.active`，并把 `subprocess.run` 换成 **argv 记录器**：探针只捕获命令，**绝不发射、绝不连活靶场**（fail-closed 用例实测 `subprocess_calls=0`） |

**这条教训值得挂墙**：探针的失败模式必须是"大声不可比"，不能是"两侧同样取不到 ⇒ 看起来一致"。

---

## 2. 语法门禁 A/B（修复后，`tools/_ab_secaudit_verify.py`）

```
合法 PHP 过闸:  main=True fork=True 一致 ✓
坏 PHP 过闸(应为False): main=False fork=False 一致 ✓
门禁调用点：main=175: lint = subprocess.run([a.php, "-l", str(CANONICAL_APP)],
          fork=182: gate_argv = _PROFILE.expand_syntax_gate(CANONICAL_APP, php=a.php)
               184: lint = subprocess.run(gate_argv, capture_output=True, text=True,
解释器来源：两侧 argv0 同为 C:/Users/<user>/tools/php-8.4.26/php.exe（fork 侧 PATH 解析=None 仍判定一致）
```

**证据强度自己压一档**（复核意见）：这条 A/B 是"探针按两侧源码各自重建 argv + 真跑 php -l"级别的等价，
**不是**端到端跑过一轮 runloop。`demo_case.php_lint` 两侧源码哈希相同（`711ff47ea4c73e4e`）**只**说明该函数没被改动，
它并不在 runloop 的门禁调用路径上（两侧 `demo_case.py` 整体差 179/48 行）——所以这条不算独立证据，别拿它当等价性证明。

**修复前**（php 不在 PATH，即 DGX 的真实处境）：`不可比（main=True fork=None）`，fork 侧 `EXEC_NOT_FOUND`。
这就是那条真降级——不是判定变松，而是**门禁在靶机上根本跑不起来**。

修的方式沿用仓里已有的约定，不新造概念：
- `profile.gates.syntax` 写**裸令牌** `php` 时，用调用方传进来的解释器路径替换 `argv[0]`
  （与 `verify.functional_tests` 替换 `python` 令牌同一形状）；
- profile 写了绝对路径、或 `${...}` 展开成路径的，**一律不替换**（显式压过推断）；
  **注意口径**：`c-demo-target` profile 写的是 `${AEGIS_PHP_BIN} -l {file}` ⇒ 那条路上 `--php` 本来就不参与，
  换目标时必须设 `AEGIS_PHP_BIN`，别以为本轮修复覆盖了它；
- `FileNotFoundError` 转成 `patcher.PatchError` ⇒ "解释器不可用"是一次**可见的门禁拒绝**，
  不再是从 `except patcher.PatchError` 缝里逃出去的整轮崩溃。
  （复核又抓到一层：Windows 下 `FileNotFoundError.filename` 是 `None`，原先文案会打成"不可用: None"——
  已改为直接打 `gate_argv[0]` 与 `--php` 的值。）
- 单测两条守住双向：`test_syntax_gate_accepts_caller_php_binary` / `test_syntax_gate_explicit_path_not_overridden`。

## 3. 功能门禁 A/B（靶机本机实测）

| 项 | 主仓（改造前） | fork（改造后） |
|---|---|---|
| argv | `<py> -m pytest <root>/target/edu-lite/tests/test_functional.py -q` | **同形**（差异仅 root 不同） |
| cwd | 未设（继承调用方） | `cwd=ROOT`（改进） |
| env | `EDU_BASE/EDU_INSTANCE` | 同两键，由 `profile.gates.functional_test_env` 展开 |
| profile 未声明 `functional_test` | 无此概念（命令写死） | `passed=False` + 原因 `target profile 未声明 functional_test 门禁`，**subprocess 调用数 0**（fail-closed 加强） |

**靶机本机（gx10-9bf3 / aarch64，`~/venvs/aegis/bin/python` 3.12.3 + pytest 9.1.1）**：

```
六个服务全在听（含 30010 素材服务，avatar.png=200；8081 /login=200）
EDU_BASE=http://127.0.0.1:8081 EDU_INSTANCE=a  →  11 passed in 0.08s
EDU_BASE=http://127.0.0.1:1      （复现旧探针的死地址）→  2 failed, 9 errors in 0.06s   EEEEEEEEEFF
```
⇒ 实例 a 状态干净，门禁命令链健康。靶机 `php` **不在 PATH**（`command -v php` 与 `bash -lc` 双探均空，
只有 `~/tools/php/php`）——这条直接解释了 §2 的降级为什么在靶机上是致命的。

---

## 4. 本轮顺带挖出的两处非门禁缺陷（已修）

1. **跑一次回归就把证据队列改了数**。`demo_case` 的闭环收集钩子写的是**跟踪中的**队列
   （`engine.jevtrain.DEFAULT_QUEUE`，与 `--workspace` 无关）：单跑 `tests/test_demo_case.py` 一次，
   队列 `122 → 124`，新增两行是 `path` 与 `body` 皆空的**空样本**且同 `input_hash`；
   committed 的 122 条里空样本为 0。队列条数是 T14 对外的交付口径，不能被一次回归改数。
   修法：`tests/conftest.py` 里 `AEGIS_JEVTRAIN=off`（该开关本就在 T14 §2 表内），生产默认仍为 `on`。
   修后复跑 `122 → 122`。
   **待拍板（未动）**：`collect_from_run` 该不该把"空交换"直接拒入并计数——它会改已导出卷的口径，请负责人定。
2. **Windows 专属假红**：`test_frontend_runtime` 里 `subprocess.run(text=True)` 不给 `encoding`
   ⇒ 按 cp936 解 node 的 UTF-8 输出 ⇒ reader 线程抛 `UnicodeDecodeError`、`stdout` 变空串
   ⇒ 断言退化成 `PASS  A08 in ''`，看着像运行态验收没过。加 `encoding="utf-8", errors="replace"`。
   （此条**先于本轮存在**：把本轮改动 stash 后照样红，已核。）
3. **MANIFEST.tracked.txt 陈旧**：上一轮 v8-mut / v9-integrity 导出物提交后没重生成清单，差 39 条。
   这张表是 Spark 无 git 环境下脱敏闸门的权威文件表，**少登记=少扫**，补登记只收紧不放松。已 `git ls-files` 重生成（638→677）。
4. **上面第 2 条不是一处，是一类**（独立复核把我引到根上：我只修了单点，漏了同款）。全仓扫 `text=True 且未给 encoding` 的调用点，
   命中 14 处，本轮修掉 **11 处**并加上 `or ""` 兜底：
   `engine/verify.py`（**复测门禁本体**：`proc.stdout` 与 `stderr` 同时为 `None` 时 `(None or None).strip()` 会 `AttributeError`，
   即"唯一权威"那道闸自己会崩）、`engine/runner.py`（技能桥接跑官路径）、`engine/marathon.py`、`engine/runloop.py`（语法门禁）、
   `bench/train/assert_claims.py`（**宣称数字的来源**：`re.search(..., r.stdout)` 在 stdout 为 None 时 `TypeError`）、
   `bench/train/desensitize.py`、`tests/test_claims.py`（5 处）、`tests/test_poc_queue.py`（2 处，其一在 utf-8 环境下真红过）。
   **未修 3 处并说明理由**：`engine/jail_ctl.py:33/54` 与 `bench/spark/jail_smoke.py:58` 都走 `docker exec`（只在 Linux 靶侧执行，
   那边父进程默认 UTF-8，缺陷不触发）；留在这里是为了让下一个改 jail 的人知道它没被验过。
   验证口径：**纯环境与 `PYTHONIOENCODING=utf-8` 两种模式下全量结果必须一致**（现均为 `299 passed / 5 skipped`）。

---

## 5. 执行环境事实（下一个 agent 别再栽在"本机没有 python"）

- 本机 python 在 **conda**：`C:/Users/<user>/anaconda3/envs/aegis/python.exe`（3.12.14 + pytest 9.1.1，
  与 `requirements.txt` 的 `pydantic/requests/pyyaml/pytest` 同集）。
  **Git Bash 的 PATH 看不到 anaconda** ⇒ `command -v python` 为空 ≠ 本机没有 python。
- 可复制跑法（引擎面）：`/c/Users/<user>/anaconda3/envs/aegis/python.exe -m pytest tests/ -q`
- 各环境依赖分布（实测，决定哪些测试能跑）：

  | 解释器 | 版本 | pytest | fastapi | 说明 |
  |---|---|---|---|---|
  | `envs/aegis` | 3.12.14 | 9.1.1 | ✗ | 引擎面主用（本轮口径） |
  | `anaconda3`（base） | 3.12.7 | 7.4.4 | ✗ | |
  | `envs/deepseekocr` | 3.12.7 | ✓ | ✓ | 能跑 console 面，但引擎测试有 2 个收集错误（`tests.test_*` 包名解析差异） |
  | `envs/mcp` | 3.12.11 | ✗ | ✓ | |
  | `envs/bettafish` | 3.11.14 | ✓ | ✓ | 未在本轮验证 |
- **控制台默认 GBK**：脚本打印中文或 `✓/✗` 会 `UnicodeEncodeError` 把结论整个吞掉
  ⇒ 探针/脚本一律启动即 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`；
  读子进程输出必须 `encoding="utf-8", errors="replace"`。
- 本机 `php` 在 `C:/Users/<user>/tools/php-8.4.26/php.exe` 且**不在 PATH**（与 DGX 同构）——
  这正是 §2 那条降级在两个平台上都致命的原因。

---

## 6. 本轮口径数字（收口）

| 项 | 数字 |
|---|---|
| fork 全量回归（conda env aegis，`pytest tests/ -q` 原命令、不加任何 flag） | **299 passed / 5 skipped / 0 error**；纯环境与 `PYTHONIOENCODING=utf-8` 两种模式结果一致（原先该命令在无 fastapi 的环境里会被收集期错误**整轮打断、0 条未跑**，本轮按兄弟文件写法补 `importorskip` 修好） |
| 语法门禁 A/B | 判定逐字一致；`--php` 通路已修（修复前 fork 侧 `不可比`）；**未做端到端 runloop 一轮**（见 T14 §10 第 1 条的"仍欠一步"） |
| 功能门禁（靶机本机） | **11 passed / 0.08s** |
| 证据队列 | **122 条**（回归不再改数） |
| fork 提交 | `14d81ee`（A/B+语法门禁）· `c3890cc`（测试隔离）· `a2850e1`（MANIFEST 重生成）· 复核后第二层修复 1 条（同类编码缺陷 11 处 + runloop 文案 + importorskip） |
| 主仓 | `engine/`、`skills/` 未动；仅 `docs/worklog/` 新增本文与 T14 更新 |
| 独立复核 | 派陌生 agent 反证六条：①②③④ 成立；⑤⑥ **我的数字与口径被更正**（见 §7 与上一行的"0 error"）；另抓到 `PatchError` 文案打 `None`、`c-demo-target` 未覆盖、以及我漏修的同款编码缺陷 —— 均已处理 |

## 7. 顺带钉出的三条交付闸缺陷（**先于本轮存在，本轮未动，待拍板**）

改的都不在 T14 §1 允许的动笔面（`skills/ docs/ tools/`）里，所以只挂账不擅动。三条都带自证命令。

| # | 缺陷 | 证据 | 建议改法（一行） |
|---|---|---|---|
| 1 | **脱敏闸漏扫反斜杠 Windows 路径**：`RULES` 第二条写的是 raw 串 `c:` 后跟 `\+`，而 raw 里 `\+` 是**字面加号**、不是"一个或多个分隔符" ⇒ 它只能匹配"`c:` 后面紧跟一个字面加号"这种现实中不存在的形态 | `tools/_ab_secaudit_verify.py` 里 3 处反斜杠宿主路径全不在 `hits` 里。按 `git ls-files` 逐文件数出现次数（utf-8 可读文件）：**fork 漏 17 处 / 7 文件**（另有 2 个非 utf-8 文件未计），**主仓漏 14 处 / 6 文件**（我已把写进 T14/T15 的自己那份先掩掉）。集中在 `bench/train/results/*/train_stdout.log.txt`、`docs/worklog/T0-baseline`、`T9-judge-brief`、`dataset/judge_v0/README.md`、`skills/aegis-self-repair/_test_skill.ps1.bak`。⟸ **更正**：本文第一版这里写的是"22 处 / 9 文件"，那是拿 ripgrep 数"匹配行数"且把未跟踪文件算进来的混合口径，不成立，以本行为准 | 改成 `re.compile(r"[A-Za-z]:[\\\\/]+users[\\\\/]+<user>", re.I)`；改完先 `--check` 看真实命中面，再由负责人决定要不要 `--write` 批量掩（**批量掩会动 17/14 处日志与文档文本**，其中多数是运行日志，不是一行能收尾的事） |
| 2 | **语言闸会把任何第三方 .md 当对外材料**：`bench/train/lint_language.py` 用 `ROOT.rglob("*.md")` 全仓扫禁用措辞，扫进了 `workspace/ppt-build-20260927/node_modules/@oai/.../imported-deck.md:156` 里的 `TODO` ⇒ 本机 `exit=1`。口径说清楚：那目录是**未被 git 跟踪**的构建产物，`rglob` 不看跟踪表，所以**在本机这样的工作树上必红、在干净 clone 上不红**（复核方就是按这条把我的"永远红"降级的） | `PYTHONIOENCODING=utf-8 python bench/train/lint_language.py` → `exit=1`，命中行就是那条 node_modules 里的第三方文档 | 复用脱敏闸已有的 `ARTIFACT_DIR_PREFIXES`（`workspace/`、`node_modules/` 等）做跳过表，两张表合并成一份 |
| 3 | **claims 测试会把失败原因吞成 TypeError**：主仓 `tests/test_claims.py` 5 处 `subprocess.run(text=True)` 不给 `encoding` ⇒ 父进程按 cp936 解码子进程的 UTF-8 输出时 `_readerthread` 抛 `UnicodeDecodeError`，`r.stdout` 变 `None`，断言退化成 `TypeError: 'NoneType' object is not subscriptable`。触发条件写清楚：**子进程输出 UTF-8 时**（Linux 或设了 `PYTHONIOENCODING=utf-8`）才退化成 TypeError；纯 Windows 默认编码下给的是正常 AssertionError | `PYTHONIOENCODING=utf-8 pytest tests/test_claims.py::test_outward_language_gate --tb=long` 顶部即 `_readerthread ... UnicodeDecodeError: 'gbk' codec can't decode byte 0xaa` | 5 处都加 `encoding="utf-8", errors="replace"`。**fork 侧同类本轮已全修**（见 §4-4），主仓 `tests/`、`bench/` 越权未动 |

**归因说明**（避免被当成本轮改坏）：三条都在 HEAD 即存在，复核方查 `git log` 确认相关三处最后一次改动停在 09-25/09-26，
而我两枚主仓提交只动了 `docs/` 与清单。第 2、3 条合起来导致主仓 `test_outward_language_gate` 本轮开工前就是红的（红因不是措辞内容）。
本轮把 T14/T15 里**我自己写进去的**宿主路径与靶机地址掩掉后，主仓 `bench/train/desensitize.py --check` 由红转绿
（HEAD 那版 T14 自带靶机 IP 与用户名，这道闸在开工前也是红的），`test_claims.py` 现 5 passed / 1 failed，
唯一那条红就是上面第 2+3 条。

## 8. 复现命令


```bash
PY=/c/Users/<user>/anaconda3/envs/aegis/python.exe      # conda env aegis（Git Bash PATH 看不到它，用绝对路径）
cd /c/Users/<user>/.zcode/workspace/default/aegis-fork
export AEGIS_TARGET=edu-lite
$PY tools/_ab_secaudit_verify.py            # 语法/功能门禁 A/B（php 不在 PATH 也能跑）
AB_ADD_PHP_DIR_TO_PATH=1 $PY tools/_ab_secaudit_verify.py   # 对照组：把 php 塞进 PATH 后两侧判定相同
$PY -m pytest tests/test_target_profile.py tests/test_demo_case.py -q
grep -c . dataset/review_queue/jevtrain_candidates.jsonl    # 应为 122，跑完回归仍是 122

# 靶机本机功能门禁（只读使用；实例 a，未碰留存 B）
ssh -o BatchMode=yes -p 6012 <user>@<SPARK_HOST> \
  'cd ~/aegis && EDU_BASE=http://127.0.0.1:8081 EDU_INSTANCE=a ~/venvs/aegis/bin/python -m pytest target/edu-lite/tests/test_functional.py -q'
```
