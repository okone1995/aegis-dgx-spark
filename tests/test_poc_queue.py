"""POC 候选队列单测（T11/A）。

三条底线反复验：
1. **未命中不得入库**：confirm 空 markers 直接拒；approve 在 confirmed 之前一律拒；
2. **票据不进库**：入队文本里的 Cookie 一律变 {{SESSION}}，base 变 {{TARGET}}；
3. **写回必须显式**：poc_apply 默认 dry-run；未 approved 的候选一个字节都不写。
"""
import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import poc_queue as PQ  # noqa: E402

RAW_REQ = ("GET http://127.0.0.1:8093/reports/exception-queue?warehouse=%27%20UNION HTTP/1.1\n"
           "Cookie: dtk=ABCDEF123456; other=1\n"
           "Accept: text/html\n"
           "\n")


@pytest.fixture()
def q(tmp_path):
    return PQ.PocQueue(tmp_path / "poc_candidates.jsonl")


def test_append_scrubs_session_and_base(q):
    res = q.append({"class": "sqli", "source": "menu", "parent": "01.txt",
                    "transform_id": "kw_case", "intent": "attack",
                    "request_text": RAW_REQ, "base": "http://127.0.0.1:8093"})
    assert res["duplicate"] is False
    row = res["candidate"]
    assert "dtk=ABCDEF123456" not in row["request_text"]
    assert "{{SESSION}}" in row["request_text"]
    assert "{{TARGET}}" in row["request_text"]
    assert "127.0.0.1:8093" not in row["request_text"]
    assert row["evidence_state"] == "untried" and row["review_state"] == "pending"


def test_duplicate_dedup(q):
    a = q.append({"class": "sqli", "source": "menu", "intent": "attack",
                  "request_text": RAW_REQ, "base": "http://127.0.0.1:8093"})
    b = q.append({"class": "sqli", "source": "freeform", "intent": "attack",
                  "request_text": RAW_REQ, "base": "http://127.0.0.1:8093"})
    assert a["duplicate"] is False and b["duplicate"] is True
    assert b["existing"]["candidate_id"] == a["candidate"]["candidate_id"]


def test_confirm_requires_hit(q):
    cid = q.append({"class": "sqli", "source": "menu", "intent": "attack",
                    "request_text": RAW_REQ})["candidate"]["candidate_id"]
    with pytest.raises(PQ.PocQueueError, match="markers_hit"):
        q.confirm(cid, {"status": 200, "response": "x"}, [])


def test_approve_before_confirm_is_refused(q):
    cid = q.append({"class": "sqli", "source": "menu", "intent": "attack",
                    "request_text": RAW_REQ})["candidate"]["candidate_id"]
    with pytest.raises(PQ.PocQueueError, match="无命中证据"):
        q.approve(cid, "reviewer-a")


def test_confirm_then_approve_is_one_way(q):
    cid = q.append({"class": "sqli", "source": "menu", "intent": "attack",
                    "request_text": RAW_REQ})["candidate"]["candidate_id"]
    row = q.confirm(cid, {"status": 200, "method": "GET", "path": "/x",
                          "response": "CREATE TABLE api_registrations"},
                    ["CREATE TABLE"], origin_run="run-x", origin_flow="FL-009")
    assert row["evidence_state"] == "confirmed"
    assert row["evidence"]["markers_hit"] == ["CREATE TABLE"]
    q.approve(cid, "reviewer-a", "命中真实 DDL 泄漏")
    assert q.get(cid)["review_state"] == "approved"
    with pytest.raises(PQ.PocQueueError, match="单向"):
        q.approve(cid, "reviewer-a")
    with pytest.raises(PQ.PocQueueError, match="单向"):
        q.reject(cid, "reviewer-a")


