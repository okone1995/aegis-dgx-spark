# -*- coding: utf-8 -*-
"""T10-S3(UI):三层指标接口必须是"服务端读文件"的真值,且第三层如实为空。

纪律:页面上墙的每个数字都要能指到文件。本测试做两件事:
  ① 逐项核对接口返回值 == 源结果文件里的值(不许四舍五入编造);
  ② 核对"真外部分布"层如实标为未采集(不得因为整机只有一条链路就声称泛化)。
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from console.backend import main as M  # noqa: E402


def _get_metrics():
    c = TestClient(M.app)
    r = c.get("/api/demo/models")
    assert r.status_code == 200, r.text[:200]
    j = r.json()
    m = j.get("metrics_three_layers")
    assert isinstance(m, dict), "缺 metrics_three_layers 块"
    return m


def test_offline_layer_matches_source_file():
    m = _get_metrics()
    off = m["layers"]["offline_same_distribution"]
    src = json.loads((ROOT / off["source"]).read_text(encoding="utf-8"))
    assert off["n"] == src["n"]
    assert off["acc"] == src["accuracy_overall"]["acc"]
    assert off["precision"] == src["precision_attack"]
    assert off["recall"] == src["recall_attack"]
    assert off["fp"] == src["confusion_attack_positive"]["fp"]
    assert off["fn"] == src["confusion_attack_positive"]["fn"]
    assert off["abstain_ratio"] == src["abstain"]["ratio"]
    assert off["band"] == src["protocol"]["band"], "离线口径带必须原样带出"


def test_small_sample_weak_items_match_source():
    m = _get_metrics()
    src = json.loads((ROOT / m["small_sample_weak"]["source"]).read_text(encoding="utf-8"))
    by_class = src["by_class"]
    assert m["small_sample_weak"]["items"], "小样本弱项不得为空(如实披露是硬要求)"
    for it in m["small_sample_weak"]["items"]:
        assert it["n"] == by_class[it["class"]]["n"]
        assert it["acc"] == by_class[it["class"]]["acc"]


def test_external_layer_is_honestly_empty_and_dataset_facts_match():
    m = _get_metrics()
    ext = m["layers"]["external_distribution"]
    assert ext["status"] == "未采集"
    assert ext["source"] is None
    st = json.loads((ROOT / m["dataset"]["source"]).read_text(encoding="utf-8"))
    assert m["dataset"]["train_n"] == st["train_n"]
    assert m["dataset"]["holdout_n"] == st["holdout_n"]
    assert m["dataset"]["b_family_overlap"] == st["b_family_overlap"] == 0
    assert m["dataset"]["families"]["cross_split_overlap"] == 0
