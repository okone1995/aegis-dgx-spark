# EVIDENCE —— 宣称 → 仓内出处 → 复算命令

> 一页可当场点验。每行 = 一句对外宣称 + 它的硬出处 + 一条你自己能跑的复算命令。
> 规矩：本页与 README 的数字全部来自 [`claims.yaml`](claims.yaml)（`python bench/train/assert_claims.py` 一次全验）。
> 环境：仓根目录，Python 任意 3.10+（跑测试用 `python -m pytest tests/ -q`）。

## 判官（自训 4B LoRA）的深度证据

| 宣称 | 出处 | 复算命令 |
|---|---|---|
| 域内（A 实例，家族级切分）acc **0.9974**，n=1140 | `bench/train/results/tier2_family_noblocked/eval_logit_results.json` → `accuracy_overall` | `python -c "import json;print(json.load(open('bench/train/results/tier2_family_noblocked/eval_logit_results.json',encoding='utf-8'))['accuracy_overall'])"` |
| 域内混淆 **tp666 fp2 fn1 tn471** | 同上 → `confusion_attack_positive` | `python -c "import json;print(json.load(open('bench/train/results/tier2_family_noblocked/eval_logit_results.json',encoding='utf-8'))['confusion_attack_positive'])"` |
| 第二实例净卷 acc **0.9989**、召回 **0.9996**、精确率 **0.9987**、悬置带 **0.01%**、n=**7266** | `bench/train/results/d7_b/eval_b_clean.json` | `python -c "import json;d=json.load(open('bench/train/results/d7_b/eval_b_clean.json',encoding='utf-8'));print(d['n'],d['accuracy_overall']['acc'],d['recall_attack'],d['precision_attack'],d['abstain']['ratio'])"` |
| 家族聚类 95% CI **[0.9976, 0.9998]**、ECE **0.0006** | `bench/train/results/d7_b/ci_b_clean.json`（bootstrap 2000 次，seed=7，重抽样单元=payload 家族） | `python -c "import json;d=json.load(open('bench/train/results/d7_b/ci_b_clean.json',encoding='utf-8'));print(d['n_families'],d['cluster_bootstrap_95ci']['acc'],d['point']['ece'])"` |
| 同卷底座零样本 acc **0.8431**、召回 **0.7618**、悬置带 **14.23%** | `bench/train/results/d7_b/eval_b_clean_zeroshot.json`（`model.adapter=null` 已硬校验） | `python -c "import json;d=json.load(open('bench/train/results/d7_b/eval_b_clean_zeroshot.json',encoding='utf-8'));print(d['model']['adapter'],d['accuracy_overall']['acc'],d['recall_attack'],d['abstain']['ratio'])"` |
| 含**未见响应体**的完整交换臂 acc **0.9989**（n=3633） | `bench/train/results/d7_b/eval_b_clean.json` → `by_input_mode.exchange` | `python -c "import json;print(json.load(open('bench/train/results/d7_b/eval_b_clean.json',encoding='utf-8'))['by_input_mode']['exchange'])"` |
| 训练成本：峰值显存 **30.8 GiB**、wall **7651.1 s** | `bench/train/results/tier2b_family_v1_blocked/train_summary.json` | `python -c "import json;d=json.load(open('bench/train/results/tier2b_family_v1_blocked/train_summary.json',encoding='utf-8'));print(d['peak_vram_gib'],d['wall_seconds'])"` |
| Spark 上单发判读 **中位 73.6 ms / p95 118.2 ms**（**v1 口径，历史对照**；当前参赛 v3 见下一行） | `bench/…` |
| Spark 上单发判读 **中位 80.8 ms / p95 118.5 ms**（**参赛 v3 口径**：`judge_v3_mix_lora`，batch=1，40 条覆盖全 8 类+良性/近良） | 与 v1 同法测量；**两个口径不得互相换算** |
| 换机**判定零翻转**：同 40 条与 5090 逐条对账，连接 40/40、翻转 **0**、最大 `p_attack` 漂移 **0.00029**（含硬件与批形两个变量，不单独归因） | 同上 → `agreement_with_5090`；参照卷 `bench/train/results/d7_b/samples_b_clean.jsonl` | `python -c "import json;d=json.load(open('bench/results/judge_latency/spark_single_shot.json',encoding='utf-8'))['agreement_with_5090'];print(d['n_joined'],d['verdict_flips'],d['max_abs_drift'])"` |

## 闭环与可重复性

| 宣称 | 出处 | 复算命令 |
|---|---|---|
| 一键演示连跑 **1653 轮、0 失败** | `bench/results/soak_5h/soak_summary.json`（5 小时预算，Spark 实机） | `python -c "import json;d=json.load(open('bench/results/soak_5h/soak_summary.json',encoding='utf-8'));print(d['cycles'],d['cycle_fails'],d['first_cycle_utc'],'->',d['last_cycle_utc'])"` |
| **每一轮**都收敛到 verified=8/8 | `bench/results/soak_5h/verified_per_cycle.txt`（逐轮原文入仓） | `python -c "ls=[l.strip() for l in open('bench/results/soak_5h/verified_per_cycle.txt',encoding='utf-8') if l.strip()];print(len(ls),set(ls))"` |
| 端到端真跑（非录像） | Spark：`bash demo.sh` → 末行 `8 findings, verified=8`；`bench/d6-soak.md` 记录重置/毒化/复原三段实弹 | 需 Spark 机时（本仓为纯代码+产物发布面） |

