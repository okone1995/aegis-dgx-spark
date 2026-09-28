"""secaudit-run 最小编排 —— D3 分水岭：单类漏洞全闭环。

attack → detect → patch → verify → re-attack → 收敛判定，
全程黑板留痕（findings 状态机 + audit-log + 轮次快照 + flow.jsonl 飞轮埋点）。

D3 评审修正（P0/P1）：
- re-attack 重新认证（补丁重部署已清空 sessions，旧 ctx 的"清零"是空证据）
- 三个发射阶段（attack/verify/re-attack）全部前置 payload policy
- finding id 按黑板现存最大 id 递增（同一 workspace 可多次运行）
- 规范源码会话级备份/恢复（--keep-patch 保留补丁，默认会话末恢复漏洞态并重部署）

用法（Spark）:
  ~/venvs/aegis/bin/python -m engine.runloop --base http://127.0.0.1:8081 \
      --instance a --class sqli --runtime ~/aegis/target/edu-lite/runtime/a
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import time
from typing import List, Optional

import yaml

from . import detect, patcher, replay, verify
from .blackboard import Blackboard
from .models import Finding, FindingStatus, Severity
from .scope import Scope, Target

from .target_profile import active as _active_profile

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGINS = ROOT / "plugins"
_PROFILE = _active_profile()
# T11/S1: 目标从 profile 读取（默认 edu-lite，行为不变）；测试可 monkeypatch 此全局
CANONICAL_APP = _PROFILE.primary_source
DEPLOY_ARGV_BASE = _PROFILE.deploy_argv  # (instance) -> argv


def next_finding_id(bb: Blackboard) -> str:
    nums = [int(f.id.split("-")[1]) for f in bb.all()]
    return f"F-{(max(nums) + 1) if nums else 1:03d}"


def re_attack(base: str, instance: str, plugin_dir: pathlib.Path, markers: List[str],
              on_exchange=None) -> List[str]:
    """收敛证明：**重新认证**后重放 attack 组（旧会话已被补丁重部署作废，必须新会话）。

    每条 payload 发射前过 payload policy（与 attack/verify 阶段同一承诺）。
    返回仍命中的 payload 文件名列表。
    """
    fresh_ctx = replay.make_ctx(base, instance, replay.admin_session(base, instance))
    hits: List[str] = []
    for pf in sorted((plugin_dir / "payloads" / "attack").glob("*.txt")):
        req = pf.read_text(encoding="utf-8")
        replay.check_payload_policy(req)
        res = replay.replay_payload(base, req, pf.name, fresh_ctx, markers,
                                    on_exchange=on_exchange)
        if res.markers_hit:
            hits.append(pf.name)
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--instance", required=True, choices=["a", "b"])
    ap.add_argument("--class", dest="cls", required=True)
    ap.add_argument("--runtime", required=True, help="靶场 runtime/<inst> 目录")
    ap.add_argument("--workspace", default=str(ROOT / "workspace"))
    ap.add_argument("--php", default=os.path.expanduser("~/tools/php/php"))
    ap.add_argument("--llm", choices=["qwen", "step", "off"], default="off",
                    help="模板精确匹配失败时的补丁适配 provider（SPEC §11 第二层）")
    ap.add_argument("--keep-patch", action="store_true",
                    help="会话结束保留补丁（D6 多轮累积用）；默认恢复漏洞态")
    a = ap.parse_args()

    # 会话标志：崩溃自愈依据（__main__ 出口统一检查恢复）
    keep_flag = pathlib.Path(a.workspace) / ".keep_patch"
    keep_flag.parent.mkdir(parents=True, exist_ok=True)
    keep_flag.write_text("1") if a.keep_patch else keep_flag.unlink(missing_ok=True)

    t0 = time.time()
    plugin_dir = _PROFILE.plugin_dir(a.cls, PLUGINS)  # T11/S1c: 目标 overlay 优先
    meta = yaml.safe_load((plugin_dir / "plugin.yaml").read_text(encoding="utf-8"))
    markers = meta["detector"]["markers"]

    # ---- Phase 0: scope 门禁（治理层真实调用）----
    scope = Scope(targets=[Target(name=f"edu-lite-{a.instance}", base_url=a.base,
                                  bind="local-only")],
                  allowed_classes=[a.cls])
    scope.check_url(a.base)
    scope.check_class(a.cls)
    print(f"[scope] OK target=edu-lite-{a.instance} class={a.cls}")

    # 规范源码会话级备份（会话末恢复，防 canonical 残留补丁污染下一会话/B 集）
    backup_dir = pathlib.Path(a.workspace) / ".canonical-backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_app = backup_dir / "app.php"
    pristine = not CANONICAL_APP.read_text(encoding="utf-8").count("[patched")
    if pristine and not backup_app.exists():
        shutil.copy2(CANONICAL_APP, backup_app)
        print(f"[backup] pristine canonical -> {backup_app}")
    if not pristine and not backup_app.exists():
        print("[backup] 拒绝运行：canonical 处于已修补状态且本 workspace 无纯净备份"
              "（先重置：git checkout 或重传 app.php）")
        return 5

    def restore(redeploy: bool = True):
        if a.keep_patch:
            print("[restore] --keep-patch，保留补丁")
            return
        if backup_app.exists():
            shutil.copy2(backup_app, CANONICAL_APP)
            if redeploy:
                subprocess.run(_PROFILE.deploy_argv(a.instance),
                               env={**os.environ, "PHP_BIN": a.php}, check=True,
                               stdout=subprocess.DEVNULL)
            print("[restore] canonical 已恢复漏洞态并重部署")

    bb = Blackboard(pathlib.Path(a.workspace))
    flow_path = bb.workspace / "flow.jsonl"
    flow_file = flow_path.open("a", encoding="utf-8")

    def record(phase: str, label: str):
        def hook(ex):
            ex.update({"ts": time.time(), "phase": phase, "label": label})
            flow_file.write(json.dumps(ex, ensure_ascii=False) + "\n")
            flow_file.flush()
        return hook

    # ---- Phase 1: attack（红方）----
    hook = record("attack", "attack")
    ctx = replay.make_ctx(a.base, a.instance, replay.admin_session(a.base, a.instance))
    hits = []
    for pf in sorted((plugin_dir / "payloads" / "attack").glob("*.txt")):
        req = pf.read_text(encoding="utf-8")
        replay.check_payload_policy(req)
        res = replay.replay_payload(a.base, req, pf.name, ctx, markers, on_exchange=hook)
        if res.markers_hit:
            hits.append((pf.name, res.markers_hit))
    print(f"[attack] {len(hits)} payload(s) HIT")
    if not hits:
        print("[attack] 未确认漏洞，退出（无可修补项）")
        restore()
        return 1

    fid = next_finding_id(bb)
    finding = Finding(id=fid, class_=a.cls, endpoint=meta.get("endpoint", ""),
                      severity=Severity(meta.get("severity", "high")),
                      evidence={"replay_ref": hits[0][0], "marker": ",".join(hits[0][1]),
                                "marker_hit": True})
    bb.add_finding(finding, round_=1)
    bb.audit("secaudit-attack", "phase.done", hits=hits)

    # ---- Phase 2: detect（蓝方，同一份 flow）----
    alerts = detect.link_findings(detect.scan_flow(flow_path), {a.cls: fid})
    (bb.workspace / "alerts.json").write_text(
        json.dumps(alerts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[detect] {len(alerts)} alert(s), linked={sum(1 for x in alerts if x['finding_link'])}")
    bb.audit("secaudit-detect", "phase.done", alerts=len(alerts))

    # ---- Phase 3: patch（模板精确应用 → php -l 语义门禁 → 重部署生效）----
    llm_fallback = None
    if a.llm != "off":
        from . import llm as llm_mod
        os.environ["AEGIS_MONOLOG"] = str(pathlib.Path(a.workspace) / "llm-monolog.jsonl")
        llm_fallback = lambda src, t, r: llm_mod.patch_adapt(src, t, r, provider=a.llm)
    original_src = CANONICAL_APP.read_text(encoding="utf-8")
    try:
        pr = patcher.apply_template(CANONICAL_APP,
                                    plugin_dir / "patch-template" / "fix.patch.tpl",
                                    llm_fallback=llm_fallback,
                                    llm_first=(a.llm != "off"))
        # php -l：确定性语法门禁（模板与 LLM 产物一律过闸）
        # 解释器取不到必须是"门禁拒绝"而不是"整轮崩溃"——外层只 catch PatchError，
        # 裸 FileNotFoundError 会逃出补丁阶段，把这一轮的台账留成无结论状态。
        gate_argv = _PROFILE.expand_syntax_gate(CANONICAL_APP, php=a.php)
        try:
            lint = subprocess.run(gate_argv, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
        except FileNotFoundError as exc:
            # 不用 exc.filename：Windows 下子进程启动失败时它是 None，报错会退化成"不可用: None"
            raise patcher.PatchError(
                f"语法门禁解释器不可用: {gate_argv[0]}（--php={a.php}）") from exc
        if lint.returncode != 0:
            raise patcher.PatchError(f"php -l failed: {(lint.stdout or '').strip()[:200]}")
        # P0 三重审查门禁：仅 LLM 模式产物（diff 边界 + 后门模式 + 异构复核，fail-closed）
        if pr.modes and "llm" in pr.modes:
            from . import patch_review
            all_targets = [t for t, _ in patcher.load_template(
                plugin_dir / "patch-template" / "fix.patch.tpl")]
            ok, reasons, _ = patch_review.review(
                original_src, CANONICAL_APP.read_text(encoding="utf-8"),
                all_targets, provider="step")
            if not ok:
                raise patcher.PatchError("patch_review 拒绝: " + "; ".join(reasons[:3]))
            print("[patch-review] LLM 产物三重审查 PASS")
    except patcher.PatchError as e:
        print(f"[patch] FAIL {e}")
        bb.transition(fid, FindingStatus.accepted, 1, detail=str(e), actor="secaudit-patch")
        restore()
        return 2
    patch_file = bb.patches_dir / f"{fid}.patch"
    patch_file.write_text(pr.diff, encoding="utf-8")
    bb.transition(fid, FindingStatus.patched, 1,
                  detail="template applied to canonical source + redeploy",
                  actor="secaudit-patch")
    print(f"[patch] template applied -> {patch_file}; redeploying...")
    # 重启实例使补丁生效（php 内置服务器存在 opcache 时间窗，重启是确定性生效方式）
    subprocess.run(_PROFILE.deploy_argv(a.instance),
                   env={**os.environ, "PHP_BIN": a.php}, check=True,
                   stdout=subprocess.DEVNULL)

    # ---- Phase 4: verify（门禁：重放必须全失败 + 功能测试必须全绿）----
    g = verify.gate(a.base, a.instance, plugin_dir, markers,
                    on_exchange=record("verify", "attack"))
    print(f"[verify] blocked={g['replay_blocked']} ({g['replay_stat']}) "
          f"tests={g['tests_passed']} :: {g['tests_tail'][-120:]}")
    if g["passed"]:
        bb.transition(fid, FindingStatus.verified, 1, detail=json.dumps(g["replay_stat"]),
                      actor="secaudit-verify")
    else:
        bb.transition(fid, FindingStatus.regressed, 1,
                      detail=f"blocked={g['replay_blocked']} tests={g['tests_passed']}",
                      actor="secaudit-verify")
        print("[verify] 门禁未过 → regressed（D3 单轮演示，回滚省略）")
        snap = bb.snapshot_round(1)
        print(f"[round1] snapshot -> {snap}")
        restore()
        return 3

    # ---- Phase 5: re-attack（收敛证明：新会话 + policy 前置）----
    re_hits = re_attack(a.base, a.instance, plugin_dir, markers,
                        on_exchange=record("re-attack", "attack"))
    converged = not re_hits
    print(f"[re-attack] hits={re_hits} converged={converged}")

    snap = bb.snapshot_round(1)
    flow_file.close()
    print(json.dumps({"result": "CONVERGED" if converged else "NOT_CONVERGED",
                      "counts": bb.counts(), "wall_s": round(time.time() - t0, 1),
                      "snapshot": str(snap), "finding": fid,
                      "status": bb.get(fid).status.value}, ensure_ascii=False))
    restore()
    return 0 if converged else 4


def crash_restore(workspace: str, php: str, instance: str, reason: str = "崩溃/异常出口"):
    """出口自愈（P2）：若 canonical 残留补丁且非 keep-patch 会话 → 恢复并重部署。"""
    backup = pathlib.Path(workspace) / ".canonical-backup" / "app.php"
    keep = (pathlib.Path(workspace) / ".keep_patch").exists()
    try:
        patched = CANONICAL_APP.read_text(encoding="utf-8").count("[patched") > 0
    except OSError:
        return
    if patched and not keep and backup.exists():
        shutil.copy2(backup, CANONICAL_APP)
        subprocess.run(_PROFILE.deploy_argv(instance),
                       env={**os.environ, "PHP_BIN": php}, check=True,
                       stdout=subprocess.DEVNULL)
        print(f"[restore:{reason}] canonical 已恢复漏洞态并重部署")


if __name__ == "__main__":
    import argparse as _ap  # noqa: F401
    from engine.run_lock import (DEFAULT_IDENTITY, RunLock, StaleLockError,
                                 default_lock_path)
    # 契约 §3:旧 CLI 必须与新入口共用同一把靶场锁——否则旧任务会在新任务
    # attack/verify 期间重部署靶场、污染证据(T7 复核 P0)。锁覆盖 main 与
    # 崩溃恢复(crash_restore 也会重部署靶场),结束后才释放。
    _lk = RunLock(default_lock_path(), DEFAULT_IDENTITY)
    try:
        if not _lk.acquire(timeout_s=120.0):
            print("[lock] runloop: 靶场被其他任务占用,拒绝启动(契约 §3)",
                  file=__import__("sys").stderr)
            raise SystemExit(3)
    except StaleLockError as _e:
        print(f"[lock] runloop: 遗留锁需人工处理,拒绝启动: {_e}",
              file=__import__("sys").stderr)
        raise SystemExit(3) from None
    try:
        try:
            code = main()
        except SystemExit as e:
            code = int(e.code or 0)
        except BaseException:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            code = 99
        finally:
            _a = None
            # 从 argv 重建关键参数（崩溃路径下 argparse 状态不可靠，重解析一次）
            try:
                _p = __import__("sys").argv
                _args = __import__("argparse").ArgumentParser()
                _args.add_argument("--workspace", default=str(ROOT / "workspace"))
                _args.add_argument("--php", default=os.path.expanduser("~/tools/php/php"))
                _args.add_argument("--instance", default="a")
                _a, _ = _args.parse_known_args(_p[1:])
            except Exception:  # noqa: BLE001
                pass
            if _a:
                crash_restore(_a.workspace, _a.php, _a.instance)
    finally:
        _lk.release()
    raise SystemExit(code)
