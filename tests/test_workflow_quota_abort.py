"""Tests for insufficient-quota detection in workflow orchestration."""

import importlib.util
from pathlib import Path


def _load_mod(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, Path(path).resolve())
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


workflow = _load_mod("ragonometrics/pipeline/workflow.py", "ragonometrics.pipeline.workflow")


def test_is_insufficient_quota_error_true():
    exc = RuntimeError(
        "Error code: 429 - {'error': {'message': 'You exceeded your current quota', "
        "'type': 'insufficient_quota', 'code': 'insufficient_quota'}}"
    )
    assert workflow._is_insufficient_quota_error(exc) is True


def test_is_insufficient_quota_error_false():
    exc = RuntimeError("Error code: 429 - {'error': {'type': 'rate_limit_exceeded'}}")
    assert workflow._is_insufficient_quota_error(exc) is False

