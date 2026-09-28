"""payload 验收 harness —— 元修复制度（D2 评审采纳）：

在【漏洞实例】上全量发射 attack+verify 两组 payload，断言每条 marker HIT；
held-out 组只有先证明能打中，"补丁后 blocked"才是有效信号。
良性流量（--benign）断言零 marker 误命中（benign_exempt 豁免）。

用法（Spark）:
  ~/venvs/aegis/bin/python -m engine.acceptance --base http://127.0.0.1:8081 --instance a
  ~/venvs/aegis/bin/python -m engine.acceptance --base http://127.0.0.1:8081 --instance a --benign
"""
from __future__ import annotations

import argparse
import glob
import json
import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from engine import replay

PLUGINS_DIR = pathlib.Path(__file__).resolve().parent.parent / "plugins"


def run(base: str, instance: str, plugins_root: pathlib.Path) -> int:
    cookie = replay.admin_session(base, instance)
    ctx = replay.make_ctx(base, instance, cookie)
    failures = []
    total = 0
    for plugin_yaml in sorted(plugins_root.glob("*/plugin.yaml")):
        meta = yaml.safe_load(plugin_yaml.read_text(encoding="utf-8"))
        cls = meta["class"]
        markers = meta["detector"]["markers"]
        for group in ("attack", "verify"):
            for pf in sorted(glob.glob(str(plugin_yaml.parent / "payloads" / group / "*.txt"))):
                req = pathlib.Path(pf).read_text(encoding="utf-8")
                # 元检查：marker 不得出现在请求行的 query 中（GET 参数会被页面回显，自命中造出打不死的假漏洞）。
                # 请求体中的 marker（上传内容/gadget 载荷）是设计使然——判定面是落盘文件，不查回显。
                req_line = req.splitlines()[0] if req else ""
                echoed = [m for m in markers if m in req_line]
                if echoed:
                    print(f"ECHO-MARKER {cls}/{group}/{pathlib.Path(pf).name} "
                          f"markers 在 payload 文本中: {echoed}")
                    failures.append(f"{cls}/{group}/{pathlib.Path(pf).name} (echo-marker)")
                replay.check_payload_policy(req)
                res = replay.replay_payload(base, req, pathlib.Path(pf).name, ctx, markers)
                total += 1
                ok = bool(res.markers_hit) and not res.error
                flag = "HIT " if ok else "MISS"
                print(f"{flag} {cls}/{group}/{res.payload_file} "
                      f"[{res.status}] markers={res.markers_hit} {res.error}")
                if not ok:
                    failures.append(f"{cls}/{group}/{res.payload_file}")
    print(f"\n== payload acceptance: {total - len(failures)}/{total} HIT ==")
    return len(failures)


def benign_check(base: str, instance: str) -> int:
    """良性流量响应不得命中 marker（豁免行数信号类）。"""
    import subprocess, tempfile
    meta_markers = []
    exempts = []
    for plugin_yaml in sorted(PLUGINS_DIR.glob("*/plugin.yaml")):
        meta = yaml.safe_load(plugin_yaml.read_text(encoding="utf-8"))
        meta_markers += meta["detector"]["markers"]
        exempts += meta["detector"].get("benign_exempt", [])
    strict = [m for m in set(meta_markers) if m not in exempts]
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        out = f.name
    from .target_profile import active as _active_profile
    _bt = _active_profile().benign_traffic_cmd
    script = PLUGINS_DIR.parent / _bt.split()[-1] if _bt else None
    if script is None:
        raise RuntimeError("target profile 未声明 benign_traffic 门禁")
    subprocess.run([sys.executable, str(script), "--base", base, "--instance", instance,
                    "--sessions", "6", "--out", out, "--capture"], check=True)
    hits = 0
    for line in open(out, encoding="utf-8"):
        rec = json.loads(line)
        for m in strict:
            if m in rec.get("response_snippet", ""):
                print(f"FALSE-POSITIVE marker {m!r} on benign {rec['kind']}")
                hits += 1
    print(f"== benign check: {'PASS' if hits == 0 else f'{hits} marker hits'} ==")
    return hits


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8081")
    ap.add_argument("--instance", default="a", choices=["a", "b"])
    ap.add_argument("--plugins", default=str(PLUGINS_DIR))
    ap.add_argument("--benign", action="store_true")
    a = ap.parse_args()
    bad = run(a.base, a.instance, pathlib.Path(a.plugins))
    if a.benign:
        bad += benign_check(a.base, a.instance)
    sys.exit(1 if bad else 0)
