# Aegis 决赛演示手册（单案例闭环 · 现场操作版）

版本：2026-09-26 · 适用入口：`/demo` 作战大屏 + `POST /api/demo/runs` 单案例闭环
配套材料：`JUDGES.md`（评委 100 分映射与最短评审路径）、`docs/worklog/T7-spark-evidence.md`（全部真机证据）

> 本手册只讲**现场怎么演、屏上怎么读、被问到怎么答**；不讲开发历程，也不含未上墙数字。
> 手册里每个数字都带出处；现场若与手册不符，**以屏幕和接口为准**，并如实说明。

---

## 0. 一句话讲什么

正常业务 → 红方注入成功 → **规则与 JEV 判官各自独立判读** → 本地 125B 模型生成补丁 →
三门禁（改动范围 / 危险模式 / 云端异构复核）→ 部署 → 同一注入复测打不动、正常业务仍通过 →
对抗样本进入候选学习队列 → 靶场恢复，**同一 run_id 全程可追**。

三个展示目标对应三块屏幕内容：**红蓝对抗**（左栏 + 中栏阶段条）、**经过验证的自修补**（中栏 diff + 门禁 + 复测证据）、**JEV 判官**（右栏判官卡 + 风险面板 + 逐流裁决）。

---

## 1. 开始前 10 分钟检查单（逐条过，全绿才开演）

在**能连 Spark 的终端**（Windows 上必须用 Git-Bash 的 `ssh`，系统自带 ssh 在这台机器上静默失效）执行：

```bash
SSH="ssh -o BatchMode=yes -p <端口> <用户>@<Spark主机>"     # 目标见交接单,不写进本仓

# 1) 六个端口都在听（应为 6 行）
$SSH 'ss -tlnp | grep -E ":(8000|8081|8082|30000|30002|30010) "'

# 2) 判官身份与设备（关键是 model_loaded=true、device=NVIDIA GB10）
$SSH 'curl -s -m 6 http://127.0.0.1:30002/health; echo; curl -s -m 6 http://127.0.0.1:30002/metadata'

# 3) 演示入口活着（应为 200 / 200）
$SSH 'curl -s -o /dev/null -w "GET / = %{http_code}\n" http://127.0.0.1:8000/; \
      curl -s -o /dev/null -w "GET /demo = %{http_code}\n" http://127.0.0.1:8000/demo'

# 4) 靶场处于**漏洞态**（应无输出：补丁标记 0，hash 4b7c0d2d…）
$SSH 'cd ~/aegis && grep -c "\[patched" target/edu-lite/src/app.php; \
      sha256sum target/edu-lite/src/app.php | cut -c1-16'

# 5) 没有残留锁、没有卡住的 run（应无锁文件；runs 列表里无 queued/running）
$SSH 'cd ~/aegis && (ls -l workspace/.aegis.lock 2>/dev/null || echo "(无锁=可开演)"); \
      curl -s -m 6 http://127.0.0.1:8000/api/demo/runs | head -c 200'
```

**若判官没起**（`/health` 不通，或返回 501 `Unsupported method` = 跑的是旧版）：

```bash
$SSH 'cd ~/aegis && OLD=$(ss -tlnp | grep ":30002 " | grep -oP "pid=\K[0-9]+" | head -1); \
      [ -n "$OLD" ] && kill $OLD; sleep 3; \
      setsid nohup env PYTHONPATH="$HOME/aegis/models/_tf517:$HOME/aegis/bench/_train" \
        "$HOME/.local/share/sglang-ssd-stream/venv-0.3.0/bin/python" engine/judge_service.py \
        --base "$HOME/aegis/models/ms/models/Qwen--Qwen3.5-4B/snapshots/master" \
        --adapter "$HOME/aegis/models/judge_v3_adapter" --port 30002 \
        > ~/logs/judge.log 2>&1 < /dev/null &'
# 模型加载约 55–65 秒后 /health 变为 model_loaded=true，再开演
```

**若 Console 没起（或重启 Console）——一律走唯一权威启动器**：

