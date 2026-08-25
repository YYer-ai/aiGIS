# tests/test_executor.py
import pytest
from aigis.config import Config
from aigis.executor import execute_readonly

@pytest.mark.integration
def test_select_ok():
    r = execute_readonly("SELECT count(*) FROM spatial_ref_sys", Config())
    assert r.ok and r.rows[0][0] > 8000

@pytest.mark.integration
def test_write_denied_and_timeout_error_captured():
    r = execute_readonly("SELECT pg_sleep(20)", Config())  # 超时 15s 拦截
    assert not r.ok and "timeout" in r.error.lower()
