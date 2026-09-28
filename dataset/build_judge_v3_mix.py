"""v3 混训卷 = 老域（edu-lite）教材 + 新域（外部靶 DVWA + 外部样式）教材，**两张原卷拼起来**。

动机（实测驱动，不是"多多益善"）：v2b 只用外部靶教材训练，在同一张 edu-lite 老卷上
acc 0.9953 → **0.9797**、漏报 4 → **22 条**、并冒出 v1 没有的 1.56% 悬置带
（`bench/results/three_arm/v2b_on_judge_v0_holdout.json` vs
`bench/train/results/tier2b_family_v1_blocked/eval_logit_results.json`）。
**v2b 是专才不是通才**：它一条 edu-lite 流量都没见过。
所以正确做法不是让 v2b 顶掉 v1，而是**混训**，看"吃到新环境数据"是否要以"丢掉老域能力"为代价。

三条硬约束（都是本项目踩过的坑）：
  1. **不改两张原卷的切分**：train 就是两份 train 的并、holdout 就是两份 holdout 的并。
     各自的家族级留出性质由各自建卷器保证（`judge_v0` 家族级切分、`judge_v2_merged` 家族级切分），
     跨源不冲突靠**命名空间**（新域家族一律 `DVWA:` 前缀，老域是 `REQ:`）——脚本硬断言，不靠自觉。
  2. **留存 B 卷家族零重合**：拼接后逐条对 B 家族核，命中即整卷作废（拒绝产出，不是"剔一剔"）。
  3. 统计单位仍是家族；两域各自出标签/模块分布，方便事后按域切片评估。

用法: python dataset/build_judge_v3_mix.py --out dataset/judge_v3_mix
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dataset"))


def rows(p: pathlib.Path):
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dataset/judge_v3_mix")
    a = ap.parse_args()
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)

    srcs = [("edulite", "dataset/judge_v0"), ("external_dvwa", "dataset/judge_v2_merged")]
    parts = {}
    for split in ("train", "holdout"):
        acc = []
        for tag, d in srcs:
            rs = rows(ROOT / d / f"{split}.jsonl")
            for r in rs:
                r.setdefault("meta", {})["domain"] = tag
            acc.append((tag, rs))
        parts[split] = acc

    tr_fam = {r["meta"]["family"] for _, rs in parts["train"] for r in rs}
    ho_fam = {r["meta"]["family"] for _, rs in parts["holdout"] for r in rs}
    leak = sorted(tr_fam & ho_fam)
    assert not leak, f"跨源家族泄漏 {len(leak)} 族，例：{leak[:3]}"

    # B 卷重合闸：读留存卷家族核，命中即拒绝产出（B 只读，不外流内容）
    import build_judge_v2_dvwa as BJ
    b_cores = set()
    for p in ("bench/train/results/d7_b/holdout_b_clean.jsonl",
              "dataset/raw/gen_flows_b.jsonl", "dataset/raw/gen_mutations_b.jsonl"):
        f = ROOT / p
        if not f.is_file():
            continue
        for r in rows(f):
            m = r.get("meta") or {}
            core = m.get("family", "")
            b_cores.add(core.split("REQ:", 1)[-1] if "REQ:" in core else core)
    hit = sorted({f.split("REQ:", 1)[-1] for f in (tr_fam | ho_fam)} & b_cores)
    assert not hit, f"新卷与留存 B 卷家族重合 {len(hit)} 族，拒绝产出。例：{hit[:3]}"

    stats = {"sources": {tag: {"train": len(rs), "holdout": len(dict(parts['holdout'])[tag]) if tag in dict(parts['holdout']) else 0}
                         for tag, rs in parts["train"]},
             "train_n": sum(len(rs) for _, rs in parts["train"]),
             "holdout_n": sum(len(rs) for _, rs in parts["holdout"]),
             "families": {"train": len(tr_fam), "holdout": len(ho_fam), "cross_split_overlap": 0},
             "b_family_overlap": 0, "b_cores_known": len(b_cores),
             "domains_in_holdout": {t: sum(1 for r in rs if r["meta"]["domain"] == t)
                                    for t, rs in parts["holdout"]},
             "labels": {},
             "note": "两域原卷拼接、切分不改；家族命名空间隔离由断言保证",
             "reproduce": "python dataset/build_judge_v3_mix.py --out dataset/judge_v3_mix"}
    for split in ("train", "holdout"):
        lab, dom = {}, {}
        for tag, rs in parts[split]:
            for r in rs:
                lab[r["messages"][-1]["content"]] = lab.get(r["messages"][-1]["content"], 0) + 1
                dom[r["meta"]["domain"]] = dom.get(r["meta"]["domain"], 0) + 1
        stats["labels"][split] = {"labels": lab, "domains": dom}

    for split in ("train", "holdout"):
        (out / f"{split}.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for _, rs in parts[split] for r in rs),
            encoding="utf-8")
    (out / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