```bash
$SSH 'bash $HOME/bin/start_aegis_console.sh'
# 输出会报告：/demo 的状态码、PHP_BIN 指向的路径、以及两个密钥是否已从 secrets 载入
```

> **必须用这个启动器，不要裸起 uvicorn。** 原因（2026-09-26 实际事故 S1）：`demo_case` 子进程继承 Console 的环境，
> 而这三项都不在默认 shell 里 —— ① `PHP_BIN`（PHP 8.4.8 在 `~/tools/php/php`，**不在** 默认 PATH）；
> ② 本地 125B 补丁模型所需密钥；③ 云端异构复核所需密钥。
> 裸起 uvicorn 会丢掉它们，预检立即失败、屏幕显示 `preflight_php_missing: PHP_BIN unset & php not on PATH`，
> 后两项会在补丁/复核阶段才暴露。两个密钥只从 `~/aegis/secrets.local.env` 载入（未跟踪、未进 git 历史）。
> 本工程的推送脚本 `docs/worklog/_spark_final.sh` 已改为调用该启动器。

```bash
# 只读核对环境是否带全（只列变量名，不打印任何值）
$SSH 'CPID=$(ss -tlnp | grep ":8000 " | grep -oP "pid=\K[0-9]+" | head -1); \
      tr "\0" "\n" < /proc/$CPID/environ | cut -d= -f1 | grep -E "PHP_BIN|STEP_API_KEY|AEGIS_KEY"'
```

浏览器打开 `http://<Spark>:8000/demo`（评委自带的机器若不能直连，请用投屏/录屏，见 §4 模式 B）。

---

## 1.5 给外网同事看的公网隧道（可选，与现场演示互不影响）

现场之外如果需要让**不在同一内网的人**看到大屏，走服务器上已有的 cloudflared 快速隧道：

```bash
# 查当前隧道与地址（进程活着就说明隧道在跑）
$SSH 'ps -eo pid,etimes,args | grep "cloudflared tunnel" | grep -v grep'
$SSH 'grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" ~/aegis/logs/cf_tunnel.log | tail -1'
```

**实测状态（2026-09-26）**：隧道进程存活，地址 `https://frog-converted-officially-summaries.trycloudflare.com`；
从服务器侧打该地址的 `/api/demo/runs` 返回 **HTTP 200 且内容就是本机 Console 的数据**，`/demo` 返回 200 ⇒ **外网可直接访问，现在就能发出去**。

**关掉后怎么重开**（二进制在 `~/tools/cloudflared`，**不在** `~/aegis/tools/` 下；版本 2026.9.3）：

```bash
$SSH 'cd ~/aegis && setsid nohup ~/tools/cloudflared tunnel --url http://127.0.0.1:8000 --no-autoupdate \
      >> ~/logs/cf_tunnel.log 2>&1 < /dev/null & sleep 8; \
      grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" ~/logs/cf_tunnel.log | tail -1'
```

关掉：`$SSH 'pkill -f "cloudflared tunnel"'`

**两条纪律**：

1. **快速隧道的地址每次重启都会变**（随机子域名）。已经发出去的链接在重启后会立刻失效，必须把新地址重新发给对方 ⇒ **演示期间不要重启隧道**；用 `>>` 追加日志，保留历史地址可查。
2. 这条隧道**把 Console 的完整 API 暴露到公网**（含 `POST /api/demo/runs`，即对方也能真的触发一轮攻击）。要防这一点，就让对方只看回放页、不要点 LIVE；或者演示完立即 `pkill`。

---

## 1.6 服务器代码快照与回滚（同步是覆盖式，必须有回滚点）

服务器 `~/aegis` **不是 git 仓**（无版本历史），本地仓库是唯一权威源；推送是 `tar -x` **覆盖式**（只覆盖、不删除）。
所以每次推送前先拍一份**代码类文件小快照**（309 个文件 ≈ 1 MB，秒级完成）：

