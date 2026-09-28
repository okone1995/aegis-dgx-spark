"""judge v0 训练集组装 —— 三类标签源的原始流量 → 统一 ChatML 分类样本。

输入（仓库内相对路径）:
  dataset/raw/gen_flows.jsonl      攻击面发射流（canonical/变体/失败攻击/near-miss）
  dataset/raw/gen_benign.jsonl     良性会话流（benign_traffic.py --capture）
  bench/spark/benign/*.jsonl       历史良性捕获（D2 冒烟+验收，响应片段制）
  bench/spark/selftest/flow.jsonl  xss 五连跑真实闭环流

输出 dataset/judge_v0/:
  train.jsonl / holdout.jsonl —— 每行 {"messages":[system,user,assistant], "meta":{...}}
  stats.json                  —— 条数/正负比/来源分布/家族账/去重与剔除账目

切分粒度为 **payload 家族**（meta.family，85/15 家族级确定性哈希）：同一 payload 的
exchange/request_only 双形态与变异近重复必落同侧，holdout 不开卷（评审 P1-1）。

样本形态（logit 判读口径）：assistant 只含一个词 attack|benign；
推理时单次前向、比较该位置两候选 token 的 logit —— 因此标签词必须是单 token 语义
清晰的短词（见 dataset/judge_v0/README.md 训练方案）。

用法（Spark 或任意有 python3 处）:
  python3 dataset/build_judge.py [--root .] [--out dataset/judge_v0]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys

SYSTEM = ("你是 Web 安全流量判官。给定一条 HTTP 请求及其响应片段，"
          "判断该请求是否为攻击流量。只输出一个词：attack 或 benign。")

REQ_CAP, RESP_CAP = 700, 1200

# ---------- 协议版本与输入渲染:全仓唯一实现(契约 §2.8) ----------
# 训练/评估/线上服务必须共用本文件的 render_user。此前同一段拼接被写了两遍:
#   engine/judge_service.build_shadow_record(线上服务侧, **缺 scrub**)
#   engine/judge_client.render_user(任务进程侧, **缺 scrub**)
# 结果是线上送进前向的 prompt 与训练卷不同源,却共用同一个 protocol_id。
# 现两处一律委派本函数,并由 tests/test_judge_rendering_parity.py 做同输入同
# prompt_hash 对拍(任一实现分叉即测试红)。
#
# protocol_id 语义 = "prompt 构造 + 判读阈值/悬置带"的版本。改动渲染或阈值必须
# 升版本:线上已有记录按旧版本产出,不得与新版本混算(见 bench/live_regression/)。
#   v1:渲染规格未统一(服务侧自带实现、无 scrub);线上悬置带 0.40-0.60。
#   v2:**本版本**。渲染统一为 render_spec=render-v1,服务侧新增 scrub 与
#      "空响应"拼接规则(与训练卷同形);悬置带不变(仍 0.40-0.60)。
#      v1 线上裁决(judge.jsonl 旧行)不可与 v2 指标合并,需按 v2 重判。
PROTOCOL_ID = "judge-protocol-v2"
RENDER_SPEC = "render-v1"
# 线上协议的悬置带:服务侧**硬判定**的边界(带内 verdict=abstain)。
ABSTAIN_LO, ABSTAIN_HI = 0.40, 0.60
# 离线评估口径的悬置带(bench/train/eval_logit.py 的 BAND_LO/BAND_HI=0.2/0.8)。
# 两带口径不同,各自绑定其产物,禁止混算(差异已登记,见 bench/live_regression/)。
# 本模块只做**登记**,不改离线侧常量:改它要重跑 v3 评估并升 protocol_id,
# 属研究决策,不在代码整改范围内。
EVAL_ABSTAIN_LO, EVAL_ABSTAIN_HI = 0.20, 0.80
# 判读占位:prompt_ids 对 msgs[:-1] 套模板,该词不进前向(见 inference_record)。
ASSISTANT_PLACEHOLDER = "attack"
# 训练集卫生：剔除记录中的环境敏感串（本机路径/端口拓扑属答辩脱敏范围）
SCRUB = [(re.compile(r"/home/[a-z0-9_]+"), "[PATH]")]


def scrub(text: str) -> str:
    for pat, rep in SCRUB:
        text = pat.sub(rep, text)
    return text


def h(x: str, n=12) -> str:
    return hashlib.sha256(x.encode("utf-8", "ignore")).hexdigest()[:n]


# ---------- 输入渲染:训练/评估/服务唯一实现 ----------

def clean_body(body) -> str:
    """截断 + 脱敏,顺序固定:先按 REQ_CAP 截断,再 scrub。"""
    return scrub((body or "")[:REQ_CAP])


def clean_response(response) -> str:
    return scrub((response or "")[:RESP_CAP])


def render_user(method, path, body, status, response, with_response=True) -> str:
    """拼 user 段——**唯一实现**。

    with_response=False → 请求侧样本(request_only),不拼响应段。
    with_response=True  → 一律拼 `响应(<status>):`,即使响应文本为空
    (训练卷 exchange 样本即此形;"空响应就省略整段"是服务侧旧实现的分叉,
    已在 v2 拉齐)。
    """
    body = clean_body(body)
    response = clean_response(response)
    return (f"请求:\n{method} {path}\n"
            + (f"Body: {body}\n" if body else "")
            + (f"\n响应({status}):\n{response}" if with_response else ""))


def make_messages(user: str, label: str) -> list:
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
            {"role": "assistant", "content": label}]


def response_present(response) -> bool:
    """在线判读的形态判据:观测到响应体 → exchange,否则 request_only。

    status 有值但响应体为空串时仍算 exchange(与训练卷一致)。
    """
    return response is not None and str(response) != ""


def inference_record(method, path, body, status, response, with_response=None) -> dict:
    """线上判读输入 → 与训练卷**同构**的 rec(eval_logit.prompt_ids 用 msgs[:-1])。

    形状必须带 assistant 占位:prompt_ids 对 msgs[:-1] 套 chat template,
    缺 assistant 会触发 Qwen3.5 模板 "No user query found"。
    with_response=None → 由 response_present() 自动判断形态。
    """
    if with_response is None:
        with_response = response_present(response)
    user = render_user(method, path, body, status, response,
                       with_response=with_response)
    return {"messages": make_messages(user, ASSISTANT_PLACEHOLDER),
            "meta": {"input_mode": "exchange" if with_response else "request_only"}}


def prompt_hash(rec) -> str:
    """渲染级 hash(SYSTEM+user+渲染规格)前 16 位——线上/离线对拍与溯源用。

    与 engine/demo_case._input_hash 的区别(两者都保留,不互相替代):
      input_hash  观测级:method/path/body(不含 status/response,不含渲染与脱敏),
                  只回答"是不是同一条观测";
      prompt_hash 渲染级:回答"送进前向的文本是否逐字相同"——渲染口径漂移
                  (截断/脱敏/拼接顺序)只有它能发现。
    """
    msgs = rec["messages"]
    return h(f"{RENDER_SPEC}\x1f{msgs[0]['content']}\x1f{msgs[1]['content']}", 16)


def fold(payload: str) -> str:
    """噪声折叠：迭代 URL 解码（双重编码 %25xx 与单次同源，评审 P2-1）→小写→去非词
    字符（空白/注释/引号/编码噪声）。保留任何文字的字母数字（mut_benign 是中文词，
    只留 a-z0-9 会把千余条不同 payload 并成一个 mega 家族）；数字**保留**——rce 网格
    只差序号，折掉数字会把整类并成一个家族、holdout 直接丢格子。"""
    import urllib.parse
    s = str(payload or "").lower()
    for _ in range(3):                       # 双重/三重编码收敛
        t = urllib.parse.unquote_plus(s).lower()   # 每轮解码后再小写：%41→'A' 不欠合并
        if t == s:
            break
        s = t
    return re.sub(r"[\W_]+", "", s, flags=re.UNICODE)


def family_of(method, path, body, source="", payload=""):
    """切分家族键 = **折叠后的请求内容**，全局唯一、不按 source/文件名/cls 加盐
    （评审 P1-1 返工：加盐版下 canonical 与 mut 网格向同端点发射的同一请求分属两族、
    可跨侧，holdout 残余开卷 7 组）。双形态孪生共享请求侧→必同族；跨 source 同请求
    自动并族。fold 意外为空时退回 source+payload 哈希，防空键并万族。
    前提（main 断言兜住）：同请求内容不存在 attack/benign 标签冲突。"""
    key = fold(f"{method} {path} {body or ''}")
    return "REQ:" + (key or ("SRC:" + h(f"{source}|{payload}")))


def make_record(method, path, body, status, response, label, meta, with_response=True):
    user = render_user(method, path, body, status, response,
                       with_response=with_response)
    mode = meta.get("input_mode") or ("exchange" if with_response else "request_only")
    sample = {
        "messages": make_messages(user, label),
        "meta": {**meta, "input_mode": mode,
                 "sample_id": h(method + path + clean_body(body) + str(status) + label
                                + (clean_response(response)[:200] if with_response else ""))},
    }
    return sample


def dedup_key(sample):
    return sample["meta"]["sample_id"]


def load_jsonl(p: pathlib.Path):
    if not p.exists():
        return
    for ln in p.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            yield json.loads(ln)


def _is_b_leak(f: pathlib.Path) -> bool:
    """B 实例（D7 held-out）文件必须被 A 侧构建排除——通配 gen_flows*.jsonl 会
    把 gen_flows_b.jsonl 一起吃进来，一旦入集，"训练未见过的实例"当场作废。
    判据必须精确：`"_b" in stem` 会误杀 wave4 的 gen_flows_blocked/（09-25 实证
    误伤，train 8056→6930），故只认"词尾 _b"与"b_heldout 标签"。"""
    return f.stem.endswith("_b") or "b_heldout" in f.stem


def _is_b_content(sample, src: str = "") -> bool:
    """内容判据（评审 P1：改名/忘带 tag 也要拦）。只认真实坐标形态——
    首版查 json.dumps 全文，被 sample_id 的十六进制子串 "…8082…" 误杀
    （a8fb8082093a），故限定 family/user 两处。"""
    src = src or str((sample.get("meta") or {}).get("source") or "")
    if "b_heldout" in src:
        return True
    fam = str((sample.get("meta") or {}).get("family") or "")
    if "1270018082" in fam:
        return True
    try:
        user = str(sample["messages"][1]["content"])
    except Exception:  # noqa: BLE001
        return False
    return ":8082" in user


def _a_only(files):
    kept = [f for f in files if not _is_b_leak(f)]
    dropped = [f.name for f in files if _is_b_leak(f)]
    if dropped:
        print(f"[guard] A 侧构建排除 B held-out 文件 {len(dropped)} 个: {dropped}")
    return kept


def from_gen_flows(root: pathlib.Path):
    files = _a_only(sorted((root / "dataset/raw").glob("gen_flows*.jsonl")))
    files += _a_only(sorted((root / "dataset/raw").glob("gen_mutations*.jsonl")))
    files += _a_only(sorted((root / "dataset/raw").glob("gen_freeflows*.jsonl")))  # wave5 自由造招
    for f in files:
        for r in load_jsonl(f):
            if r.get("error") or not r.get("status"):
                continue  # 治理拦截/网络错误记录不入训练集（无真实交互）
            label = r["label"]
            base_meta = {
                "class": r.get("class"), "source": r.get("source"),
                "payload": r.get("payload"),
                "markers_hit": r.get("res_markers") or r.get("markers_hit"),
                "family": family_of(r["method"], r["path"], r.get("body"),
                                    r.get("source"), r.get("payload"))}
            for wr in (True, False):  # exchange + request_only 双形态
                yield make_record(r["method"], r["path"], r["body"], r["status"],
                                  r["response"], label, base_meta, with_response=wr)


def from_benign_rows(root: pathlib.Path):
    """benign_traffic 记录制：kind/detail/响应片段（无请求行→以摘要重建请求侧）。"""
    files = [root / "dataset/raw/gen_benign.jsonl",
             *sorted((root / "bench/spark/benign").glob("*.jsonl")),
             ]
    for f in files:
        for r in load_jsonl(f):
            if r.get("label") != "benign":
                continue
            kind = r.get("kind", "page")
            detail = (r.get("detail") or "")[:120]
            pseudo_path = f"/{kind}" + (f"?q={detail}" if detail else "")
            yield make_record("GET", pseudo_path, "", r.get("status", 200),
                              r.get("response_snippet", ""), "benign", {
                "class": "benign_traffic", "source": f"benign_gen:{f.name}",
                "markers_hit": None, "input_mode": "response_only",
                # 良性家族键＝伪请求内容折叠，不含 capture 文件名盐（评审 P1-1 返工：
                # 同一伪请求跨 capture 文件必同族同侧）
                "family": family_of("GET", pseudo_path, "", f.name, detail)})


def from_selftest(root: pathlib.Path):
    for r in load_jsonl(root / "bench/spark/selftest/flow.jsonl"):
        label = r.get("label", "attack")
        body = r.get("body", "")
        # 闭环流家族键＝请求内容折叠（与 gen_flows 全局同键空间，跨源同请求并族）
        fam = family_of(r["method"], r["path"], body, "selftest_runloop")
        for wr in (True, False):
            yield make_record(r["method"], r["path"], body,
                              r.get("status"), r.get("response", ""), label, {
                "class": "xss_stored", "source": "selftest_runloop",
                "markers_hit": None, "family": fam}, with_response=wr)


def split(sample):
    """确定性 85/15 切分：按**家族键**哈希尾数（评审 P1-1）。

    家族键＝折叠后的请求内容（method+path+body，全局去盐）：双形态孪生、跨 source
    同请求、编码/大小写/空白近重复都并入同族→必落同侧，holdout 不开卷。
    （此前按 sample_id 切存在孪生跨侧；首版家族键按 source 加盐仍残余 7 组跨侧。）"""
    return "holdout" if int(h(sample["meta"]["family"])[-2:], 16) % 100 < 15 else "train"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--out", default="dataset/judge_v0")
    ap.add_argument("--used-for-training", action="store_true",
                    help="标记本卷确用于训练；缺省写 used_for_training=false（复审：手涂会被重建抹掉）")
    a = ap.parse_args()
    root = pathlib.Path(a.root)
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    samples, seen = [], set()
    dropped_dup = 0
    for gen in (from_gen_flows, from_benign_rows, from_selftest):
        for s in gen(root):
            k = dedup_key(s)
            if k in seen:
                dropped_dup += 1
                continue
            seen.add(k)
            # 双保险（文件名闸可被 --out 改名绕过）：样本级 source 含 b_heldout 一律拒收
            src = str(s["meta"].get("source") or "") + str(s["meta"].get("payload") or "")
            # 内容判据：B 只可能在 :8082（改名或忘带 --source-tag 也拦得住）。
            # 只查真实坐标形态——首版查 json.dumps 全文，被 sample_id 里的
            # "…8082…" 十六进制子串误杀（a8fb8082093a），教训入册。
            if _is_b_content(s, src):
                b_dropped += 1     # 剔除并计数：A 侧响应偶含 ":8082" 字面量时不该整轮炸
                continue
            samples.append(s)

    if b_dropped:
        print(f"[guard][WARN] 内容判据剔除 {b_dropped} 条疑似 B 样本（A 侧构建不应有 B 料，"
              f"请查是否采集串了 --base :8082）")
    buckets = {"train": [], "holdout": []}
    for s in samples:
        # 评审返工 P2：硬校验用显式 raise，不走 assert（python -O 会旁路）
        if not s["meta"].get("family"):
            raise RuntimeError(f"样本缺家族键: {s['meta'].get('sample_id')}")
        buckets[split(s)].append(s)

    # 评审 P1-1 硬校验①：家族键=折叠请求内容（全局去盐），同请求跨 source 已并族，
    # train/holdout 家族零重叠 ⇒ "同一请求行跨侧"（含双形态孪生）被结构上排除
    fam_t = {s["meta"]["family"] for s in buckets["train"]}
    fam_h = {s["meta"]["family"] for s in buckets["holdout"]}
    overlap = fam_t & fam_h
    if overlap:
        raise RuntimeError(f"家族级切分被破坏，{len(overlap)} 族跨侧: {list(overlap)[:3]}")
    # 硬校验②：同族（同请求内容）不得有 attack/benign 标签冲突（数据卫生哨兵）
    fam_lab = {}
    for s in samples:
        f, l = s["meta"]["family"], s["messages"][2]["content"]
        if fam_lab.setdefault(f, l) != l:
            raise RuntimeError(f"家族标签冲突: {f[:60]}")

    for name, rows in buckets.items():
        with (out / f"{name}.jsonl").open("w", encoding="utf-8") as f:
            for s in rows:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")

    def dist(rows):
        lab = {"attack": 0, "benign": 0}
        src, cls = {}, {}
        for s in rows:
            lab[s["messages"][2]["content"]] += 1
            src[s["meta"]["source"]] = src.get(s["meta"]["source"], 0) + 1
            c = str(s["meta"]["class"])
            cls[c] = cls.get(c, 0) + 1
        return {"labels": lab, "sources": src, "classes": cls}

    stats = {
        "total": len(samples), "dropped_duplicate": dropped_dup,
        "split_unit": "family",  # 切分粒度：payload 家族（非样本），防孪生/近重复跨侧
        "families": {"train": len(fam_t), "holdout": len(fam_h),
                     "total": len(fam_t | fam_h), "overlap": len(overlap)},
        "train": dist(buckets["train"]), "holdout": dist(buckets["holdout"]),
        "train_n": len(buckets["train"]), "holdout_n": len(buckets["holdout"]),
        "used_for_training": bool(a.used_for_training),
        "used_for_training_note": "本卷是否确为某次训练的进料；缺省 false=仅作覆盖/审计重建，"
                                  "v1 adapter 实吃 dataset/judge_v0/（冻结卷）",
    }
    (out / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
