"""#7 secaudit 不降级 A/B：同样输入，比"改造前(主仓) / 改造后(fork)"的门禁判定。

第二轮重写。第一轮有两个探针自身的错，都已修：
  1) 语法门禁探针按 `patcher.syntax_ok` / `patcher.php_lint` 猜函数名，两侧都不存在
     => getattr 拿到 None，`None + "."` 抛错被外层吞掉，`syntax_valid` 两侧同为 None
     => 打印出"一致 ✓"的**假一致**。本轮不再猜名字：**从 runloop.py 源码找真实调用点**，
     并按该侧自己的代码路径解析 argv；解析不出来就是 `COMPARABILITY_LOST`，
     结论行只能打印"不可比"，不得打印"一致"。
  2) fail-closed 探针把替身 profile 当**关键字参数**传给 `verify.functional_tests`
     （它没有 profile 形参）=> TypeError 兜底走了无参分支 => **真的跑了靶场 pytest**。
     本轮改为 monkeypatch `target_profile.active`，并把 `subprocess.run` 换成记录器，
     探针**只捕获 argv，绝不发射、绝不连活靶场**。

比什么：
  A 语法门禁的**判定**：合法 PHP 与坏 PHP（少一个分号）两侧各给什么（走真实 php -l）。
  B 语法门禁的**解释器来源**：改造前用 `--php` 传进来的路径；改造后用 profile argv[0]。
    两边的 `--php` 是否等价，是本轮要定性的真问题。
  C 功能门禁的**命令解析**：两侧各自构造出来的 argv/env（捕获比对，不执行）。
  D fail-closed：profile 未声明 functional_test 时改造后必须 passed=False 并写明原因。
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

REPOS = {"main": pathlib.Path(r"C:\Users\<user>\.zcode\workspace\default\aegis"),
         "fork": pathlib.Path(r"C:\Users\<user>\.zcode\workspace\default\aegis-fork")}
PHP = pathlib.Path(r"C:\Users\<user>\tools\php-8.4.26\php.exe")
DEAD_BASE = "http://127.0.0.1:1"          # 探针绝不指向活靶场
VALID = "<?php\nfunction ok($a) { return $a + 1; }\necho ok(1);\n"
BROKEN = "<?php\nfunction bad($a) { return $a + 1 }\necho bad(1);\n"   # 少一个分号


class _Recorder:
    """替身 subprocess.run：只记录 argv/env/cwd，返回假成功，不真跑任何东西。"""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        env = kw.get("env") or {}
        self.calls.append({
            "argv": [str(a) for a in argv],
            "cwd": kw.get("cwd"),
            "timeout": kw.get("timeout"),
            "env_keys": {k: str(env[k]) for k in ("EDU_BASE", "EDU_INSTANCE")
                         if k in env},
        })
        return subprocess.CompletedProcess(argv, 0, "[captured]", "")


def _lint_call_evidence(root: pathlib.Path) -> list:
    """从该侧 runloop.py 里抓语法门禁的真实调用行（证据，不是猜测）。"""
    hits = []
    src = (root / "engine" / "runloop.py").read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(src, 1):
        s = line.strip()
        if (s.startswith("lint =") or s.startswith("gate_argv =")) and \
                ("subprocess.run" in s or "expand_syntax_gate" in s):
            hits.append(f"{i}: {s}")
    return hits


def _syntax_argv(root: pathlib.Path, file: pathlib.Path, php_bin: str):
    """按该侧自己的代码路径解析语法门禁 argv；认不出来就抛（不得静默给 None）。"""
    ru = (root / "engine" / "runloop.py").read_text(encoding="utf-8")
    if "expand_syntax_gate" in ru:
        from engine import target_profile as tp
        # 与 runloop 那一行同形：改造后门禁的解释器由调用方 php= 提供
        return tp.active().expand_syntax_gate(file, php=php_bin), \
            "profile.expand_syntax_gate(php=a.php)"
    if '"-l"' in ru:
        return [php_bin, "-l", str(file)], "runloop 内联 [a.php, '-l', file]"
    raise RuntimeError("语法门禁调用点未识别 => 本轮不可比")


def probe(repo_name: str, php_bin: str) -> dict:
    root = REPOS[repo_name]
    sys.path.insert(0, str(root))
    out: dict = {"repo": repo_name, "root": str(root), "python": sys.executable}

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="ab_verify_"))
    vf, bf = tmp / "valid.php", tmp / "broken.php"
    vf.write_text(VALID, encoding="utf-8")
    bf.write_text(BROKEN, encoding="utf-8")

    # --- A/B 语法门禁：真实调用点 + 真实 php -l ---
    out["lint_source_lines"] = _lint_call_evidence(root)
    for tag, path in (("valid", vf), ("broken", bf)):
        try:
            argv, how = _syntax_argv(root, path, php_bin)
            out["syntax_gate_how"] = how
            p = subprocess.run(argv, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=60)
            out[f"syntax_{tag}"] = {"argv0": argv[0], "returncode": p.returncode,
                                    "pass": p.returncode == 0,
                                    "note": (p.stdout or p.stderr).strip()[:90]}
        except FileNotFoundError as exc:
            out[f"syntax_{tag}"] = {"argv0": getattr(exc, "filename", "?"),
                                    "pass": None, "note": f"EXEC_NOT_FOUND:{exc.filename}"}
        except Exception as exc:  # noqa: BLE001
            out[f"syntax_{tag}"] = {"pass": None,
                                    "note": f"{type(exc).__name__}:{str(exc)[:90]}"}

    # 两侧都存在的 demo_case.php_lint：源码是否逐字相同 + 行为是否相同
    try:
        import hashlib
        import inspect
        from engine import demo_case as D
        src = inspect.getsource(D.php_lint)
        out["php_lint_sha256"] = hashlib.sha256(src.encode("utf-8")).hexdigest()[:16]
        out["php_lint_valid"] = D.php_lint(php_bin, vf)
        out["php_lint_broken"] = D.php_lint(php_bin, bf)
    except Exception as exc:  # noqa: BLE001
        out["php_lint_error"] = f"{type(exc).__name__}:{str(exc)[:90]}"

    out["which_php_on_path"] = shutil.which("php")
    out["php_bin_exists"] = pathlib.Path(php_bin).exists()

    # --- C 功能门禁 argv 捕获（不执行）---
    try:
        from engine import verify as V
        rec = _Recorder()
        real_run = V.subprocess.run
        V.subprocess.run = rec
        try:
            r = V.functional_tests(DEAD_BASE, "a", python=sys.executable)
        finally:
            V.subprocess.run = real_run
        out["functional_build"] = {"calls": rec.calls,
                                   "result_passed": r.get("passed"),
                                   "result_tail": (r.get("tail") or "")[:90]}
    except Exception as exc:  # noqa: BLE001
        out["functional_build"] = {"error": f"{type(exc).__name__}:{str(exc)[:90]}"}

    # --- D fail-closed：profile 未声明 functional_test ---
    try:
        from engine import verify as V
        try:
            from engine import target_profile as tp
        except ImportError:
            out["failclosed"] = {"applies": False,
                                 "note": "该侧无 target profile 概念（命令写死在 verify.py）"}
        else:
            class _NoGates:
                name = "probe-nogates"
                functional_test_cmd = None
                _raw: dict = {}
            orig_active, real_run = tp.active, V.subprocess.run
            rec = _Recorder()
            tp.active = lambda *a, **k: _NoGates()
            V.subprocess.run = rec
            try:
                r = V.functional_tests(DEAD_BASE, "a", python=sys.executable)
            finally:
                tp.active, V.subprocess.run = orig_active, real_run
            out["failclosed"] = {"applies": True, "passed": r.get("passed"),
                                 "reason": (r.get("tail") or "")[:90],
                                 "subprocess_calls": len(rec.calls)}
    except Exception as exc:  # noqa: BLE001
        out["failclosed"] = {"error": f"{type(exc).__name__}:{str(exc)[:90]}"}
    return out


def _dec(side: dict, key: str):
    v = side.get(key)
    return v.get("pass") if isinstance(v, dict) else v


def _verdict(label: str, m, f) -> str:
    """可比性守门：任一侧不是真 bool 就报"不可比"，绝不打印"一致"。"""
    ok = isinstance(m, bool) and isinstance(f, bool)
    if not ok:
        return f"{label}: 不可比（main={m!r} fork={f!r}）✗ 探针需修"
    return f"{label}: main={m} fork={f} " + ("一致 ✓" if m == f else "不一致 ✗")


def main() -> int:
    # 本机控制台默认 GBK，✓/✗ 与中文会抛 UnicodeEncodeError 把结论吞掉
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if "--repo" in sys.argv:
        i = sys.argv.index("--repo")
        print(json.dumps(probe(sys.argv[i + 1], sys.argv[i + 2]), ensure_ascii=False))
        return 0
    php_bin = os.environ.get("PHP_BIN") or str(PHP)
    res = {}
    for name in REPOS:
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PHP_BIN=php_bin)
        if os.environ.get("AB_ADD_PHP_DIR_TO_PATH") == "1":
            env["PATH"] = str(pathlib.Path(php_bin).parent) + os.pathsep + env.get("PATH", "")
        p = subprocess.run([sys.executable, __file__, "--repo", name, php_bin],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, timeout=300)
        res[name] = (json.loads(p.stdout.strip().splitlines()[-1])
                     if p.returncode == 0 and p.stdout.strip()
                     else {"error": (p.stderr or "")[-300:]})
    print(json.dumps(res, ensure_ascii=False, indent=1))

    m, f = res.get("main", {}), res.get("fork", {})
    print("\n=== A/B 结论 ===")
    print("  " + _verdict("合法 PHP 过闸", _dec(m, "syntax_valid"), _dec(f, "syntax_valid")))
    print("  " + _verdict("坏 PHP 过闸（应为 False）",
                          _dec(m, "syntax_broken"), _dec(f, "syntax_broken")))
    print("  门禁调用点：main=%s | fork=%s" %
          (m.get("lint_source_lines"), f.get("lint_source_lines")))
    print("  解释器来源：main.argv0=%s（来自 --php=%s，存在=%s）| fork.argv0=%s（PATH 解析=%s）" %
          ((m.get("syntax_valid") or {}).get("argv0"), php_bin, m.get("php_bin_exists"),
           (f.get("syntax_valid") or {}).get("argv0"), f.get("which_php_on_path")))
    print("  php_lint 源码哈希：main=%s fork=%s %s" %
          (m.get("php_lint_sha256"), f.get("php_lint_sha256"),
           "逐字相同 ✓" if m.get("php_lint_sha256") == f.get("php_lint_sha256")
           and m.get("php_lint_sha256") else "不同/未取到 ✗"))
    print("  功能门禁 argv：main=%s\n            fork=%s" %
          ((m.get("functional_build") or {}).get("calls"),
           (f.get("functional_build") or {}).get("calls")))
    print("  fail-closed：main=%s | fork=%s" % (m.get("failclosed"), f.get("failclosed")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