```bash
$SSH 'cd ~/aegis && TS=$(date +%Y%m%d_%H%M%S); mkdir -p ~/backups; \
  find . -type f \( -name "*.py" -o -name "*.sh" -o -name "*.json" -o -name "*.md" -o -name "*.html" -o -name "*.js" -o -name "*.php" \) \
   -not -path "./dataset/*" -not -path "./models/*" -not -path "./workspace/*" -not -path "./.jail/*" \
   | tar -czf ~/backups/aegis_code_$TS.tgz -T - && ls -lh ~/backups/aegis_code_$TS.tgz'
# 从快照回捞单个文件：
# $SSH 'tar -xzf ~/backups/aegis_code_<时间戳>.tgz -C /tmp ./engine/demo_case.py'
```

**不要**在服务器上直接编辑代码：那份改动会在下次推送时被覆盖掉（当前 509/509 逐字节一致，说明还没发生过）。要改就在本地 git 改、提交、再推。

---

## 2. 三种演示模式（按现场情况选，不要临时改口径）

### 模式 A：实时跑一轮（推荐，约 2.5–3 分钟）

1. 页面上点 **▶ LIVE 对抗**（等价于 `POST /api/demo/runs`，参数 `case_id=sqli, strict_llm=true, cleanup_policy=restore`）。
2. 屏幕会依次走 10 个阶段；**真实实测耗时**（`run-20260926-125754-39b5`）：

| 阶段 | 实测 | 屏上该看到什么 |
|---|---|---|
| preflight | 0.02s | 服务与依赖就绪、canonical hash |
| baseline | 0.01s | 「基线业务检查 通过」（关键字 `图书馆`） |
| attack | 0.01s | 红方发射行（方法/路径/状态码）、注入命中标记 |
| classify | 0.30s | **同一交换**同时进规则与判官：规则 `matched`、判官逐流 `p_attack`（线上 `judge-protocol-v2`） |
| **patch** | **69.7s** | 125B 本地模型生成候选 diff（这段最久，主讲人可以讲模型身份与技能模板） |
| **review** | **20.0s** | 三门禁逐项 PASS；云端异构复核（讲到"发送的是补丁 diff，不是源码全文"） |
| deploy | 0.56s | 部署版本哈希、备份文件 |
| verify | 0.81s | 攻击 `blocked=true`、重放 0/2 命中、正常业务仍通过、功能测试 passed |
| learn | 0.13s | 候选样本入队条数（本轮去重后入队） |
| cleanup | 0.55s | 靶场已恢复漏洞态（**必须念这句**，见 §6） |

3. 终态由服务端决定：`succeeded` / `partial` / `failed` 三态都会真实出现，不要预告"一定绿"。

### 模式 B：回放已成功的真实 run（网络或云端抖动时的兜底）

- 点 **⟲ 彩排回放** → 选择列表里的 run → 屏幕按 `(run_id, seq)` 从零逐条重放，**回放期间零网络写请求**（不发起攻击、不调模型）。
- 现在车上有 **8 个 `succeeded` 真实 run** 可回放，最近三个：`run-…-6046`、`run-…-a240`、`run-…-39b5`。
- 口径：**"这是本轮之前在同一台机器上真实跑出来的 run，不是动画"**——回放页会显示该 run 的 id、模型版本与耗时。

### 模式 C：判官降级的诚实演示（评委问"如果判官挂了会怎样"时）

```bash
# 停判官 → 跑一轮 → 期望 state=partial（核心修复仍成立，但不许声称全部通过）
$SSH 'cd ~/aegis && kill $(ss -tlnp | grep ":30002 " | grep -oP "pid=\K[0-9]+" | head -1)'
# 跑完记得按 §1 重启判官
```

真实证据：`run-20260926-113020-e4fb`（`partial · repair=verified · judge=unavailable`，事件里如实记 `Connection refused`）。

---

## 3. 屏幕怎么读（每一块的出处）

