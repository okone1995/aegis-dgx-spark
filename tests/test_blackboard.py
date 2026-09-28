import json

import pytest

from engine.blackboard import Blackboard, BlackboardError
from engine.models import Finding, FindingStatus, Severity


def mk(fid="F-001"):
    return Finding(
        id=fid, class_="upload_bypass",
        endpoint="POST /profile/avatar", severity=Severity.critical,
    )


def test_full_lifecycle(tmp_path):
    bb = Blackboard(tmp_path)
    bb.add_finding(mk())
    f = bb.transition("F-001", FindingStatus.patched, round_=1, actor="secaudit-patch")
    assert f.status is FindingStatus.patched
    f = bb.transition("F-001", FindingStatus.verified, round_=1, actor="secaudit-verify")
    assert f.status is FindingStatus.verified
    assert bb.counts() == {"verified": 1}


def test_illegal_transition_rejected(tmp_path):
    bb = Blackboard(tmp_path)
    bb.add_finding(mk())
    with pytest.raises(BlackboardError):
        bb.transition("F-001", FindingStatus.verified, round_=1)  # open→verified 非法


def test_retry_then_auto_accept(tmp_path):
    bb = Blackboard(tmp_path, max_retries=2)
    bb.add_finding(mk())
    bb.transition("F-001", FindingStatus.patched, 1)
    bb.transition("F-001", FindingStatus.regressed, 1)
    f = bb.transition("F-001", FindingStatus.patched, 2)   # retry 1
    assert f.retries == 1
    bb.transition("F-001", FindingStatus.regressed, 2)
    f = bb.transition("F-001", FindingStatus.patched, 3)   # retry 2
    assert f.retries == 2
    bb.transition("F-001", FindingStatus.regressed, 3)
    f = bb.transition("F-001", FindingStatus.patched, 4)   # 超限 → 自动 accepted
    assert f.status is FindingStatus.accepted
    assert "auto-accepted" in f.history[-1].detail


def test_verified_can_refound(tmp_path):
    bb = Blackboard(tmp_path)
    bb.add_finding(mk())
    bb.transition("F-001", FindingStatus.patched, 1)
    bb.transition("F-001", FindingStatus.verified, 1)
    f = bb.transition("F-001", FindingStatus.open, 3, detail="mutation bypass")  # 后续轮次被绕过
    assert f.status is FindingStatus.open


def test_accepted_is_terminal(tmp_path):
    bb = Blackboard(tmp_path)
    bb.add_finding(mk())
    bb.transition("F-001", FindingStatus.accepted, 1, detail="risk accepted")
    with pytest.raises(BlackboardError):
        bb.transition("F-001", FindingStatus.patched, 2)


def test_persistence_and_audit_and_snapshot(tmp_path):
    bb = Blackboard(tmp_path)
    bb.add_finding(mk())
    bb.transition("F-001", FindingStatus.patched, 1)
    snap = bb.snapshot_round(1)
    assert snap.exists()
    assert json.loads(snap.read_text())["counts"] == {"patched": 1}

    bb2 = Blackboard(tmp_path)  # 重新加载
    assert bb2.get("F-001").status is FindingStatus.patched
    lines = (tmp_path / "audit-log.jsonl").read_text().strip().splitlines()
    assert any(json.loads(l)["event"] == "finding.transition" for l in lines)
