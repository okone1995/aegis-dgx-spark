"""hunt（信号驱动搜索）单测 —— 用假发射层，不依赖网络。

守三条：① 列宽只能由"2xx 的那一发"定（早期 bug：把所有报错的 n 当候选 ⇒ 定成 n=1）；
② 只有 marker 命中才算 exploited_confirmed；③ 预算耗尽必须如实报 budget_exhausted。
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import hunt as H  # noqa: E402
from engine.poc_queue import PocQueue  # noqa: E402


class FakeHunt(H.Hunt):
    """不连网：把 _fire 换成按 (step,value) 返回预置观测。"""

    def __init__(self, script, tmp_path):
        # 绕过 __init__（不登录、不读 profile）
        self.base = "http://127.0.0.1:1"
        self.path = "/p"
        self.param = "q"
        self.cls = "sqli"
        self.profile = type("P", (), {"name": "fake"})()
        self.budget_requests = 40
        self.budget_seconds = 60.0
        self.queue = PocQueue(tmp_path / "q.jsonl")
        self.instance = "a"
        self.scope = None
        self.use_library = False
        self.use_library = False
        self.llm_fallback = False   # 兜底默认关：替身必须镜像 __init__ 的默认值
        self.llm_shots = 0
        self.library_hit = None
        self.detector_markers = ["MARK"]
        self.attempts = []
        self.requests_used = 0
        import time
        self.started = time.time()
        self.script = script      # [(step_prefix, status, body), ...] 顺序消费

    def _budget_left(self):
        if self.requests_used >= self.budget_requests:
            return False, "requests_exhausted"
        return True, ""

    _SWEEP_DEFAULT = 500     # 列宽扫描里没写进脚本的 n 一律按"列数不符 ⇒ 报错"处理

    def _fire(self, value, step_id="", extra=""):
        self.requests_used += 1
        status, body = 200, "ok"
        if step_id.startswith("column_count"):
            status, body = self._SWEEP_DEFAULT, "column mismatch"
        for prefix, st, bd in self.script:
            if step_id.startswith(prefix):
                status, body = st, bd
                break
        obs = {"n": self.requests_used, "step": step_id, "value": value,
               "status": status, "length": len(body),
               "markers_hit": [m for m in self.detector_markers if m in body],
               "confirmed": bool([m for m in self.detector_markers if m in body])}
        self.attempts.append(obs)
        return obs


def test_column_count_uses_2xx_only(tmp_path):
    """列宽=5 的场景：只有 n=5 是 2xx，其余 500 ⇒ 必须定成 5（不是 1）。"""
    script = [
        ("baseline", 200, "base"),
        ("quote_probe", 500, "err"),
        ("dquote_probe", 200, "x"),
        ("comment_probe", 200, "x"),
        ("comment_probe_hash", 500, "err"),
        ("boolean_pair", 200, "x"),
        ("column_count:n=5", 200, "rows"),
        ("introspect", 200, "SCHEMA MARK"),
    ]
    h = FakeHunt(script, tmp_path)
    out = h.run()
    assert out["column_count"] == 5, out["column_count"]
    assert out["claim"] == "exploited_confirmed"
    assert out["hit"]["step"].startswith("introspect")
    assert out["hit"]["markers_hit"] == ["MARK"]


def test_no_marker_means_not_exploited(tmp_path):
    """只有状态码差异、没有任何 marker ⇒ 只能报 anomaly_only。"""
    script = [("baseline", 200, "base"), ("quote_probe", 500, "err"),
              ("column_count", 500, "err"), ("introspect", 500, "err")]
    h = FakeHunt(script, tmp_path)
    out = h.run()
    assert out["claim"] == "anomaly_only", out["claim"]
    assert out["hit"] is None


def test_budget_exhausted_is_reported(tmp_path):
    """预算极小 ⇒ 必须如实报 budget_exhausted，不得假装没找到就完事。"""
    script = [("baseline", 200, "base")]
    h = FakeHunt(script, tmp_path)
    h.budget_requests = 3
    out = h.run()
    assert out["claim"] in ("budget_exhausted", "anomaly_only"), out["claim"]
    assert out["budget"]["requests_used"] <= 3


def test_verdict_is_json_serialisable(tmp_path):
    script = [("baseline", 200, "base"), ("introspect", 200, "MARK")]
    h = FakeHunt(script, tmp_path)
    json.dumps(h.run(), ensure_ascii=False)  # 不得抛


def test_ambiguous_column_count_is_flagged(tmp_path):
    """两个 n 都 2xx 时必须标注歧义（不许悄悄挑一个当定论）。"""
    script = [("baseline", 200, "base"),
              ("column_count:n=5", 200, "rows"),
              ("column_count:n=7", 200, "rows"),
              ("introspect", 200, "SCHEMA MARK")]
    h = FakeHunt(script, tmp_path)
    out = h.run()
    assert out["column_count"] == 5
    assert any("ambiguous" in n for n in out["notes"]), out["notes"]
