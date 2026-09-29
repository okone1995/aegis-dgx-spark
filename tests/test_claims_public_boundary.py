"""Public evidence checks must never turn a missing JSON claim into success."""
import importlib.util
import json
from pathlib import Path


def validator(tmp_path, monkeypatch, claims):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("claims_boundary", root/"bench/train/assert_claims.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path/"claims.yaml").write_text(json.dumps(claims), encoding="utf-8")
    return module


def test_public_lists_private_checks_without_calling_them(tmp_path, monkeypatch, capsys):
    m = validator(tmp_path, monkeypatch, [{"claim_id": "private", "kind": "computed",
        "fn": "wave5_exchanges", "expected": 47, "clause": "private raw evidence"}])
    monkeypatch.setattr("sys.argv", ["assert_claims.py", "--public", "--json"])
    assert m.main() == 0
    data = json.loads(capsys.readouterr().out)
    assert data["scope"] == "public_shipped_evidence"
    assert data["claims"] == []
    assert data["not_evaluated"][0]["status"] == "not_evaluated"
    monkeypatch.setattr("sys.argv", ["assert_claims.py", "--json"])
    assert m.main() == 1


def test_public_missing_json_claim_still_fails(tmp_path, monkeypatch, capsys):
    m = validator(tmp_path, monkeypatch, [{"claim_id": "broken", "kind": "json",
        "file": "missing.json", "selector": "metric", "expected": 1, "clause": "must fail"}])
    monkeypatch.setattr("sys.argv", ["assert_claims.py", "--public", "--json"])
    assert m.main() == 1
    data = json.loads(capsys.readouterr().out)
    assert data["claims"][0]["ok"] is False
    assert data["not_evaluated"] == []