def test_reject_keeps_evidence_and_blocks_writeback(q):
    cid = q.append({"class": "sqli", "source": "menu", "intent": "attack",
                    "request_text": RAW_REQ})["candidate"]["candidate_id"]
    q.confirm(cid, {"status": 200, "response": "x"}, ["CREATE TABLE"])
    q.reject(cid, "reviewer-a", "与既有载荷重复")
    assert q.get(cid)["review_state"] == "rejected"
    assert q.approved() == []


def test_unknown_source_and_missing_class_refused(q):
    with pytest.raises(PQ.PocQueueError, match="class"):
        q.append({"request_text": RAW_REQ})
    with pytest.raises(PQ.PocQueueError, match="source"):
        q.append({"class": "sqli", "source": "guess", "request_text": RAW_REQ})


def test_stats_counts(q):
    q.append({"class": "sqli", "source": "menu", "intent": "attack", "request_text": RAW_REQ})
    q.append({"class": "sqli", "source": "freeform", "intent": "attack",
              "request_text": RAW_REQ.replace("UNION", "union")})
    st = q.stats()
    assert st["total"] == 2 and st["pending"] == 2
    assert st["by_source"].get("menu") == 1 and st["by_source"].get("freeform") == 1


def test_apply_tool_dry_run_writes_nothing(q, tmp_path):
    cid = q.append({"class": "sqli", "source": "menu", "intent": "attack",
                    "request_text": RAW_REQ})["candidate"]["candidate_id"]
    q.confirm(cid, {"status": 200, "response": "CREATE TABLE x"}, ["CREATE TABLE"])
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    out = subprocess.run([sys.executable, str(ROOT / "tools" / "poc_apply.py"),
                          "--queue", str(q.path), "--plugins-dir", str(plugins)],
                         capture_output=True, text=True, cwd=str(ROOT),
                         encoding="utf-8", errors="replace")
    assert out.returncode == 0, out.stderr
    assert "no approved candidates" in out.stdout.lower() or "0 written" in out.stdout.lower()
    assert not list(plugins.rglob("*.txt"))


def test_apply_tool_writes_only_approved_with_provenance(q, tmp_path):
    approved = q.append({"class": "sqli", "source": "menu", "parent": "01.txt",
                         "transform_id": "kw_case", "intent": "attack",
                         "request_text": RAW_REQ})["candidate"]["candidate_id"]
    q.confirm(approved, {"status": 200, "response": "CREATE TABLE x"}, ["CREATE TABLE"],
              origin_run="run-x", origin_flow="FL-009")
    q.approve(approved, "reviewer-a", "ok")
    # 未审核的第二条：必须一个字节都不写
    pending = q.append({"class": "sqli", "source": "freeform", "intent": "attack",
                        "request_text": RAW_REQ.replace("UNION", "uNION")})["candidate"]["candidate_id"]
    plugins = tmp_path / "plugins"
    (plugins / "sqli" / "payloads" / "attack").mkdir(parents=True)
    out = subprocess.run([sys.executable, str(ROOT / "tools" / "poc_apply.py"),
                          "--queue", str(q.path), "--plugins-dir", str(plugins), "--apply"],
                         capture_output=True, text=True, cwd=str(ROOT),
                         encoding="utf-8", errors="replace")
    assert out.returncode == 0, out.stderr
    txts = sorted((plugins / "sqli" / "payloads" / "attack").glob("*.txt"))
    assert len(txts) == 1, [t.name for t in txts]
    body = txts[0].read_text(encoding="utf-8")
    assert body.startswith("GET ") and "{{SESSION}}" in body and "{{TARGET}}" in body
    prov = json.loads(txts[0].with_suffix(".provenance.json").read_text(encoding="utf-8"))
    assert prov["candidate_id"] == approved
    assert prov["reviewer"] == "reviewer-a"
    assert prov["evidence"]["markers_hit"] == ["CREATE TABLE"]
    assert pending not in [p["candidate_id"] for p in
                           [json.loads(t.with_suffix(".provenance.json").read_text(encoding="utf-8"))
                            for t in txts]]
