"""target_profile 加载层单测（T11/S1a，fork 版）。

四条底线：
1. 默认加载 edu-lite 且关键字段与 demo 仓历史写死值一致（行为不变）；
2. AEGIS_TARGET 指到不存在的目标 ⇒ 可见失败（TargetProfileError），绝不静默回退；
3. 语法门禁展开正确（{file} 占位）；
4. 实例 surface 与历史 INSTANCE_CFG 逐字同源；deploy_argv 可执行展开。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine import target_profile as tp  # noqa: E402

# demo 仓 engine/replay.py 的历史 INSTANCE_CFG（逐字抄录，作为行为不变的对照锚）
LEGACY_INSTANCE_CFG = {
    "a": dict(search_param="q", avatar_field="avatar", import_param="backup",
              sig_field="signature",
              avatar_url_field="avatar_url", dl_param="file", uid_param="uid",
              cookie="edu_session"),
    "b": dict(search_param="keyword", avatar_field="picture", import_param="restore",
              sig_field="motto",
              avatar_url_field="pic_url", dl_param="path", uid_param="target",
              cookie="session_key"),
}


def test_default_profile_matches_legacy_paths():
    p = tp.load()
    assert p.name == "edu-lite"
    assert p.kind == "php"
    # 与 demo 仓 runloop.py/demo_case.py 历史写死值逐字对齐
    assert p.primary_source == tp.ROOT / "target/edu-lite/src/app.php"
    assert str(p.paths_root) == str(tp.ROOT / "target/edu-lite")
    assert "deploy.sh" in p.deploy_cmd
    assert p.syntax_gate.startswith("php -l")
    assert p.instances["a"].base == "http://127.0.0.1:8081"
    assert p.instances["b"].base == "http://127.0.0.1:8082"
    assert p.instances["b"].label == "evaluation-only"
    assert p.corpus_keys.get("sqli")


def test_missing_profile_fails_visibly(monkeypatch):
    monkeypatch.setenv(tp.ENV_OVERRIDE, "no-such-target")
    with pytest.raises(tp.TargetProfileError, match="no-such-target"):
        tp.load()


def test_syntax_gate_expansion():
    p = tp.load()
    argv = p.expand_syntax_gate(Path("x/y.php"))
    assert argv[:2] == ["php", "-l"]
    assert Path(argv[2]).as_posix() == "x/y.php"


def test_syntax_gate_accepts_caller_php_binary():
    """裸令牌 php 必须能被调用方的解释器路径替换（守住改造前 [a.php,'-l',file] 的行为）。

    靶机上 php 不在 PATH（DGX 只有 ~/tools/php/php），只认 PATH 会让补丁轮崩在门禁上。
    """
    p = tp.load()
    argv = p.expand_syntax_gate(Path("x/y.php"), php="/home/<user>/tools/php/php")
    assert argv == ["/home/<user>/tools/php/php", "-l", "x/y.php"], argv
    # 不传 php 时保持原样（profile 自洽，不依赖 runloop）
    assert p.expand_syntax_gate(Path("x/y.php"), php=None)[:1] == ["php"]


def test_syntax_gate_explicit_path_not_overridden(monkeypatch, tmp_path):
    """profile 写了绝对路径 ⇒ 显式压过推断，即使调用方传了 php 也不替换。"""
    import json as _json
    raw = {
        "kind": "php", "name": "absphp",
        "paths": {"root": ".", "canonical_sources": ["app.php"],
                  "deploy_cmd": "bash deploy.sh"},
        "gates": {"syntax": "/opt/fixed/php -l {file}"},
        "instances": {"a": {"base": "http://127.0.0.1:9999"}},
    }
    d = tmp_path / "targets" / "absphp"
    d.mkdir(parents=True)
    (d / "profile.yaml").write_text(_json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(tp, "TARGETS_DIR", tmp_path / "targets")
    p = tp.load("absphp")
    argv = p.expand_syntax_gate(tmp_path / "x.php", php="/other/php")
    assert argv[0] == "/opt/fixed/php", argv


def test_instance_surface_matches_legacy_cfg():
    p = tp.load()
    for iname, legacy in LEGACY_INSTANCE_CFG.items():
        got = p.instances[iname].surface
        assert got == legacy, f"instance {iname}: surface 与历史 INSTANCE_CFG 不一致: {got}"


def test_deploy_argv_expansion():
    p = tp.load()
    argv = p.deploy_argv("a")
    assert argv[0] == "bash"
    assert argv[1].endswith("deploy.sh")
    assert argv[-1] == "a"


def test_available_lists_edu_lite():
    assert "edu-lite" in tp.available()

def test_windows_style_paths_survive_shlex(monkeypatch, tmp_path):
    """Windows 反斜杠路径必须原样保留（曾有 bug：shlex POSIX 模式吃掉反斜杠）。

    这条守的是 profile 里 ${AEGIS_PHP_BIN}=C:\\php\\php.exe 这类写法。
    """
    import json as _json
    raw = {
        "kind": "php", "name": "wintarget",
        "paths": {"root": ".", "canonical_sources": ["app.php"],
                  "deploy_cmd": "powershell -File C:\\ops\\start.ps1"},
        "gates": {"syntax": "C:\\php\\php.exe -l {file}"},
        "instances": {"a": {"base": "http://127.0.0.1:9999"}},
    }
    d = tmp_path / "targets" / "wintarget"
    d.mkdir(parents=True)
    (d / "profile.yaml").write_text(_json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(tp, "TARGETS_DIR", tmp_path / "targets")
    p = tp.load("wintarget")
    argv = p.expand_syntax_gate(tmp_path / "x.php")
    assert argv[0] == "C:/php/php.exe", argv
    dep = p.deploy_argv("a")
    assert dep[0] == "powershell" and "C:/ops/start.ps1" in dep
