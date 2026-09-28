"""marathon —— 多轮对抗收敛演示引擎（D6）。

全类别 × N 轮：每轮逐类 attack（新会话）→ 有命中则 detect/patch(keep)/verify，
轮末 snapshot_round(r)；已修补类次轮 attack 零命中 = 收敛。补丁跨轮累积在 canonical
（会话级备份，结束时恢复漏洞态——演示可重复）。收敛曲线数据 = rounds/*.json。

用法（Spark）:
  ~/venvs/aegis/bin/python -m engine.marathon --base http://127.0.0.1:8081 \
      --instance a --runtime ~/aegis/target/edu-lite/runtime/a --rounds 2 [--keep-patch]
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import time
from typing import List

import yaml

from . import detect, patcher, replay, verify
from .blackboard import Blackboard
from .models import Finding, FindingStatus, Severity
from .runloop import CANONICAL_APP, ROOT, crash_restore, next_finding_id
from .target_profile import active as _active_profile

_PROFILE = _active_profile()

PLUGINS = ROOT / "plugins"
ALL_CLASSES = ["sqli", "upload_bypass", "rce_deser", "xss_stored", "lfi", "idor",
               "ssrf", "brute_no_lock"]


def _deploy(php: str, instance: str):
    subprocess.run(["bash", str(ROOT / "target/edu-lite/deploy.sh"), instance],
                   env={**os.environ, "PHP_BIN": php}, check=True,
                   stdout=subprocess.DEVNULL)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--instance", required=True, choices=["a", "b"])
    ap.add_argument("--classes", default=",".join(ALL_CLASSES))
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--runtime", required=True)
    ap.add_argument("--workspace", default=str(ROOT / "workspace"))
    ap.add_argument("--php", default=os.path.expanduser("~/tools/php/php"))
    ap.add_argument("--llm", choices=["qwen", "step", "off"], default="off")
    ap.add_argument("--red-agent", choices=["off", "qwen", "step"], default="off",
                    help="R2 起红方 agent 选招变异（SPEC §9 归位；失败静默回退确定性重放）")
    ap.add_argument("--keep-patch", action="store_true")
    a = ap.parse_args()
    classes = [c for c in a.classes.split(",") if c]

    t0 = time.time()
    # 每次 marathon 自洽起跑：旧 workspace 归档（飞轮纪律：只搬不删）——
    # 防 flow 跨 run 追加导致告警指向不存在的 finding（D6 评审：双视角叙事的数据层断裂）。
    # 契约 §3（T0-contract-freeze F2）：归档仅限旧入口自己的工作内容，
    # workspace/runs/ 是新单案例演示（engine.demo_case）的持久化目录，原地不动。
    ws_dir = pathlib.Path(a.workspace)
    if ws_dir.exists() and any(ws_dir.iterdir()):
        ts = time.strftime("%Y%m%d_%H%M%S")
        archive = ROOT / f"dataset/raw/runloop_archive/{ts}_marathon_pre"
        archive.mkdir(parents=True, exist_ok=True)
        for item in ws_dir.iterdir():
            if item.name == "runs":
                continue  # 新单案例演示的 run 目录不随旧入口归档
            shutil.move(str(item), str(archive / item.name))
        print(f"[archive] 旧 workspace（除 runs/） -> {archive}")

    # 会话级备份 + keep 标志（crash_restore 依据）
    backup_dir = pathlib.Path(a.workspace) / ".canonical-backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_app = backup_dir / "app.php"
    if not CANONICAL_APP.read_text(encoding="utf-8").count("[patched") and not backup_app.exists():
        shutil.copy2(CANONICAL_APP, backup_app)
        print(f"[backup] pristine canonical -> {backup_app}")
    # 评审 P1：补丁态遗留 + 无备份 = 假收敛温床（runloop 有同款守卫，marathon 补齐）
    if CANONICAL_APP.read_text(encoding="utf-8").count("[patched") and not backup_app.exists():
        print("[FATAL] canonical 处于补丁态且本 workspace 无纯净备份——拒绝起跑"
              "（防 R1 零命中假收敛）：先经 demo.sh [2/4] 重置或恢复源文件")
        return 2
    keep_flag = pathlib.Path(a.workspace) / ".keep_patch"
    if a.keep_patch:
        keep_flag.write_text("1")
    else:
        keep_flag.unlink(missing_ok=True)

    bb = Blackboard(pathlib.Path(a.workspace))
    flow_file = (bb.workspace / "flow.jsonl").open("a", encoding="utf-8")
    red_ledger: dict = {}

    def record(phase: str, extra: dict = None):
        def hook(ex):
            ex.update({"ts": time.time(), "phase": phase, "label": "attack"})
            if extra:
                ex.update(extra)
            flow_file.write(json.dumps(ex, ensure_ascii=False) + "\n")
            flow_file.flush()
        return hook

    def defense_note(cls: str) -> str:
        """给红方 agent 的防御情报：该类 verified 补丁的头部新增行。"""
        fs = [f for f in bb.all() if f.class_ == cls]
        if not fs:
            return "无该类已确认漏洞，靶未加固"
        f = max(fs, key=lambda x: (x.history[-1].round if x.history else 0))
        pf = bb.patches_dir / f"{f.id}.patch"
        if f.status is FindingStatus.verified and pf.is_file():
            adds = [ln[:80] for ln in pf.read_text(
                encoding="utf-8").splitlines() if ln.startswith("+") and not ln.startswith("+++")][:3]
            return "已打补丁并验证；补丁新增代码头部: " + " | ".join(adds)
        if f.status in (FindingStatus.accepted, FindingStatus.regressed):
            return "补丁失败/被驳回——防御实际缺位，直攻即可"
        return "补丁进行中，防御状态未知"

    # P1-2：攻击前基线快照（round 0）——收敛曲线"8→0"的起点，没有它红线从第一点就是平的
    bb.snapshot_round(0, counts_override={"open": len(classes)})
    print(f"[baseline] round_000: open={len(classes)}（战前可利用面=插件清单）")

    try:
        for rnd in range(1, a.rounds + 1):
            new_hits = 0
            for cls in classes:
                plugin_dir = _PROFILE.plugin_dir(cls, PLUGINS)  # T11/S1c: 目标 overlay 优先
                meta = yaml.safe_load((plugin_dir / "plugin.yaml").read_text(encoding="utf-8"))
                markers = meta["detector"]["markers"]
                # regressed 的重新打开进入本轮循环（补丁重试预算语义）
                for f in bb.by_status(FindingStatus.regressed):
                    if f.class_ == cls:
                        bb.transition(f.id, FindingStatus.open, rnd, detail="marathon reopen",
                                      actor="secaudit-run")
                already = [f for f in bb.all() if f.class_ == cls
                           and f.status is FindingStatus.verified]
                ctx = replay.make_ctx(a.base, a.instance,
                                      replay.admin_session(a.base, a.instance))
                parents = [(pf.name, pf.read_text(encoding="utf-8"))
                           for pf in sorted((plugin_dir / "payloads" / "attack").glob("*.txt"))]
                hits = []
                variants = []
                if rnd > 1 and a.red_agent != "off":
                    from . import attacker
                    variants = attacker.plan(cls, parents, defense_note(cls),
                                             provider=a.red_agent, base=a.base)
                if variants:
                    src = {v["source"] for v in variants}
                    red_ledger[f"R{rnd}/{cls}"] = {
                        "n": len(variants), "source": "llm" if "llm" in src else "fallback",
                        "moves": [f'{v["transform_id"]}:{v["rationale"][:48]}' for v in variants[:6]]}
                    for v in variants:
                        replay.check_payload_policy(v["text"])   # 与母弹同一治理闸
                        meta = {"red_agent": {"parent": v["parent"],
                                              "transform_id": v["transform_id"],
                                              "rationale": v["rationale"],
                                              "source": v["source"]}}
                        res = replay.replay_payload(a.base, v["text"],
                                                    f'{v["parent"]}·{v["transform_id"]}',
                                                    ctx, markers,
                                                    on_exchange=record(f"R{rnd}-attack", meta))
                        if res.markers_hit:
                            hits.append(f'{v["parent"]}[{v["transform_id"]}]')
                    if hits:
                        print(f"[R{rnd}] {cls}: 红方 agent {len(variants)} 发变体，"
                              f"绕过 {len(hits)} 发 ← {hits[:3]}")
                else:
                    for pname, req in parents:
                        replay.check_payload_policy(req)
                        res = replay.replay_payload(a.base, req, pname, ctx, markers,
                                                    on_exchange=record(f"R{rnd}-attack"))
                        if res.markers_hit:
                            hits.append(pname)
                if not hits:
                    print(f"[R{rnd}] {cls}: 零命中"
                          f"{'（已收敛，补丁在防）' if already else '（漏洞不在场？）'}")
                    continue
                new_hits += 1
                fid = next_finding_id(bb)
                bb.add_finding(Finding(
                    id=fid, class_=cls, endpoint=meta.get("endpoint", ""),
                    severity=Severity(meta.get("severity", "high")),
                    evidence={"replay_ref": hits[0], "marker": "marathon",
                              "marker_hit": True}), round_=rnd)
                bb.audit("secaudit-attack", "round.hit", round=rnd, cls=cls, hits=hits)
                # detect（全量 flow 扫描，链接到本类全部 findings）
                links = {f.class_: f.id for f in bb.all()}
                alerts = detect.link_findings(detect.scan_flow(bb.workspace / "flow.jsonl"), links)
                (bb.workspace / "alerts.json").write_text(
                    json.dumps(alerts, ensure_ascii=False, indent=2), encoding="utf-8")
                # patch
                llm_fb = None
                if a.llm != "off":
                    from . import llm as llm_mod
                    os.environ["AEGIS_MONOLOG"] = str(pathlib.Path(a.workspace) / "llm-monolog.jsonl")
                    llm_fb = lambda src, t, r: llm_mod.patch_adapt(src, t, r, provider=a.llm)
                try:
                    pre_src = CANONICAL_APP.read_text(encoding="utf-8")  # 本类补丁前快照（审查基线）
                    pr = patcher.apply_template(
                        CANONICAL_APP, plugin_dir / "patch-template" / "fix.patch.tpl",
                        llm_fallback=llm_fb)
                    lint = subprocess.run([a.php, "-l", str(CANONICAL_APP)],
                                          capture_output=True, text=True,
                                          encoding="utf-8", errors="replace")
                    if lint.returncode != 0:
                        raise patcher.PatchError(f"php -l: {(lint.stdout or '').strip()[:150]}")
                    if pr.modes and "llm" in pr.modes:
                        from . import patch_review
                        targets = [t for t, _ in patcher.load_template(
                            plugin_dir / "patch-template" / "fix.patch.tpl")]
                        ok, reasons, _ = patch_review.review(
                            pre_src, CANONICAL_APP.read_text(encoding="utf-8"), targets,
                            provider="step")
                        if not ok:
                            raise patcher.PatchError("review: " + "; ".join(reasons[:2]))
                    (bb.patches_dir / f"{fid}.patch").write_text(pr.diff, encoding="utf-8")
                    bb.transition(fid, FindingStatus.patched, rnd, actor="secaudit-patch")
                    _deploy(a.php, a.instance)
                except patcher.PatchError as e:
                    print(f"[R{rnd}] {cls}: patch FAIL {str(e)[:120]}")
                    bb.transition(fid, FindingStatus.accepted, rnd, detail=str(e)[:150],
                                  actor="secaudit-patch")
                    continue
                # verify
                g = verify.gate(a.base, a.instance, plugin_dir, markers,
                                on_exchange=record(f"R{rnd}-verify"))
                if g["passed"]:
                    bb.transition(fid, FindingStatus.verified, rnd, actor="secaudit-verify")
                    print(f"[R{rnd}] {cls}: {fid} verified ({g['replay_stat']})")
                else:
                    bb.transition(fid, FindingStatus.regressed, rnd, actor="secaudit-verify")
                    print(f"[R{rnd}] {cls}: {fid} regressed "
                          f"(blocked={g['replay_blocked']} tests={g['tests_passed']})")
            snap = bb.snapshot_round(rnd)
            counts = bb.counts()
            print(f"[R{rnd}] snapshot={snap.name} counts={counts} new_hits={new_hits}")
            all_verified = all(
                any(f.class_ == c and f.status is FindingStatus.verified for f in bb.all())
                for c in classes)
            if all_verified and new_hits == 0:
                print(f"[converged] 全部类别已验证且本轮零新发现（R{rnd}）")
                break
    finally:
        flow_file.close()
        if red_ledger:
            (pathlib.Path(a.workspace) / "redagent_ledger.json").write_text(
                json.dumps(red_ledger, ensure_ascii=False, indent=1), encoding="utf-8")
        if not a.keep_patch:
            crash_restore(a.workspace, a.php, a.instance, reason="会话收尾")

    print(json.dumps({"result": "MARATHON_DONE", "wall_s": round(time.time() - t0, 1),
                      "counts": bb.counts(), "rounds_dir": str(bb.rounds_dir)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    # 契约 §3:旧入口(demo.sh → marathon)也必须持同一把靶场锁,
    # 否则会在新入口 attack/verify 期间重部署靶场、污染证据(T7 复核 P0)。
    from engine.run_lock import locked_run

    raise SystemExit(locked_run(main, label="marathon"))
