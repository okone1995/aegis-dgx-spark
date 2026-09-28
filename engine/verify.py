"""验证门禁执行器 —— SPEC §11：攻击重放必须失败 + 功能测试必须全绿。

verify 重放插件包 attack+verify **两组** payload（held-out 组防"补丁只挡自家 payload"），
任一 marker 命中即 blocked=False；功能测试跑 pytest 于目标实例。
"""
from __future__ import annotations

import glob
import os
import pathlib
import subprocess
import sys

from .replay import admin_session, make_ctx, replay_payload

ROOT = pathlib.Path(__file__).resolve().parent.parent


def replay_all(base: str, instance: str, plugin_dir: pathlib.Path,
               markers: list, on_exchange=None, verify_mode: str = "all_blocked") -> dict:
    """重放该插件全部 payload（attack+verify），返回统计。

    verify_mode（plugin.yaml 可覆盖）：
      all_blocked —— 全部 payload 必须 zero-marker（默认，绝大多数类别）
      last_blocked —— 仅最后一个 payload 必须被阻断（brute_no_lock：前 N-1 次
                      401 是合法行为，第 N 次被锁定才是补丁生效的证据）
    """
    import yaml
    ctx = make_ctx(base, instance, admin_session(base, instance))
    results = []  # (group, name, hit: bool)
    for group in ("attack", "verify"):
        for pf in sorted(glob.glob(str(plugin_dir / "payloads" / group / "*.txt"))):
            req = pathlib.Path(pf).read_text(encoding="utf-8")
            from .replay import check_payload_policy
            check_payload_policy(req)
            res = replay_payload(base, req, pathlib.Path(pf).name, ctx, markers)
            if on_exchange:
                on_exchange({"method": "REPLAY", "path": f"{group}/{res.payload_file}",
                             "body": "", "status": res.status,
                             "response": res.response_text[:400],
                             "marker_hits": res.markers_hit})
            results.append((group, pathlib.Path(pf).name, bool(res.markers_hit)))
    hit = sum(1 for _, _, h in results if h)
    if verify_mode == "last_blocked":
        # 语义（D5 评审后再修正）：每组内【首条】正常处理（401=正常错误行为未回归）
        # 且【末条】被阻断（锁定存在）。中间各条不约束——真实锁定会在组中途生效，
        # "前 N-1 全 hit"会误杀一切提前生效的锁定。
        by_group = {}
        for g, _, h in results:
            by_group.setdefault(g, []).append(h)
        blocked = bool(by_group) and all(
            gs and gs[0] and not gs[-1] for gs in by_group.values())
    else:
        blocked = hit == 0
    return {"total": len(results), "hit": hit, "blocked": blocked, "mode": verify_mode}


def functional_tests(base: str, instance: str, python: str = sys.executable) -> dict:
    """目标功能测试门禁 —— 命令与环境变量由 target profile 提供（T11/S1d）。

    profile 未声明 functional_test 时视为**缺门禁**：passed=False 并写明原因，
    不得静默当成通过（红线：缺依赖必须可见失败）。
    """
    import shlex
    try:
        from .target_profile import active as _active_profile
        prof = _active_profile()
    except Exception as exc:  # noqa: BLE001
        return {"passed": False, "tail": f"target profile 不可用: {exc}"}
    if not prof.functional_test_cmd:
        return {"passed": False, "tail": "target profile 未声明 functional_test 门禁"}
    env = dict(os.environ)
    for k, v in (prof._raw.get("gates", {}).get("functional_test_env") or {}).items():
        env[k] = str(v).replace("{base}", base).replace("{instance}", instance)
    argv = shlex.split(prof.functional_test_cmd)
    argv = [python if a == "python" else a for a in argv]
    argv = [str(ROOT / a) if a.endswith(".py") and not a.startswith(("/", "\\")) else a
            for a in argv]
    proc = subprocess.run(argv, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env,
                          cwd=str(ROOT), timeout=300)
    return {"passed": proc.returncode == 0,
            "tail": ((proc.stdout or proc.stderr) or "").strip()[-300:]}


def gate(base: str, instance: str, plugin_dir: pathlib.Path, markers: list,
         on_exchange=None) -> dict:
    import yaml
    meta = yaml.safe_load((plugin_dir / "plugin.yaml").read_text(encoding="utf-8"))
    verify_mode = meta.get("verify_mode", "all_blocked")
    blocked = replay_all(base, instance, plugin_dir, markers, on_exchange,
                         verify_mode=verify_mode)
    tests = functional_tests(base, instance)
    return {"replay_blocked": blocked["blocked"], "replay_stat": blocked,
            "tests_passed": tests["passed"], "tests_tail": tests["tail"],
            "passed": blocked["blocked"] and tests["passed"]}
