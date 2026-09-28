"""技能蒸馏器（T2）—— 红蓝经验 → 候选技能 → 人工过闸（SPEC 愿景：经验固化成 skill）。

三条纪律：
1. 只读黑板（flow/alerts/findings + plugins 现状），输出只进 workspace/candidates/——
   绝不自动合并进 plugins/（自动改写自己的检测规则 = 自绕过风险，人工过闸是刻意设计）。
2. 蓝方双信号：真漏检（红命中∧蓝零告警）+ 低置信升格（规则只敢给 0.3 的攻击，人工确认后升格）。
3. 候选必须可验证：verify 子命令跑双门禁（良性零命中 + 原漏检命中），两道都过才值得人看。

用法:
  python -m engine.learn blue  --workspace <dir> [--out <dir>]
  python -m engine.learn red   --workspace <dir> [--out <dir>]
  python -m engine.learn verify --candidates <yaml> --flow <flow.jsonl> [--benign benign.jsonl]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import time
import urllib.parse
from typing import Dict, List, Tuple

from . import detect

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGINS = ROOT / "plugins"


def _load_flow(p: pathlib.Path) -> List[dict]:
    out = []
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        e["_idx"] = i
        out.append(e)
    return out


def _hits(flow: List[dict]) -> List[dict]:
    return [e for e in flow
            if e.get("label") == "attack" and e.get("marker_hits")]


def _alerted_refs(alerts: List[dict]) -> Dict[int, dict]:
    out = {}
    for a in alerts:
        try:
            out[int(a["flow_ref"])] = a
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _class_of(flow: dict, findings: List[dict], hit_marker: str) -> str:
    """命中流的类别归属：优先按 marker 反查插件，退化到 finding 关联。"""
    hit = (flow.get("marker_hits") or [hit_marker])[0] if hit_marker else ""
    for p in PLUGINS.glob("*/plugin.yaml"):
        try:
            import yaml
            meta = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        markers = (meta.get("detector") or {}).get("markers") or []
        if hit and hit in markers:
            return meta.get("class", "?")
    for f in findings:
        if f.get("endpoint") and f["endpoint"].split("?")[0] in (flow.get("path") or ""):
            return f.get("class", "?")
    return flow.get("class") or "?"


def _decode(s: str) -> str:
    try:
        return urllib.parse.unquote(s or "")
    except Exception:  # noqa: BLE001
        return s or ""


def _generalize(query_or_body: str) -> str:
    """从具体值推保守正则：转义字面量 + 数字/路径段泛化。人工过闸可再收紧。"""
    s = _decode(query_or_body)
    s = re.sub(r"\d+", "\\\\d+", s)
    s = re.sub(re.escape("'%20"), "('|%20)", s)
    return s[:120]


# ---------- learn blue ----------
def blue(workspace: pathlib.Path, out_dir: pathlib.Path) -> List[dict]:
    flow = _load_flow(workspace / "flow.jsonl")
    alerts = json.loads((workspace / "alerts.json").read_text(encoding="utf-8")) \
        if (workspace / "alerts.json").is_file() else []
    findings = json.loads((workspace / "findings.json").read_text(encoding="utf-8"))["findings"]
    refs = _alerted_refs(alerts)
    by_cls = {}
    for f in findings:
        by_cls.setdefault(f["class"], []).append(f)

    misses, upgrades = [], []
    for e in _hits(flow):
        ref = refs.get(e["_idx"])
        if ref is None:
            misses.append(e)
        elif float(ref.get("confidence", 1)) < 0.5:
            upgrades.append((e, ref))

    cands = []
    n = 0
    for e in misses:
        n += 1
        cls = _class_of(e, findings, (e.get("marker_hits") or [""])[0])
        cands.append({
            "id": f"BLUE-C-{n:03d}", "class": cls, "origin": "missed-hit",
            "evidence": f"flow _idx={e['_idx']} marker={e.get('marker_hits')} status={e.get('status')} phase={e.get('phase', '?')}",
            "where": "request_query" if "?" in (e.get("path") or "") else
                     ("request_body" if e.get("body") else "request_path"),
            "pattern": _generalize((e.get("path", "").split("?", 1) + [""])[1] or e.get("body", "")),
            "confidence": 0.85,
        })
    for e, ref in upgrades:
        n += 1
        cands.append({
            "id": f"BLUE-C-{n:03d}", "class": ref.get("class", "?"), "origin": "lowconf-upgrade",
            "evidence": f"flow _idx={e['_idx']} 旧规则={ref.get('reasoning')} 旧置信={ref.get('confidence')}",
            "where": "request_query" if "?" in (e.get("path") or "") else
                     ("request_body" if e.get("body") else "request_path"),
            "pattern": _generalize(_decode(e.get("path", "").split("?", 1)[-1]) or e.get("body", "")),
            "confidence": 0.85,
        })

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"blue-{time.strftime('%Y%m%d_%H%M%S')}.yaml"
    lines = [f"# 蓝方候选签名（蒸馏器产出，人工过闸后并入 plugins/<class>/signatures/rules.yaml）",
             f"# 生成时间 {time.strftime('%c')} · 漏检 {len(misses)} + 低置信升格 {len(upgrades)}"]
    for c in cands:
        lines += [f"- id: {c['id']}", f"  class: {c['class']}", f"  origin: {c['origin']}",
                  f"  evidence: {c['evidence']}", f"  where: {c['where']}",
                  f"  pattern: {json.dumps(c['pattern'])}", f"  confidence: {c['confidence']}", ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    return cands


# ---------- learn red ----------
def red(workspace: pathlib.Path, out_dir: pathlib.Path) -> List[dict]:
    flow = _load_flow(workspace / "flow.jsonl")
    fam: Dict[Tuple[str, str, str], List[dict]] = {}
    for e in _hits(flow):
        path = re.sub(r"\d+", "N", (e.get("path") or "").split("?")[0])
        key = (e.get("method", ""), path, (e.get("marker_hits") or ["?"])[0])
        fam.setdefault(key, []).append(e)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"red-{time.strftime('%Y%m%d_%H%M%S')}.md"
    lines = ["# 红方命中样本候选（家族折叠 · 人工过闸后并入 plugins/<class>/payloads/attack/）", ""]
    fams = []
    for (method, path, marker), es in sorted(fam.items(), key=lambda kv: -len(kv[1])):
        example = es[0]
        fams.append({
            "class": _class_of(example, [], marker), "method": method, "path": path,
            "count": len(es), "marker": marker,
            "example": (example.get("body") or example.get("path") or "")[:160],
        })
        lines += [f"## {marker} × {len(es)}", f"- {method} {path}",
                  f"- 样例体：`{example.get('body', '')[:120]}`", ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    return fams


# ---------- 候选双门禁 ----------
def verify(cand_yaml: pathlib.Path, flow_path: pathlib.Path,
           benign_path: pathlib.Path = None) -> dict:
    """候选规则双门禁：①良性零命中 ②原漏检流命中。任一不过 = 打回。"""
    import yaml as _yaml
    text = cand_yaml.read_text(encoding="utf-8")
    rules = []
    for m in re.finditer(r"- id: (\S+)\n  class: (\S+)\n[^\n]*\n  evidence:[^\n]*\n  where: (\S+)\n  pattern: (.+)", text):
        pat = m.group(4).strip()
        try:
            pat = json.loads(pat) if pat.startswith('"') else pat
        except json.JSONDecodeError:
            pass
        rules.append({"id": m.group(1), "class": m.group(2), "where": m.group(3),
                      "pattern": pat.strip('"')})
    flow = _load_flow(flow_path)
    attack = [e for e in flow if e.get("label") == "attack"]
    benign = [e for e in _load_flow(benign_path)] if benign_path else []
    report = {"rules": len(rules), "results": []}
    for rule in rules:
        pat = re.compile(rule["pattern"]) if rule["pattern"] else None
        hit_atk = sum(1 for e in attack
                      if pat and pat.search(detect._flow_text(e, rule["where"])))
        hit_ben = sum(1 for e in benign
                      if pat and pat.search(detect._flow_text(e, rule["where"])))
        ok = hit_atk > 0 and (not benign or hit_ben == 0)
        report["results"].append({"id": rule["id"], "attack_hits": hit_atk,
                                  "benign_hits": hit_ben, "gate": "PASS" if ok else "REJECT"})
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["blue", "red", "verify"])
    ap.add_argument("--workspace", default=str(ROOT / "workspace"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--candidates", default=None)
    ap.add_argument("--flow", default=None)
    ap.add_argument("--benign", default=None)
    a = ap.parse_args()
    out_dir = pathlib.Path(a.out) if a.out else pathlib.Path(a.workspace) / "candidates"
    if a.mode == "blue":
        cands = blue(pathlib.Path(a.workspace), out_dir)
        print(f"[learn blue] {len(cands)} 条候选 → {out_dir}")
        for c in cands:
            print(f"  {c['id']} {c['class']} ({c['origin']}) {c['evidence'][:60]}")
    elif a.mode == "red":
        fams = red(pathlib.Path(a.workspace), out_dir)
        print(f"[learn red] {len(fams)} 个命中家族 → {out_dir}")
    else:
        rep = verify(pathlib.Path(a.candidates), pathlib.Path(a.flow),
                     pathlib.Path(a.benign) if a.benign else None)
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0 if all(r["gate"] == "PASS" for r in rep["results"]) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
