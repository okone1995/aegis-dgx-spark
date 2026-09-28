"""Regression (qcode report #1): omitting --reviewer must NOT pass the gate."""
import importlib.util
import os
import pathlib

SKILL = pathlib.Path(__file__).resolve().parents[1]


def _mod():
    spec = importlib.util.spec_from_file_location(
        "aegis_evolve", SKILL / "scripts" / "aegis_evolve.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_none_or_empty_reviewer_is_rejected():
    m = _mod()
    os.environ["AEGIS_EVOLVE_OPERATOR"] = "op-name"
    for bad in (None, "", "   "):
        try:
            m._operator(bad)
        except m.BridgeError as exc:
            assert str(exc) in ("operator_reviewer_required", "operator_gate_not_satisfied")
        else:  # pragma: no cover
            raise AssertionError("gate passed with reviewer=%r" % (bad,))


def test_matching_reviewer_passes():
    m = _mod()
    os.environ["AEGIS_EVOLVE_OPERATOR"] = "op-name"
    m._operator("op-name")