## 红方与治理（"拒绝即演示"）

| 宣称 | 出处 | 复算命令 |
|---|---|---|
| 菜单外自由造招：准入并发射 **47** 发、环境判定命中 **17**、双闸拒案 **22**（其中治理闸真拦 **1**） | `dataset/raw/gen_freeflows.jsonl` / `gen_freeflows_rejects.jsonl` | `python -c "import json;e=[json.loads(l) for l in open('dataset/raw/gen_freeflows.jsonl',encoding='utf-8') if l.strip()];print(len(e),sum(1 for r in e if r.get('res_markers')))"` |
| jail 实弹：webshell **真落盘且执行**（发射前笼内 uploads 为空） | `bench/results/jail_smoke/jail_smoke.json` → `pre_state.uploads` / `post_state.uploads` / `exec_probe=JAIL_RCE_OK` | `python -c "import json;d=json.load(open('bench/results/jail_smoke/jail_smoke.json',encoding='utf-8'));print(repr(d['pre_state']['uploads']),repr(d['post_state']['uploads']),repr(d['post_state']['exec_probe'][:12]))"` |
| jail 档**仍拒**越笼写（`/root` gadget 未发射） | 同上 → `escape_denied_root` 行 `jail_verdict=REJECTED:escape_syspath` 且无 `fired` | `python -c "import json;d=json.load(open('bench/results/jail_smoke/jail_smoke.json',encoding='utf-8'));print([ (s['jail_verdict'],'fired' in s) for s in d['shots'] if s['shot']=='escape_denied_root'])"` |
| 外联改写只可达 canary（假外联闭环） | 同上 → `ssrf_canary.fired.body_head=AEGIS-CANARY-RECEIVED` + `post_state.canary_log` 有源戳 | `python -c "import json;d=json.load(open('bench/results/jail_smoke/jail_smoke.json',encoding='utf-8'));print(d['shots'][4]['fired']['body_head'][:60]);print(d['post_state']['canary_log'][:80])"` |
| 文本栅栏已知可绕：自建 10 发越笼意图样本**漏过 6/10**，但漏过形态全部撞死在 kernel 层 | `bench/d6-jail.md`《对抗审计》节 + 表内笼内实录 | 阅读为证（含每一发的漏过/拦截结果） |
| 隔离面每次自证：token / tmpfs / internal 网 / 外网断 / 宿主 home 不可见 / canary 可达 / **网关:22 不可达** | `bench/results/jail_smoke/selfproof.json`；门票失效即回退 strict | Spark：`python -m engine.jail_ctl status`（撤掉网关规则会 `proof: FAIL`，已实测正反两向） |

## 我们不说什么（固定一节，防选择性上报）

1. **不说"对未见过的攻击泛化"**。第二实例那张卷子按靶场改名表归一后，与训练料的
   **请求面同形比例 = 100%**（`bench/train/results/d7_b/d7_surface_analysis.json` 的
   `T3_share_of_b_families`），真·新面 **0 样本**。级 3 只主张两点：改名不倒、含未见响应体仍判。
2. **不说"字面全未见 / 成绩不来自背题 / 零交集"**。这三句都出现在我们的初版文档里，
   被独立评审以复算戳穿后已作废，作废过程原样保留在 `bench/d7-b-instance.md` 开头。
   附带一条：原口径家族闸**真拦到了 148 族（6.0%）**——那是闸的功劳，不是恒等式的功劳。
3. **不说"无内存漂移"**。浸泡账的 `used_mem_mb=` 列全空（采样器被 ssh 引号吞了字段），
   所以"1653 轮零失败"与"内存"**永不同句**；采样修法已写进 `bench/d6-jail.md`/`bench/d6-soak.md`。
4. **不上级 1 那个开卷 100%**。样本级切分时代的成绩含泄漏，已归档为
   `bench/train/results/tier1_sample_split_leaky/` 并只作反面教材。
5. **不带 TBD 上墙，也不借口径**。Spark 侧我们只测了 **transformers bf16 未量化路径**的单发时延；
   **GGUF / llama.cpp 量化路径从未实测**，所以任何地方出现"GGUF 单发时延"这个说法都是造假。
同理不拿 Spark 的 **73.6 ms（v1）** 去和 5090 的 18.5 ms/条互相换算——后者是批量口径，不同句、不折算。
6. **不说"绝对安全/永不"**。jail 档残余风险逐条写：门票不绑容器身份（复制 cage.json 重建同内容笼
   可无感替换，定级 P2）、网关封禁规则**重启即失**（已入门票自检，失效即 fail-closed 回退 strict）。

## 一条命令全验

```
python bench/train/assert_claims.py        # 全部宣称逐条对账（条数不写死，见 claims.yaml），任一不符退出码 1
python bench/train/lint_language.py        # 对外材料禁用措辞扫描
python -m pytest tests/ -q                 # 含 tests/test_claims.py（宣称闸）+ 前端语法闸
```