| 屏幕位置 | 内容 | 数据出处 |
|---|---|---|
| 顶栏 | run_id、案例、阶段、真实耗时、模型/provider、恢复状态 | `run.json` + `model_versions` |
| 左栏 | 红方动作摘要、事件流（含 `(run_id, seq)` 游标） | `events.jsonl` |
| 中栏 | 阶段条、候选 diff、门禁三项、复测证据、学习入队 | `patch.proposed` / `gate.completed` / `verification.completed` / `learning.queued` |
| 右栏 | 判官卡（逐流 `p_attack`、悬置带、模型身份）、风险面板（四要素·基于证据） | `judge.completed` / `verification.completed` / `plugin.yaml: severity` |
| 详情抽屉 | 该条事件原文（响应体/diff 按文本转义呈现） | 事件 `payload`（点左栏任意行） |

**风险面板的正确说法**：这是**"基于证据的修复优先级"**（攻向倾向来自判官、利用结果来自验证器、影响等级来自案例配置、当前状态来自黑板），**不是 JEV 学出来的风险分数**；缺证据显示 `unknown`，不折算为低风险。

---

## 4. 数字口径与出处（被追问时照这个答）

| 说法 | 数值 | 出处 |
|---|---|---|
| 判官服务身份 | `judge_v3_adapter`，base `Qwen3.5-4B`，`protocol_id=judge-protocol-v2`，线上悬置带 0.40–0.60（离线评估口径 0.20–0.80，两者不得互引），设备 **NVIDIA GB10** | `GET :30002/metadata` |
| 逐流裁决 | 攻击流 `p_attack=0.9996/1.0` → `attack`；良性流 `0.0` → `benign`；暖态延迟 **66–119ms** | `workspace/runs/<id>/judge.jsonl` |
| 补丁由谁生成 | 本地 125B（Qwen3.8-Flash）经技能模板适配，`patch_mode=llm_guided` | `patch.proposed.payload` + `run.json.model_versions` |
| 异构复核 | 云端 StepFun，**只发 diff** 不发源码全文 | `model_versions.review_boundary = "cloud: diff only"` |
| 验证口径 | `blocked=true` 需"实发交换数>0 且认证有效且无 marker 命中"；`functional_tests=passed` 是真跑 `test_functional.py` | `verification.json` |
| 真实成功率 | 14 轮真机：**8 成功 / 1 降级 / 5 失败**（失败含 2 次靶场未起、3 次复核拒绝） | `workspace/runs/*/run.json` |
| 单轮耗时 | patch ~70s + review ~10–20s 为主，整轮 **约 2–3 分钟** | `summary.json.stage_durations` |
| 本次没做的 | 战场训练/热更新、B 集重采、浏览器像素级渲染验收 | `docs/worklog/T7-deviation-audit-2.md` §3 |

---

## 5. 三种终态怎么讲（不许洗）

- `succeeded`：六个条件同时成立——补丁 `verified`、判官 `ok`、样本已入队、功能测试通过、清理完成、无降级原因。**这六个条件写死在代码里**，缺一个就降级。
- `partial`：修复证据成立但演示要件未完成（例如判官不可用、功能测试被跳过）。口径："修复本身成立，但**我不声称全部通过**"。
- `failed`：预检/修补/审查/部署/核心验证任一处失败，或清理失败。云端复核不可用时**按门禁失败处理**（fail-closed），不会悄悄跳过复核。
- 判官误报：它对 `/login` 这类无上下文流量会判 attack（实测 `p_attack=0.9996`），**如实显示不预过滤**——这恰好是"正常流也进检测"的证据（验收项 A03）。

---

## 6. 不许说的话（现场红线）

1. 不说"数据出域 0 字节"——本轮**云端复核会发出补丁 diff**（页面已标注边界）。全本地复核配置尚未验证。
2. 不说"判官是三个独立审查者"——复核的多票是**同一个模型的两种渲染 + 一次复测**的多数决。
3. 不说"模型自主发现未知漏洞"——攻击来自技能回放（插件 payload），没有模型选招证据。
4. 不说"样本已用于训练"——只进**候选队列**，需人工审核后才可能进新数据集版本。
5. 不说"演示验收完成"——真机主流程与失败路径有证据、前端数据层有运行态验收（A08–A14，34 条断言），但**真浏览器渲染层未在真机验过**，A04/A06/A07 的真机故障注入未做。
6. 不说"补丁还在线上"——默认 `cleanup_policy=restore`，跑完靶场已恢复漏洞态；要留补丁必须显式 `retain` 并在屏幕上说明。

