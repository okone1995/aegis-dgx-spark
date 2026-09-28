"""上墙数字必须过 claims.yaml 断言（评审第 0 步的 pytest 闸）。"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_claims_all_traceable():
    env = dict(**__import__("os").environ, AEGIS_SKIP_RUNTIME_CLAIMS="1")
    r = subprocess.run([sys.executable, "bench/train/assert_claims.py"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300, env=env)
    assert r.returncode == 0, "有宣称对不上仓内出处：\n" + r.stdout[-2500:]


def test_claims_file_not_empty():
    import yaml
    claims = yaml.safe_load((ROOT / "claims.yaml").read_text(encoding="utf-8"))
    assert claims and all(c.get("claim_id") and c.get("clause") for c in claims)


def test_outward_language_gate():
    import os
    env = dict(os.environ)
    r = subprocess.run([sys.executable, "bench/train/lint_language.py"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, env=env)
    assert r.returncode == 0, "对外材料出现禁用措辞：\n" + r.stdout[-1500:]


def test_outward_numbers_traceable():
    r = subprocess.run([sys.executable, "bench/train/check_docs_numbers.py"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, "对外材料出现未溯源数字：\n" + r.stdout[-1500:]


def test_manifest_tracked_is_fresh():
    """MANIFEST.tracked.txt 是 Spark(无 git)上脱敏闸门的权威文件表,不得陈旧。

    有 git 的环境里直接比对 git ls-files;不一致即失败(改了文件忘了重生成)。"""
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    man = root / "MANIFEST.tracked.txt"
    assert man.is_file(), "缺 MANIFEST.tracked.txt(生成:git ls-files > MANIFEST.tracked.txt)"
    r = subprocess.run(["git", "ls-files"], cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        import pytest
        pytest.skip("无 git,无法校验清单新鲜度")
    listed = [l for l in man.read_text(encoding="utf-8").splitlines() if l.strip()]
    actual = [l for l in r.stdout.splitlines() if l.strip()]
    # 清单文件自身可在两侧都排除(生成时机差异:它自己往往晚于清单生成被 add)
    listed = [l for l in listed if l != "MANIFEST.tracked.txt"]
    actual = [l for l in actual if l != "MANIFEST.tracked.txt"]
    diff = set(actual) ^ set(listed)
    assert not diff, ("MANIFEST.tracked.txt 与 git ls-files 不一致(重生成: "
                      "git ls-files > MANIFEST.tracked.txt): " + str(sorted(diff)[:6]))


def test_no_host_paths_in_tracked_files():
    r = subprocess.run([sys.executable, 'bench/train/desensitize.py', '--check'],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    assert r.returncode == 0, ('跟踪文件残留宿主路径/用户名/公网 IP（SPEC B.6）：'
 + r.stdout[-1200:])
