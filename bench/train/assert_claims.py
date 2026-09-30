"""claims 断言器 —— 上墙数字的闸门（评审强制第 0 步）。

用法:
  python bench/train/assert_claims.py            # 逐条断言，任一不符退出码 1
  python bench/train/assert_claims.py --json     # 机器可读输出（供 deck/EVIDENCE 生成引用）

规矩：对外材料（README/EVIDENCE.md/JUDGES.md/deck/字幕）里的每个数字，都必须能
指回 `claims.yaml` 的一条 claim_id；写不出条目的句子就不许出现。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


def _get(doc, selector: str):
    cur = doc
    for part in re.findall(r"[^.\[]+|\[\d+\]", selector):
        if part.startswith("["):
            cur = cur[int(part[1:-1])]
        else:
            cur = cur[part]
    return cur


def _eq(actual, expected) -> bool:
    if isinstance(expected, float) and isinstance(actual, (int, float)):
        dec = max(len(str(expected).split(".")[-1]), 1)
        return round(float(actual), dec) == round(float(expected), dec)
    return actual == expected


# ---------------- 现场计算的条目（不写死，从原始产物重算） ----------------
def _lines(p):
    return [l for l in (ROOT / p).read_text(encoding="utf-8").splitlines() if l.strip()]


def wave5_exchanges():
    return len(_lines("dataset/raw/gen_freeflows.jsonl"))


def wave5_hits():
    return sum(1 for r in (json.loads(l) for l in _lines("dataset/raw/gen_freeflows.jsonl"))
               if r.get("res_markers"))


def wave5_rejects():
    return len(_lines("dataset/raw/gen_freeflows_rejects.jsonl"))


def wave5_policy_rejects():
    return sum(1 for r in (json.loads(l) for l in _lines("dataset/raw/gen_freeflows_rejects.jsonl"))
               if "policy" in str(r.get("why_rejected", "")))


def jail_exec_evidence():
    """webshell 真落盘 + 真执行，且发射前笼内 uploads 为空（评审 P1-2 后的干净账）。"""
    d = json.loads((ROOT / "bench/results/jail_smoke/jail_smoke.json").read_text(encoding="utf-8"))
    pre, post = d["pre_state"], d["post_state"]
    ok = (not pre["uploads"].strip()) and "shell_jail.php" in post["uploads"] \
        and "JAIL_RCE_OK" in str(post.get("exec_probe", ""))
    return 1 if ok else 0


def jail_escape_denied():
    d = json.loads((ROOT / "bench/results/jail_smoke/jail_smoke.json").read_text(encoding="utf-8"))
    row = [s for s in d["shots"] if s["shot"] == "escape_denied_root"]
    return 1 if (row and "REJECTED" in row[0]["jail_verdict"] and "fired" not in row[0]) else 0


def _historical_validation_count(field):
    data = json.loads((ROOT / "VALIDATION.json").read_text(encoding="utf-8"))
    text = data["public_suite"]["stdout"]
    match = re.search(r"(\d+) subtests passed", text) if field == "subtests" else re.search(r"(\d+) passed", text)
    if not match:
        raise ValueError("historical validation output lacks " + field)
    return int(match.group(1))


def _historical_functional_count():
    data = json.loads((ROOT / "evidence/main-run-run-20260927-140612-399b/evidence-receipt.json").read_text(encoding="utf-8"))
    tail = data["functional_tests"]["value"]["tail"]
    match = re.search(r"(\d+) passed", tail)
    if data["functional_tests"]["value"]["status"] != "passed" or not match:
        raise ValueError("functional test receipt is incomplete")
    return int(match.group(1))


def _historical_skill_video_seconds():
    data = json.loads((ROOT / "evidence/skill-run-run-20260928-155647-3290/final-video-manifest.json").read_text(encoding="utf-8"))
    return round(data["segments"][1]["seconds"])


def _historical_skill_proof():
    data = json.loads((ROOT / "evidence/skill-run-run-20260928-155647-3290/capture.json").read_text(encoding="utf-8"))
    proof = data["result"]["verdict"]["proof"]
    if not proof or not all(value is True for value in proof.values()):
        raise ValueError("skill receipt proof is incomplete")
    return len(proof)


PRIVATE_COMPUTED = frozenset({
    "wave5_exchanges", "wave5_hits", "wave5_rejects", "wave5_policy_rejects",
    "jail_exec_evidence", "jail_escape_denied"})


COMPUTED = {
    "r4_tests": lambda: _historical_validation_count("tests"),
    "r4_subtests": lambda: _historical_validation_count("subtests"),
    "historical_functional_count": _historical_functional_count,
    "historical_skill_proof": _historical_skill_proof,
    "historical_skill_video_seconds": _historical_skill_video_seconds,
    "wave5_exchanges": wave5_exchanges,
    "wave5_hits": wave5_hits,
    "wave5_rejects": wave5_rejects,
    "wave5_policy_rejects": wave5_policy_rejects,
    "jail_exec_evidence": jail_exec_evidence,
    "jail_escape_denied": jail_escape_denied,
}


def pytest_count() -> int:
    r = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q"],
                       cwd=ROOT, capture_output=True, text=True, timeout=600)
    m = re.search(r"(\d+) passed", r.stdout)
    return int(m.group(1)) if m else 0


RUNTIME = {"pytest_count": pytest_count}


def check_tie(tie) -> tuple:
    doc = json.loads((ROOT / tie["file"]).read_text(encoding="utf-8"))
    got = _get(doc, tie["selector"])
    return tie["selector"], got, _eq(got, tie["expected"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--public", action="store_true",
                    help="check shipped JSON/linecount claims; explicitly list private computed claims as not evaluated")
    a = ap.parse_args()
    claims = yaml.safe_load((ROOT / "claims.yaml").read_text(encoding="utf-8"))
    rows, bad, excluded = [], [], []
    for c in claims:
        if c.get("retired_from_submission"):
            continue
        cid, kind = c["claim_id"], c["kind"]
        if a.public and (kind == "runtime" or
                         (kind == "computed" and c.get("fn") in PRIVATE_COMPUTED)):
            excluded.append({"claim_id": cid, "kind": kind,
                             "status": "not_evaluated", "reason": "requires_private_raw_or_runtime_evidence"})
            continue
        try:
            if kind == "json":
                doc = json.loads((ROOT / c["file"]).read_text(encoding="utf-8"))
                got = _get(doc, c["selector"])
                ok = _eq(got, c["expected"])
                if ok and c.get("tie"):
                    s2, g2, ok2 = check_tie(c["tie"])
                    ok = ok2
                    got = f"{got} + {s2}={g2}"
            elif kind == "linecount":
                ls = _lines(c["file"])
                got = len(ls)
                ok = got == c["expected"]
                if ok and c.get("equals"):
                    off = [l for l in ls if l.strip() != c["equals"].strip()]   # 缩进不参与同文判定
                    ok = not off
                    got = f"{got} 行（异文 {len(off)}）"
            elif kind == "computed":
                got = COMPUTED[c["fn"]]()
                ok = got == c["expected"]
            elif kind == "runtime":
                import os
                if os.environ.get("AEGIS_SKIP_RUNTIME_CLAIMS"):
                    # 在 pytest 内部跑：递归会自杀，改由 CLI 单独把这道闸
                    got, ok = "skipped(nested)", True
                else:
                    got = RUNTIME[c["fn"]]()
                    ok = got >= c["floor"]
            else:
                raise RuntimeError(f"未知 kind {kind}")
        except Exception as e:  # noqa: BLE001 —— 断言器必须把取不到值也算失败
            got, ok = f"ERROR {type(e).__name__}: {e}", False
        rows.append({"claim_id": cid, "clause": c["clause"], "actual": got,
                     "expected": c.get("expected", c.get("floor")), "ok": bool(ok)})
        if not ok:
            bad.append(cid)
    if a.json:
        print(json.dumps({"pass": not bad, "scope": "public_shipped_evidence" if a.public else "full",
                          "claims": rows, "not_evaluated": excluded}, indent=1, ensure_ascii=False,
                         default=str))
    else:
        for r in rows:
            print(f"  [{'OK ' if r['ok'] else 'RED'}] {r['claim_id']:24s} "
                  f"实际={r['actual']} 期望={r['expected']}")
        if excluded:
            print("NOT EVALUATED: " + ", ".join(r["claim_id"] for r in excluded))
        print(f"\n{len(rows) - len(bad)}/{len(rows)} 条对上仓内出处"
              + (f"；红：{bad}" if bad else "，当前范围通过"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