---

## 7. 评委问题速答（8 问）

1. **"这是真的在做还是演的？"** → 让评委任选一个 `run_id`，当场 `curl /api/demo/runs/<id>` 看快照、`/events?after=0&limit=50` 看事件、`/artifacts/patch-diff` 取真实 diff；工件哈希与文件一致。
2. **"判官和规则的区别？"** → 规则是签名匹配（`matched/no_match`，无命中≠正常）；JEV 是自训的小模型，输入是**请求+响应交换**，输出攻击倾向与悬置带。同一交换同时进两者，互不喂答案（判官输入白名单里没有 label/marker/finding）。
3. **"判官会不会看真值作弊？"** → 输入结构是 `Observation(method,path,body,status,response)`，真值标签不进入 prompt；有专测（`tests/test_judge_client.py`）钉住渲染与训练侧一致。
4. **"补丁是模板还是模型？"** → `patch_mode=llm_guided`：技能模板只给"改哪一段"，改写由本地 125B 完成；`strict_llm=true` 下模型失败/原样输出**直接判失败，不回退模板**。
5. **"怎么保证补丁安全？"** → 三门禁 + fail-closed：改动范围（hunks/lines）+ 危险模式（后门/外联/权限/数据外泄面）+ 云端异构复核（同一模型两形态多数决）；任一不过 → 拒绝，部署源 hash 不变（实测 `run-…-f527`）。
6. **"如果判官挂了会怎样？"** → 跑模式 C：`partial`，核心修复继续，但**不声称成功**（实测 `run-…-e4fb`）。
7. **"学到的东西去哪了？"** → 候选队列（`learning-candidates.jsonl`，含家族归组、脱敏版本、来源引用、审核状态）；B 集永封不重采；现场训练不在本轮范围。
8. **"和上一版（八类 marathon）什么关系？"** → 旧演示保留可用（`bash demo.sh`），新版是**单案例纵向闭环**，把"模型真改代码"和"判官独立判读"显式化；两者共享靶场锁与 canonical 源码。

---

## 8. 收尾（演示后 1 分钟内）

```bash
$SSH 'cd ~/aegis && ls -l workspace/.aegis.lock 2>/dev/null || echo "(无锁=干净)"; \
      grep -c "\[patched" target/edu-lite/src/app.php; \
      sha256sum target/edu-lite/src/app.php | cut -c1-16'
# 期望:无锁文件 / 0 / 4b7c0d2d…（漏洞态）
```

保留本轮 run 目录（拍摄与复核要用）；不要清理 `workspace/runs/`。

---

## 9. 一页速查卡

| 项 | 值 |
|---|---|
| 演示入口 | `http://<Spark>:8000/demo` |
| 靶场 A / B | `:8081` / `:8082`（B 为 held-out，Console 拒打返回 403） |
| 本地 125B / 判官 / 素材 | `:30000` / `:30002` / `:30010` |
| 运行 python | `~/venvs/aegis/bin/python` |
| PHP | `~/tools/php/php`（8.4.8） |
| 锁 | `workspace/.aegis.lock`（旧 CLI 与新入口共用；抢不到锁的旧入口会**退出码 3**） |
| 单案例命令 | `$PY -m engine.demo_case --runs-root "$PWD/workspace/runs" --base http://127.0.0.1:8081 --case-id sqli --cleanup-policy restore` |
| 常见故障 | 判官不通 → §1 重启（约 60s）；`/health` 返回 501 → 跑的是旧版服务；`/demo` 404 → Console 未起或未载新码；run 卡 `queued` → 看 `~/logs/console.log` 与锁文件 |
