"""jail_ctl —— jail 档的隔离笼生命周期（用户拍板 2026-09-25）。

职责：起/验/拆 docker 笼，产出可复验的门票 `cage.json`。
发射端（attacker.plan_free / bench/spark/gen_freeflows）只认 cage.json；**marathon 在线环刻意不接 jail**（评审 P2：原注释把 marathon 列为发射端是撒谎） + 每次发射前的
`verify_cage_isolation` 机器自检——policy jail 档的松绑建立在 namespace
物证上，不建立在词表或口头承诺上。自检不过 → 异常 → 调用方回退 strict。

拓扑（与宿主 125B/数据集/证据链同机不同网）：
  docker network aegis-jail (--internal，无网关路由=物理断外网)
    ├─ aegis-cage   ：edu-lite 靶（php:8.3-cli，/app=tmpfs 256m，--cap-drop ALL）
    └─ aegis-canary ：假外联落点（php -S :80，任何"外联"都只到达这里并记账）
  宿主与笼的全部通信走 docker exec / internal 桥 IP，-p 发布端口一律不用。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

NET = "aegis-jail"
CAGE = "aegis-cage"
CANARY = "aegis-canary"
IMAGE = "docker.m.daocloud.io/library/php:8.3-cli-bookworm"
REPO = Path(__file__).resolve().parent.parent


def _sh(args, **kw):
    r = subprocess.run(args, capture_output=True, text=True, timeout=kw.get("timeout", 120))
    if r.returncode != 0 and kw.get("check", True):
        raise RuntimeError(f"{' '.join(args[:4])}… rc={r.returncode}: {r.stderr[:200]}")
    return r


def _ip(name: str) -> str:
    out = _sh(["docker", "inspect", "-f",
               "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", name]).stdout
    ip = out.strip()
    if not ip:
        raise RuntimeError(f"{name} 无 internal 桥 IP")
    return ip


def _running(name: str) -> bool:
    return _sh(["docker", "inspect", "-f", "{{.State.Running}}", name],
               check=False).stdout.strip() == "true"


def _exec(name: str, args, timeout=20):
    return subprocess.run(["docker", "exec", name, *args],
                          capture_output=True, text=True, timeout=timeout)


def up(staging: Path, force: bool = False) -> dict:
    staging = Path(staging).resolve()
    jail_root = (REPO / ".jail").resolve()
    # 评审 P1：--staging 漏值/传 "/" 会让下面的 rm -rf 端掉 CWD 乃至根目录。
    # staging 是一次性盆，只准落在仓根 .jail/ 之下。
    if jail_root == staging or jail_root not in staging.parents:
        # 复审 P2：写成 `!=` 时短路，--staging <仓根>/.jail 会 rm -rf 掉门票自身
        raise RuntimeError(f"staging 必须是 {jail_root} 的**真子目录**（拒 {staging}）")
    if _running(CAGE) or _running(CANARY):
        if not force:
            raise RuntimeError("笼已在场——先 down 或 --force")
        down(quiet=True)
    _sh(["docker", "network", "create", "--driver", "bridge", "--internal",
         "--subnet", "172.31.99.0/24", NET], check=False)
    src = REPO / "target" / "edu-lite"
    # staging 是一次性盆：容器内 php 以 --user 运行时写回的卷属主会漂移，
    # 整盆重建比增量清理可靠（盆外无 precious 数据，证据都在 cage.json）。
    if staging.exists():
        _sh(["rm", "-rf", str(staging)])
    staging.mkdir(parents=True)
    token = uuid.uuid4().hex
    (staging / ".aegis_jail_token").write_text(token, encoding="utf-8")  # 随 /probe 拷进 /app
    for d in ("src", "config", "files"):
        _sh(["cp", "-r", str(src / d), str(staging / d)], check=False)
    _sh(["cp", str(src / "router.php"), str(staging / "router.php")])
    # app 写路径是 __DIR__/../{data,uploads}——/app 下兄弟目录，staging 不带就起不来
    for d in ("data", "uploads"):
        (staging / d).mkdir(exist_ok=True)
    # canary=独立容器（"互联网上的敌方 C2"的闭世界替身）：与靶同网不同机，
    # 靶即使 RCE 也只能到达它的 php -S 表面，摸不到记账脚本本体。
    canary = staging / "canary"
    canary.mkdir(exist_ok=True)
    (canary / "index.php").write_text(
        "<?php file_put_contents('/tmp/canary.log', date('c').' '.($_SERVER['REMOTE_ADDR']??'?')"
        ".\" \" . ($_SERVER['REQUEST_URI']??'/').PHP_EOL, FILE_APPEND);"
        "echo 'AEGIS-CANARY-RECEIVED';", encoding="utf-8")
    uid = f"{os.getuid()}:{os.getgid()}"   # 容器与宿主同 uid：卷写回不污染属主
    _sh(["docker", "run", "-d", "--name", CANARY, "--network", NET, "--cap-drop", "ALL",
         "--security-opt", "no-new-privileges", "--user", uid,
         "--memory", "128m", "--cpus", "0.25",
         "-v", f"{canary}:/www:ro", IMAGE,
         "php", "-S", "0.0.0.0:80", "-t", "/www"])
    _sh(["docker", "run", "-d", "--name", CAGE, "--network", NET, "--cap-drop", "ALL",
         "--security-opt", "no-new-privileges", "--user", uid,
         "--memory", "1g", "--cpus", "2",
         "--tmpfs", f"/app:rw,size=256m,uid={os.getuid()}", "--tmpfs", f"/tmp:rw,size=32m,uid={os.getuid()}",
         "-v", f"{staging}:/probe:ro", IMAGE,
         "sh", "-c", "cp -r /probe/. /app/ && cd /app && exec php -S 0.0.0.0:8081 router.php"])
    t0 = time.time()
    while time.time() - t0 < 20:
        r = _exec(CAGE, ["php", "-r", 'echo (bool)@fsockopen("127.0.0.1",8081);'])
        if "1" in r.stdout:
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("cage 内 http 服务 20s 未就绪")
    rec = {
        "token": token,
        "name": CAGE,
        "ip": "http://" + _ip(CAGE) + ":8081",
        "canary_ip": _ip(CANARY),   # "外联"改写落点：独立 canary 容器:80，internal 网内物理到不了真外网
        "container_id": _sh(["docker", "inspect", "-f", "{{.Id}}", CAGE]).stdout.strip(),
        "canary_container_id": _sh(["docker", "inspect", "-f", "{{.Id}}", CANARY]).stdout.strip(),
        "image": IMAGE, "network": NET, "staging": str(staging),
        "limits": {"memory": "1g", "cpus": "2", "tmpfs": "/app:256m"},
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    from governance.payload_policy import Cage as PCage, load_cage, verify_cage_isolation
    # 门票放仓根 .jail/ 而非 workspace/——demo.sh 每轮把 workspace 整体归档搬走，
    # 泡在 marathon 循环里时 workspace 态的门票会失踪（2026-09-25 soak 并发实证）。
    p = REPO / ".jail" / "cage.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    proof = verify_cage_isolation(load_cage(str(p)))
    (REPO / ".jail" / "selfproof.json").write_text(
        json.dumps({"proof": proof, "record": rec}, indent=1), encoding="utf-8")
    return rec


def down(quiet: bool = False) -> None:
    for n in (CAGE, CANARY):
        _sh(["docker", "rm", "-f", n], check=False)
    if not quiet:
        print("cage down")


def status() -> dict:
    if not (REPO / ".jail" / "cage.json").is_file():
        return {"present": False}
    from governance.payload_policy import load_cage, verify_cage_isolation
    rec = load_cage(str(REPO / ".jail" / "cage.json")).record
    out = {"present": True, "running": _running(CAGE) and _running(CANARY),
           "ip": rec["ip"]}
    try:
        out["proof"] = verify_cage_isolation(load_cage(str(REPO / ".jail" / "cage.json")))
    except Exception as e:  # noqa: BLE001 —— 状态查询不得抛
        out["proof"] = f"FAIL: {e}"
    return out


def main() -> int:
    ap = argparse.ArgumentParser(prog="engine.jail_ctl")
    ap.add_argument("cmd", choices=["up", "down", "status"])
    ap.add_argument("--staging", default=str(REPO / ".jail" / "staging"))
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    try:
        if a.cmd == "up":
            rec = up(Path(a.staging), force=a.force)
            print(json.dumps(rec, indent=1))
        elif a.cmd == "down":
            down()
        else:
            print(json.dumps(status(), indent=1, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        print(f"FATAL: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
