# -*- coding: utf-8 -*-
"""T2 测试:单案例闭环(替身模式,零网络/零 PHP/零 LLM)。

模式与 tests/test_runloop_unit.py 同源:monkeypatch 打在 engine.demo_case
命名空间的模块属性上,桩对象提供阶段所需最小接口。
验收对齐:A04(严格 LLM 不回退)/A05(复核拒绝不动 canonical)/
A06(业务断言失败不得 verified)/A07(网络错误不得算修复)。
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import demo_case as DC


class _Res:
    """replay_payload 返回值桩(字段与 replay.ReplayResult 对齐)。"""

    def __init__(self, status=200, response_text="", markers_hit=None,
                 payload_file="p.txt", error=None):
        self.status = status
        self.response_text = response_text
        self.markers_hit = markers_hit or []
        self.payload_file = payload_file
        self.error = error


VULN_SRC = ("<?php\n// [vuln] news search\n"
            "$q = $_GET['q'];\n$sql = \"SELECT * FROM news WHERE title LIKE '%$q%'\";\n")
PATCHED_SRC = ("<?php\n// [patched] parameterized\n"
               "$q = $_GET['q'];\n$stmt = q_all(\"SELECT * FROM news WHERE title LIKE ?\", [$q]);\n"
               "$rows = $stmt;\n")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """搭好一圈替身:目标可达/PHP 在/LLM 产出有效补丁/复核通过/靶场被补丁拦下。"""
    return build_env(tmp_path, monkeypatch)


def build_env(tmp_path, monkeypatch):
    """替身环境构造函数(供本模块 fixture 与契约锁测试共用)。"""
    # T10-S4:桩环境里关掉自适应探针 —— 否则会真调 LLM(那是真机行为,不是单测该做的事)。
    monkeypatch.setenv("AEGIS_PROBE", "off")
    canon = tmp_path / "app.php"
    canon.write_text(VULN_SRC, encoding="utf-8")
    monkeypatch.setattr(DC, "CANONICAL_APP", canon)

    state = {"deploy": 0, "attack_hits": ["admin123"], "verify_hits": [],
             "net_err": False, "review_ok": True,
             "biz_ok": True, "biz_ok_post": True,
             "llm_out": PATCHED_SRC, "cleanup_deploy_fail": False}

    monkeypatch.setattr(DC, "http_probe", lambda base, timeout=5.0: True, raising=False)
    monkeypatch.setattr(DC, "resolve_php", lambda: "php", raising=False)
    monkeypatch.setattr(DC, "php_lint", lambda php, path: True, raising=False)

    def _deploy(php, instance):
        state["deploy"] += 1
        if state["cleanup_deploy_fail"] and state["deploy"] > 1:
            raise RuntimeError("deploy failed during cleanup")
    monkeypatch.setattr(DC, "deploy", _deploy, raising=False)

    def _login(base, keyword, expect_present=True):
        # 修补后(deploy>0)用 biz_ok_post:模拟 A06「补丁把功能关掉了」
        ok = state["biz_ok"] if state["deploy"] == 0 else state["biz_ok_post"]
        return {"ok": ok, "status": 200,
                "flows": [{"method": "GET", "path": f"/news?q={keyword}",
                           "body": "", "status": 200, "response": "期中考试通知"}]}
    monkeypatch.setattr(DC, "login_and_search", _login, raising=False)

    def _biz(res):
        ok = state["biz_ok"] if state["deploy"] == 0 else state["biz_ok_post"]
        return (ok, "ok" if ok else "missing expected content")
    monkeypatch.setattr(DC, "business_outcome", _biz, raising=False)

    monkeypatch.setattr(DC.replay, "admin_session",
                        lambda base, inst: "PHPSESSID=stub", raising=False)
    monkeypatch.setattr(DC.replay, "make_ctx", lambda base, inst, cookie: {}, raising=False)
    monkeypatch.setattr(DC.replay, "check_payload_policy", lambda text: None, raising=False)

    def _replay(base, req_text, name, ctx, markers, on_exchange=None):
        if on_exchange is not None:
            on_exchange({"method": "GET", "path": "/news?q=x", "body": "",
                         "status": 200, "response": "ok"})
        # A07:网络错误发生于**复测**(补丁后)才是验收场景;
        # 若在攻击阶段就断网,那是 preflight/attack 失败,另一条路径
        if state["net_err"] and state["deploy"] > 0:
            return _Res(status=0, response_text="", markers_hit=[], error="conn refused")
        hits = (state["verify_hits"] if state["deploy"] > 0
                else state["attack_hits"])
        return _Res(status=200, response_text="resp", markers_hit=hits,
                    payload_file=name)
    monkeypatch.setattr(DC.replay, "replay_payload", _replay, raising=False)

    monkeypatch.setattr(DC.patcher, "load_template",
                        lambda p: [("TARGET_BLOCK", "REPLACE_WITH")], raising=False)
    monkeypatch.setattr(DC.llm, "patch_adapt",
                        lambda src, t, r, provider="qwen", timeout=180: state["llm_out"],
                        raising=False)
    monkeypatch.setattr(DC.patch_review, "review",
                        lambda orig, cand, hint, provider="step":
                        (state["review_ok"], [] if state["review_ok"] else ["hunks=5 越界"],
                         "diff"), raising=False)
    # T3 依赖:默认就绪(判官+规则扫描),A13 用例单独置为不可用
    # 扫描桩按真实语义:注入路径(含 UNION SELECT)命中,正常搜索不命中(A03)
    def _scan(obs):
        path = str((obs or {}).get("path", "") or "").upper()
        m = "UNION" in path or "1%27" in path or "'" in path
        return {"matched": m,
                "alerts": ([{"id": "O-001", "class": "sqli",
                             "reasoning": "rule:SQ-001"}] if m else []),
                "scanned_at": "2026-09-26T00:00:00.000Z"}
    monkeypatch.setattr(DC, "scan_observation", _scan, raising=False)
    # 功能测试套件(契约 §2 证据项)在替身环境里桩掉:真跑 pytest 子进程会把
    # 单次测试拖到几十秒(连接失败重试)。默认返 passed(否则新终态判定会降 partial);
    # 各状态语义另有专测(见 test_functional_* )。
    monkeypatch.setattr(DC.DemoCase, "_run_functional_tests",
                        lambda self: dict(state.get("functional",
                                                    {"status": "passed", "exit_code": 0})),
                        raising=False)

    class _JudgeStub:
        @staticmethod
        def judge(obs, timeout_s=30):
            if state.get("judge_down"):
                raise RuntimeError("judge unavailable (stub)")
            # T7 复核修正(P0-2):支持“第 N 次起失败”以制造 judge_status=partial
            n = state.get("judge_calls", 0) + 1
            state["judge_calls"] = n
            if state.get("judge_fail_from") and n >= state["judge_fail_from"]:
                raise RuntimeError("judge call failed (stub)")
            return {"p_attack": 0.93, "verdict": "attack", "band": "confident",
                    "latency_ms": 5.0, "model_version": "judge_v3_mix_lora",
                    "protocol_id": "judge-protocol-v1"}
    monkeypatch.setattr(DC, "judge_client", _JudgeStub, raising=False)

    inst = DC.DemoCase(runs_root=tmp_path / "runs", base="http://127.0.0.1:8081")
    return inst, state, canon


def _events(inst):
    rows = []
    with (inst.run_dir / "events.jsonl").open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def test_evidence_receipt_is_source_tagged_and_exposed(env):
    """T10-S2:收据必须“每项标源 seq/工件、缺失为 unknown”,并经白名单暴露。

    防的是两类事:①把没有出处的东西写成结论;②把 unknown 默认当 PASS。
    """
    inst, state, canon = env
    assert inst.run() == 0
    rec = json.loads((inst.run_dir / "evidence-receipt.json").read_text(encoding="utf-8"))
    # 顶层字段必须在，且每一项要么带 seq/工件出处，要么显式 unknown（不得凭空有效）
    for key in ("run_id", "finding_id", "state", "gates", "verification",
                "patch_candidate", "deployed_hash", "cleanup", "learning"):
        assert key in rec, "收据缺字段: %s" % key
    for sec in ("patch_candidate", "deployed_hash", "gates", "cleanup", "learning"):
        s = str((rec.get(sec) or {}).get("source", ""))
        assert s.startswith("seq:") or s == "unknown", "%s 的出处不合法: %r" % (sec, s)
    for k, item in (rec.get("verification") or {}).items():
        s = str((item or {}).get("source", ""))
        assert s in ("artifact:verification.json", "unknown"), "%s 出处不合法: %r" % (k, s)
    # 候选与部署同源时 hash 必须一致（不能候选一个、部署另一个）
    cand_sha = (rec.get("patch_candidate") or {}).get("sha256")
    dep_sha = (rec.get("deployed_hash") or {}).get("value")
    if cand_sha not in (None, "unknown") and dep_sha not in (None, "unknown"):
        assert cand_sha == dep_sha, "候选/部署 hash 不一致: %r vs %r" % (cand_sha, dep_sha)
    # 复测流若存在，必须显式 replay_of 补前攻击
    pre = rec.get("pre_patch_attack") or {}
    for r in (rec.get("post_patch_replays") or []):
        assert str(r.get("source", "")).startswith("seq:")
        if pre.get("flow_id"):
            assert r.get("replay_of") == pre.get("flow_id")
    # 白名单必须暴露该工件
    arts = json.loads((inst.run_dir / "artifacts.json").read_text(encoding="utf-8"))
    assert "evidence-receipt" in {a.get("id") for a in arts}
    # replication 关联必须也进 verification.completed 载荷（页面不靠“最后一条 attack”猜）
    vc = [e for e in _events(inst) if e.get("type") == "verification.completed"]
    assert vc, "缺 verification.completed"
    assert "replay_linkage" in (vc[-1].get("payload") or {}), "verification.completed 缺 replay_linkage"


def test_network_error_in_verify_is_inconclusive_not_internal_error(env, monkeypatch):
    """A07/D10:验证阶段目标不可达 ⇒ network_error + inconclusive + **仍写 verification.json**。

    真机实测教训:原先连接异常直接冒泡 ⇒ 整轮变成 internal_error 且没有 verification.json,
    “不算成功”虽成立,但规范语义拿不到。本测试钉死这两点。
    """
    inst, state, canon = env
    real = DC.replay.admin_session

    def _flaky(*a, **k):
        # 只让 **verify 阶段**那一次拿会话失败(否则会把 cleanup 也打断,那测的就不是 A07 了)
        if getattr(inst, "_cur_stage", "") == "verify":
            raise ConnectionError("Connection refused: dead port")
        return real(*a, **k)

    monkeypatch.setattr(DC.replay, "admin_session", _flaky)
    code = inst.run()
    assert code != 0
    meta = json.loads((inst.run_dir / "run.json").read_text(encoding="utf-8"))
    # 终态只要求“不算成功”;实测引擎把 inconclusive 映射为 partial(而非 failed)。
    # 口径张力已登记:计划书 §4.1 把“核心验证失败”归在 failed,而本例是“验证没跑成”。
    assert meta["state"] in ("failed", "partial"), meta.get("state")
    assert meta["state"] != "succeeded"
    assert meta["repair_outcome"] == "inconclusive", meta.get("repair_outcome")
    assert meta["cleanup"] == "restored"
    ver = json.loads((inst.run_dir / "verification.json").read_text(encoding="utf-8"))
    assert ver["network_error"] is True
    assert ver["blocked"] is False, "零次交换绝不得算阻断"
    assert ver["exchanges"] == 0
    ev = [e for e in _events(inst) if e.get("type") == "verification.completed"]
    assert ev, "必须产出 verification.completed 事件"


def test_happy_path_succeeds(env):
    inst, state, canon = env
    code = inst.run()
    assert code == 0
    meta = json.loads((inst.run_dir / "run.json").read_text(encoding="utf-8"))
    assert meta["state"] == "succeeded"
    assert meta["repair_outcome"] == "verified"
    assert meta["cleanup"] == "restored"          # 默认 restore:靶场恢复漏洞态
    assert canon.read_text(encoding="utf-8") == VULN_SRC
    types = {e["type"] for e in _events(inst)}
    need = {"run.created", "run.started", "stage.started", "business.checked",
            "flow.captured", "patch.proposed", "gate.completed", "patch.applied",
            "verification.completed", "cleanup.completed"}
    assert need <= types, f"缺事件: {need - types}"
    seqs = [e["seq"] for e in _events(inst)]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


def test_attack_not_exploited_fails(env):
    inst, state, canon = env
    state["attack_hits"] = []
    code = inst.run()
    assert code != 0
    meta = json.loads((inst.run_dir / "run.json").read_text(encoding="utf-8"))
    assert meta["state"] == "failed"
    assert "attack" in (meta.get("error") or "")


def test_llm_identical_output_fails_no_template_fallback(env):
    """A04:原样输出 → failed,且 canonical 必须一字未动(禁止静默回退模板)。"""
    inst, state, canon = env
    state["llm_out"] = VULN_SRC
    code = inst.run()
    assert code != 0
    meta = json.loads((inst.run_dir / "run.json").read_text(encoding="utf-8"))
    assert meta["state"] == "failed"
    assert "llm_patch_failed" in (meta.get("error") or "")
    assert canon.read_text(encoding="utf-8") == VULN_SRC
    assert not (inst.run_dir / "patches" / "F-001.candidate.php").exists()


def test_review_rejected_keeps_canonical(env):
    """A05:复核拒绝 → failed,部署源保持原 hash。"""
    inst, state, canon = env
    state["review_ok"] = False
    code = inst.run()
    assert code != 0
    assert canon.read_text(encoding="utf-8") == VULN_SRC
    meta = json.loads((inst.run_dir / "run.json").read_text(encoding="utf-8"))
    assert meta["repair_outcome"] == "rejected"
    assert meta["cleanup"] == "restored"


def test_network_error_not_verified(env):
    """A07:复测网络失败 → 不得 verified。"""
    inst, state, canon = env
    state["net_err"] = True
    inst.run()
    summary = json.loads((inst.run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["repair_outcome"] != "verified"
    assert summary["state"] in ("failed", "partial")


def test_business_assertion_failure_not_verified(env):
    """A06:补丁关闭了功能(补后业务断言失败) → 不能 verified。"""
    inst, state, canon = env
    state["biz_ok_post"] = False
    code = inst.run()
    assert code != 0
    summary = json.loads((inst.run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["repair_outcome"] != "verified"
    assert summary["state"] == "failed"


def test_cleanup_failure_forces_failed(env):
    """cleanup 恢复失败 → 终态一律 failed。"""
    inst, state, canon = env
    state["cleanup_deploy_fail"] = True
    code = inst.run()
    assert code != 0
    meta = json.loads((inst.run_dir / "run.json").read_text(encoding="utf-8"))
    assert meta["state"] == "failed"
    assert meta["cleanup"] == "failed"


def test_functional_tests_skip_is_honest(tmp_path, monkeypatch):
    """契约 §2 证据项:缺 PHP/套件时必须标 skipped 并写明原因,不得写成通过。"""
    inst, _state, _canon = build_env(tmp_path, monkeypatch)
    monkeypatch.undo()  # 拿回真实的 _run_functional_tests
    res = inst._run_functional_tests()
    assert res["status"] in ("skipped", "passed", "failed", "error", "timeout")
    if res["status"] == "skipped":
        assert res.get("reason"), "skipped 必须带原因"


def test_artifacts_manifest_written(env):
    """B2:终态后写 artifacts.json 白名单(可点击工件),且排除武器化请求包。"""
    inst, state, canon = env
    inst.run()
    arts = json.loads((inst.run_dir / "artifacts.json").read_text(encoding="utf-8"))
    ids = {a["id"] for a in arts}
    assert {"summary", "verification", "patch-diff", "patch-candidate"} <= ids, ids
    paths = {a["path"] for a in arts}
    assert not any("flow.jsonl" in p or "judge.jsonl" in p for p in paths), \
        "武器化请求/判读明细不进公开白名单"
    for a in arts:
        assert a["sha256"] and a["scrubbed"] is True


def test_stage_durations_are_sane(env):
    """T7 真机事故回归:阶段耗时曾因 time.time()-monotonic 混用写成 1.79e9 秒。
    全程单调时钟下,任何单阶段耗时都应是可解释的量级。"""
    inst, state, canon = env
    inst.run()
    summary = json.loads((inst.run_dir / "summary.json").read_text(encoding="utf-8"))
    durs = summary["stage_durations"]
    assert durs, "应有阶段耗时表"
    for name, v in durs.items():
        assert 0 <= v < 3600, f"{name} 耗时 {v}s 不可信(混用时钟?)"


def test_patch_diff_file_written(env):
    """契约 §4.1:patches/ 存 diff;工件白名单的 patch-diff 依赖它。"""
    inst, state, canon = env
    inst.run()
    pf = inst.run_dir / "patches" / "F-001.patch"
    assert pf.is_file(), "候选 diff 应落 patches/F-001.patch"
    assert pf.read_text(encoding="utf-8").strip(), "diff 不应为空"


def test_partial_state_has_own_event(env):
    """B7:partial 终态有自己的事件(run.partial),不再借 run.failed 冒充。"""
    inst, state, canon = env
    state["judge_down"] = True
    inst.run()
    types = [e["type"] for e in _events(inst)]
    assert "run.partial" in types
    assert "run.failed" not in types, "partial 不应被记成 failed"


def test_judge_unavailable_blocks_succeeded(env):
    """A13:判官不可用 → 修复证据可继续,但完整演示不得 succeeded(降 partial)。"""
    inst, state, canon = env
    state["judge_down"] = True
    inst.run()
    summary = json.loads((inst.run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["state"] == "partial"
    assert summary["repair_outcome"] == "verified"          # 修复证据本身成立
    assert "judge_status=unavailable" in summary["degraded_reasons"]


def test_summary_prefix_matches_terminal_state(env):
    """T7 真机事故回归:partial 轮的 summary 文本曾写成 `succeeded: ...`
    (前缀取自修复证据结论而非终态),与 state 自相矛盾。前缀必须等于终态。"""
    inst, state, canon = env
    state["judge_down"] = True
    inst.run()
    snap = json.loads((inst.run_dir / "run.json").read_text(encoding="utf-8"))
    assert snap["state"] == "partial"
    assert snap["summary"].startswith("partial:"), snap["summary"]
    assert "succeeded:" not in snap["summary"]
    # run.partial 事件的 summary 同样不得写 succeeded
    ev = [e for e in _events(inst) if e["type"] == "run.partial"][0]
    assert ev["summary"].startswith("partial:"), ev["summary"]


def test_run_json_has_contract_keys_always(env):
    """契约 §4.1:error / degraded_reasons / repair_outcome / judge_status /
    learning_status 必须恒在(T7 真机实测:成功轮缺 error 键)。"""
    inst, state, canon = env
    inst.run()
    snap = json.loads((inst.run_dir / "run.json").read_text(encoding="utf-8"))
    for k in ("error", "degraded_reasons", "repair_outcome", "judge_status",
              "learning_status", "exit_code", "summary", "cleanup"):
        assert k in snap, f"run.json 缺契约字段 {k}"
    assert snap["degraded_reasons"] == []


# ---------- T7 复核修正(P0):终态必须被全部主演示证据约束 ----------

def test_functional_failure_blocks_success(env):
    """P0-1:功能测试失败仍报成功——必须 failed,repair 不得 verified。"""
    inst, state, canon = env
    state["functional"] = {"status": "failed", "exit_code": 1,
                           "tail": "1 failed: test_news_list..."}
    code = inst.run()
    snap = json.loads((inst.run_dir / "run.json").read_text(encoding="utf-8"))
    assert snap["state"] == "failed", snap["summary"]
    assert snap["repair_outcome"] != "verified"
    assert snap["exit_code"] != 0
    assert "functional_tests_failed" in (snap.get("error") or "")
    types = [e["type"] for e in _events(inst)]
    assert "run.finished" not in types, "失败轮不得发 run.finished"


@pytest.mark.parametrize("fstatus", ["skipped", "timeout", "error"])
def test_functional_skipped_degrades_to_partial(env, fstatus):
    """P0-1 续:套件不存在/超时/异常 ⇒ 证据不完整 ⇒ partial,不写“已通过”。"""
    inst, state, canon = env
    state["functional"] = {"status": fstatus}
    inst.run()
    snap = json.loads((inst.run_dir / "run.json").read_text(encoding="utf-8"))
    assert snap["state"] == "partial", f"{fstatus} → {snap['state']}"
    assert f"functional_tests={fstatus}" in (snap.get("degraded_reasons") or [])
    assert snap["summary"].startswith("partial:")


def test_functional_passed_keeps_success(env):
    inst, state, canon = env
    state["functional"] = {"status": "passed", "exit_code": 0}
    inst.run()
    snap = json.loads((inst.run_dir / "run.json").read_text(encoding="utf-8"))
    assert snap["state"] == "succeeded"
    assert snap["degraded_reasons"] == []


def test_judge_partial_blocks_success(env):
    """P0-2:某条判读失败(judge_status=partial)不得报全部成功。"""
    inst, state, canon = env
    state["judge_fail_from"] = 2          # 第一条成功、其余失败 ⇒ partial
    inst.run()
    snap = json.loads((inst.run_dir / "run.json").read_text(encoding="utf-8"))
    assert snap["judge_status"] == "partial"
    assert snap["state"] == "partial", snap["summary"]
    assert "judge_status=partial" in snap["degraded_reasons"]


def test_learning_not_run_blocks_success(env, monkeypatch):
    """P0-2 续:学习模块不可用(learning_status=not_run)不得报全部成功。"""
    inst, state, canon = env
    monkeypatch.setattr(DC, "learning_queue", None, raising=False)
    inst.run()
    snap = json.loads((inst.run_dir / "run.json").read_text(encoding="utf-8"))
    assert snap["learning_status"] == "not_run"
    assert snap["state"] == "partial", snap["summary"]
    assert "learning_status=not_run" in snap["degraded_reasons"]
