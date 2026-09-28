# -*- coding: utf-8 -*-
"""候选 → 人工审核 → 已批准数据集版本(整改单 §7 / 契约 §2.6)。

职责边界(本轮只做"候选已准备"):
  * 只**读**候选队列(learning-candidates.jsonl),只收
    `review_state=approved` **且**来源/脱敏/家族切分完整的行;
  * 产出**不可变 manifest**:同一输入重复构建得到**同一 manifest hash**;
  * 只写 `status="candidates_ready"` —— **不训练、不构建训练卷、不发布数据集**;
    manifest 内任何字符串出现"已训练/已发布"口径即报错(assert_no_release_claim)。

硬拒规则(pending / rejected / duplicate / B 留存家族 / 字段不全 一律拒):
  * `review_state != approved`            → `review_state=<值>`
  * `exclusion_reason` 非空               → `<值>`(含 b_set_contamination)
  * 批内 `sample_id` 或 `(input_hash,intent_label,input_mode)` 重复
                                          → `duplicate_within_input`
  * 缺 `run_id`/`family_id`/`input_hash`/`sample_id`/`scrub_version`
                                          → `missing_*`
  * `scrub_version != 当前规则版本`        → `scrub_version_stale=<值>`
  * 命中 B 留存面(坐标 :8082 / 家族键含 8082 / 留存卷同族)→ `b_*`
  * 同族两桶(跨切分冲突)                  → `family_split_conflict:...`
  * 无 `split_assignment` 且无人工指派     → `missing_split_assignment`

两种模式:`strict=True`(默认)整批抛 `CandidateRejected`;
`strict=False` 走 `split_candidates()` **排除并逐条记账**,绝不静默混入。
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import sys
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = pathlib.Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "dataset"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from engine.learning_queue import (  # noqa: E402  只读用,不触发任何训练
    SCRUB_VERSION,
    b_sealed_family_ids,
    is_b_sealed,
)

SCHEMA_VERSION = 1
KIND = "approved_candidate_dataset"
STATUS_READY = "candidates_ready"
STATUS_NOTE = ("候选已准备:本产物只登记已批准候选与其家族切分/来源/脱敏版本,"
               "不构成任何训练动作,也不构成任何数据集发布。")
VALID_SPLITS = ("train", "holdout")
# 本轮禁写的口径(只扫字符串值,字段名/train_device 等不算)
FORBIDDEN_CLAIM_TOKENS = ("trained", "published", "已训练", "已发布")
DATASET_HASH_BASIS = ("sha256(每样本 sample_id|input_hash|family_id|split_assignment|"
                      "intent_label|input_mode|outcome,排序后逐行)")
# 计入 manifest hash 的字段(全部由输入决定;built_at 等时钟字段排除)
HASHED_FIELDS = ("schema_version", "kind", "status", "dataset_version",
                 "reviewer", "reviewed_at", "sample_ids", "run_ids", "flow_ids",
                 "scrub_versions", "family_split", "split_source", "counts",
                 "dataset_hash", "dataset_hash_basis", "train_device",
                 "train_device_source", "adapter_paths", "eval_paths",
                 "input_queue_paths", "b_seal", "rejected")
HASH_EXCLUDED_FIELDS = ("built_at", "manifest_hash", "hash_excludes", "status_note")


class CandidateRejected(Exception):
    """候选不合格:整批硬拒(不做隐式排除)。"""

    def __init__(self, defects: Sequence[Tuple[str, str]]):
        self.defects = list(defects)
        detail = "; ".join(f"{sid}:{why}" for sid, why in self.defects)
        super().__init__(f"{len(self.defects)} 条候选被硬拒 → {detail}")


class ManifestConflict(Exception):
    """同名 manifest 已存在且内容不同(不可变产物不原地改写)。"""


# ---------- 准入判定 ----------

def _split_candidate(row: dict, split_overrides: Optional[Dict[str, str]] = None):
    """→ (split 或 None, 来源标记)。人工指派优先于查表值。"""
    fam = row.get("family_id") or ""
    if split_overrides and fam in split_overrides:
        return split_overrides[fam], "human_override"
    return row.get("split_assignment"), "sealed_volume_lookup"


def candidate_defect(row, split_overrides: Optional[Dict[str, str]] = None
                     ) -> Optional[str]:
    """单行准入判定:合格返回 None,否则返回拒入理由字符串。"""
    if not isinstance(row, dict):
        return "not_a_record"
    if row.get("review_state") != "approved":
        return f"review_state={row.get('review_state')!r}"
    if row.get("exclusion_reason"):
        return f"exclusion_reason={row['exclusion_reason']}"
    for field, why in (("sample_id", "missing_sample_id"),
                       ("run_id", "missing_run_id"),
                       ("family_id", "missing_family_id"),
                       ("input_hash", "missing_input_hash")):
        if not row.get(field):
            return why
    sv = row.get("scrub_version")
    if not sv:
        return "missing_scrub_version"
    if sv != SCRUB_VERSION:
        return f"scrub_version_stale={sv}"
    why = is_b_sealed(row.get("family_id"), row.get("path") or "")
    if why:
        return why
    sp, _src = _split_candidate(row, split_overrides)
    if not sp:
        return "missing_split_assignment"
    if sp not in VALID_SPLITS:
        return f"invalid_split_assignment={sp!r}"
    return None


def split_candidates(rows: Iterable, split_overrides: Optional[Dict[str, str]] = None
                     ) -> Tuple[List[dict], List[Tuple[str, str]]]:
    """→ (admitted, rejected_accounting)。批内去重与同族同桶在此强制。"""
    admitted: List[dict] = []
    rejected: List[Tuple[str, str]] = []
    seen_ids: Dict[str, int] = {}
    seen_keys: Dict[tuple, str] = {}
    fam_split: Dict[str, str] = {}
    for row in rows:
        sid = (row or {}).get("sample_id") if isinstance(row, dict) else None
        sid = str(sid) if sid else "<no-sample_id>"
        why = candidate_defect(row, split_overrides)
        if why:
            rejected.append((sid, why))
            continue
        if sid in seen_ids:
            rejected.append((sid, "duplicate_within_input(sample_id)"))
            continue
        key = (row.get("input_hash"), row.get("intent_label"), row.get("input_mode"))
        if key in seen_keys:
            rejected.append((sid, f"duplicate_within_input(input_hash)={seen_keys[key]}"))
            continue
        sp, _src = _split_candidate(row, split_overrides)
        prev = fam_split.get(row["family_id"])
        if prev is not None and prev != sp:
            rejected.append((sid, f"family_split_conflict:{prev}!={sp}"))
            continue
        seen_ids[sid] = 1
        seen_keys[key] = sid
        fam_split[row["family_id"]] = sp
        admitted.append(row)
    return admitted, rejected


def load_queue_rows(paths) -> List[dict]:
    """读一个或多个候选队列 JSONL;坏行**不静默跳过**(抛 CandidateRejected)。"""
    if isinstance(paths, (str, pathlib.Path)):
        paths = [paths]
    rows: List[dict] = []
    bad: List[Tuple[str, str]] = []
    for p in paths:
        p = pathlib.Path(p)
        if not p.is_file():
            raise CandidateRejected([(str(p), "queue_missing")])
        with p.open(encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    bad.append((f"{p.name}#line{n}", "unparseable_line"))
    if bad:
        raise CandidateRejected(bad)
    return rows


# ---------- manifest ----------

def dataset_hash(admitted: Sequence[dict]) -> str:
    """已批准数据集的**内容**hash(排序后逐行,label/顺序无关)。"""
    keys = ("sample_id", "input_hash", "family_id", "split_assignment",
            "intent_label", "input_mode", "outcome")
    lines = sorted("|".join(str(r.get(k) or "") for k in keys) for r in admitted)
    return "sha256:" + hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _iter_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_strings(v)
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            yield from _iter_strings(v)


def assert_no_release_claim(manifest: dict) -> None:
    """本轮只允许"候选已准备":字符串值出现训练/发布口径即报错。"""
    if not isinstance(manifest, dict):
        raise TypeError("manifest 必须是 dict")
    if manifest.get("status") != STATUS_READY:
        raise ValueError(f"status 只能是 {STATUS_READY!r},"
                         f"实为 {manifest.get('status')!r}")
    if manifest.get("release") != {"trained": False, "published": False}:
        raise ValueError("release 必须显式写 {trained: false, published: false}")
    for s in _iter_strings(manifest):
        low = s.lower()
        for tok in FORBIDDEN_CLAIM_TOKENS:
            if tok.lower() in low:
                raise ValueError(f"manifest 出现训练/发布口径 {tok!r}: {s[:120]!r}")


def _rel(p) -> str:
    p = pathlib.Path(p)
    try:
        return str(p.resolve().relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(p)


def build_manifest(rows: Iterable, *, reviewer: str, reviewed_at: str,
                   dataset_version: str, train_device: str,
                   train_device_source: str = "",
                   adapter_paths: Sequence[str] = (),
                   eval_paths: Sequence[str] = (),
                   split_overrides: Optional[Dict[str, str]] = None,
                   input_queue_paths: Sequence[str] = (),
                   b_seal: Optional[dict] = None,
                   built_at: Optional[str] = None,
                   strict: bool = True) -> dict:
    """候选行 → 不可变 manifest 字典(同一输入 → 同一 manifest_hash)。

    strict=True(默认):有任一不合格候选即抛 CandidateRejected;
    strict=False:不合格者进 `rejected` 账,只登记合格者。
    """
    if not reviewer or not reviewed_at or not dataset_version or not train_device:
        raise ValueError("reviewer/reviewed_at/dataset_version/train_device 必填")
    admitted, rejected = split_candidates(rows, split_overrides)
    if strict and rejected:
        raise CandidateRejected(rejected)

    family_split: Dict[str, str] = {}
    split_source: Dict[str, str] = {}
    for r in admitted:
        sp, src = _split_candidate(r, split_overrides)
        family_split[r["family_id"]] = sp
        split_source[r["family_id"]] = src

    seal = b_seal if b_seal is not None else b_sealed_family_ids()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "status": STATUS_READY,
        "status_note": STATUS_NOTE,
        "release": {"trained": False, "published": False},
        "dataset_version": dataset_version,
        "built_at": built_at,
        "reviewer": reviewer,
        "reviewed_at": reviewed_at,
        "sample_ids": sorted(r["sample_id"] for r in admitted),
        "run_ids": sorted({r["run_id"] for r in admitted}),
        "flow_ids": sorted({r["flow_id"] for r in admitted if r.get("flow_id")}),
        "scrub_versions": sorted({r["scrub_version"] for r in admitted}),
        "family_split": dict(sorted(family_split.items())),
        "split_source": dict(sorted(split_source.items())),
        "counts": {
            "samples": len(admitted),
            "families": len(family_split),
            "by_split_samples": dict(sorted(collections.Counter(
                _split_candidate(r, split_overrides)[0]
                for r in admitted).items())),
            "by_split_families": dict(sorted(collections.Counter(
                family_split.values()).items())),
        },
        "dataset_hash": dataset_hash(admitted),
        "dataset_hash_basis": DATASET_HASH_BASIS,
        "train_device": train_device,
        "train_device_source": train_device_source,
        "adapter_paths": sorted(adapter_paths),
        "eval_paths": sorted(eval_paths),
        "input_queue_paths": sorted(_rel(p) for p in input_queue_paths),
        "b_seal": {"loaded_families": seal.get("loaded_families", 0),
                   "sources": [dict(s) for s in seal.get("sources", [])],
                   "rejected_rows": [{"sample_id": sid, "reason": why}
                                     for sid, why in rejected
                                     if why.startswith("b_")]},
        "rejected": [{"sample_id": sid, "reason": why} for sid, why in rejected],
        "hash_excludes": list(HASH_EXCLUDED_FIELDS),
    }
    manifest["manifest_hash"] = manifest_hash(manifest)
    assert_no_release_claim(manifest)
    return manifest


def manifest_hash(manifest: dict) -> str:
    """manifest hash:对 HASHED_FIELDS 规范化 JSON 求 sha256(时钟字段不计)。"""
    core = {k: manifest.get(k) for k in HASHED_FIELDS}
    payload = json.dumps(core, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_from_queues(queue_paths, **kw) -> dict:
    """队列文件 → manifest(队列路径一并登记为来源)。"""
    if isinstance(queue_paths, (str, pathlib.Path)):
        queue_paths = [queue_paths]
    queue_paths = [str(p) for p in queue_paths]
    rows = load_queue_rows(queue_paths)
    kw.setdefault("input_queue_paths", queue_paths)
    return build_manifest(rows, **kw)


def write_manifest(manifest: dict, path) -> pathlib.Path:
    """落盘不可变 manifest:同名同内容幂等;同名不同内容 → ManifestConflict。"""
    path = pathlib.Path(path)
    assert_no_release_claim(manifest)
    text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.is_file():
        if path.read_text(encoding="utf-8") == text:
            return path
        raise ManifestConflict(
            f"{path} 已存在且与本次构建不同 —— 不可变产物不原地改写;"
            "如需新版本请换 dataset_version 或换文件名")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    from engine.demo_contracts import atomic_replace
    atomic_replace(tmp, path)
    return path


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="构建已批准候选数据集 manifest")
    ap.add_argument("--queue", action="append", required=True,
                    help="候选队列 JSONL(可多次)")
    ap.add_argument("--reviewer", required=True)
    ap.add_argument("--reviewed-at", required=True, help="ISO8601,人工审核时间")
    ap.add_argument("--dataset-version", required=True)
    ap.add_argument("--train-device", required=True)
    ap.add_argument("--train-device-source", default="")
    ap.add_argument("--adapter", action="append", default=[])
    ap.add_argument("--eval", action="append", default=[])
    ap.add_argument("--split-override", action="append", default=[],
                    help="未封卷家族的人工切分指派 FAMILY=train|holdout")
    ap.add_argument("--out", default=None, help="落盘路径(不传则只打印)")
    ap.add_argument("--non-strict", action="store_true",
                    help="不整批硬拒,改为排除并记账")
    args = ap.parse_args(argv)
    overrides = {}
    for item in args.split_override:
        fam, _, bucket = item.rpartition("=")
        overrides[fam] = bucket
    m = build_from_queues(args.queue, reviewer=args.reviewer,
                          reviewed_at=args.reviewed_at,
                          dataset_version=args.dataset_version,
                          train_device=args.train_device,
                          train_device_source=args.train_device_source,
                          adapter_paths=args.adapter, eval_paths=args.eval,
                          split_overrides=overrides or None,
                          strict=not args.non_strict)
    if args.out:
        write_manifest(m, args.out)
    print(json.dumps(m, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
