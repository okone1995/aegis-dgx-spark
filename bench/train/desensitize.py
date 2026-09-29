"""公开前脱敏（SPEC B.6）——把仓内产物里的宿主绝对路径/用户名/内网痕迹掩掉。

只动**字符串里的路径痕迹**，不动任何度量值；数值型宣称由 claims 闸独立守护。
用法:  python bench/train/desensitize.py --check   # 只报告（门禁用）
       python bench/train/desensitize.py --write   # 就地脱敏
"""
from __future__ import annotations
import argparse
import pathlib
from pathlib import PurePosixPath
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
RULES = [
    (re.compile(r"/home/asus_gx10"), "/home/<user>"),
    (re.compile(r"c:\+users\+okone", re.I), r"C:\Users\<user>"),
    (re.compile(r"C:/Users/OKONE", re.I), "C:/Users/<user>"),
    (re.compile(r"\b61\.172\.235\.130\b"), "<SPARK_HOST>"),
    (re.compile(r"asus_gx10@"), "<user>@"),
    (re.compile(r"asus_gx10"), "<user>"),
]
SKIP_SUFFIX = (".png", ".jpg", ".gif", ".pdf", ".pptx", ".mp4", ".zip", ".gz")


ARTIFACT_DIR_PREFIXES = (
    # 无 git 环境(如 Spark 远端 ~/aegis 非 git 仓)下回退扫描时必须跳过的运行产物目录。
    # 它们不是仓库内容:是靶场/marathon 跑出来的证据与临时目录,含宿主路径属正常。
    # T7 施工记录:未加此表时,Spark 上全量 pytest 会因这些产物误报脱敏闸门失败。
    "dataset/raw/runloop_archive/", "workspace/", "workspace_selftest/",
    ".jail/", "scp_fix/", "scp_in/", "scp_tmp2/", "models/", "venvs/",
    ".venv/", "console/node_modules/", "target/edu-lite/runtime/",
)


def tracked_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        # 无 git 环境(如 Spark 远端 ~/aegis 非 git 仓):优先用归档自带的权威清单
        # MANIFEST.tracked.txt(内容= last commit 的 git ls-files)。
        # 没清单才回退扫树,并跳过运行产物目录——否则 Spark 上独有的
        # runloop_archive/scp_*/bench 产物会误报(T7 实测 6 文件误报)。
        manifest = ROOT / "MANIFEST.tracked.txt"
        if manifest.is_file():
            return [l.strip() for l in manifest.read_text(encoding="utf-8").splitlines()
                    if l.strip() and not l.strip().endswith(SKIP_SUFFIX)]
        return [str(p.relative_to(ROOT)) for p in ROOT.rglob("*")
                if p.is_file() and not p.suffix in SKIP_SUFFIX
                and not str(p.relative_to(ROOT)).replace("\\", "/")
                .startswith(ARTIFACT_DIR_PREFIXES)]
    return [l for l in out.stdout.splitlines() if l and not l.endswith(SKIP_SUFFIX)]


SELF = "bench/train/desensitize.py"   # 规则串就是被检模式本身，扫描必须排除自己


def scan(files, write=False):
    hits = {}
    for rel in files:
        if PurePosixPath(rel).as_posix() == SELF:
            continue
        p = ROOT / rel
        if not p.is_file():
            continue
        try:
            txt = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        new = txt
        for pat, rep in RULES:
            new = pat.sub(lambda m: rep, new)   # 替换串按字面量，不吃转义
        if new != txt:
            n = sum(len(pat.findall(txt)) for pat, _ in RULES)
            hits[rel] = n
            if write:
                p.write_text(new, encoding="utf-8")
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    hits = scan(tracked_files(), write=a.write)
    if hits:
        print(("已脱敏 " if a.write else "待脱敏（SPEC B.6）：") + f"{len(hits)} 个文件")
        for f, n in sorted(hits.items(), key=lambda x: -x[1])[:20]:
            print(f"  {n:4d} 处  {f}")
        return 0 if a.write else 1
    print("脱敏扫描：跟踪文件内无宿主路径/用户名/公网 IP 痕迹")
    return 0


if __name__ == "__main__":
    sys.exit(main())
