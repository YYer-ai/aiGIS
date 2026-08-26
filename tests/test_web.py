from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from aigis_web.app import create_app

def _mock_outcome(ok=True, rows=None, geojson=None, error=""):
    m = MagicMock()
    m.ok = ok; m.sql = "SELECT 1"; m.reasoning = "r"; m.attempts = 1
    m.rows = rows or []; m.columns = ["count"]; m.geojson = geojson; m.error = error
    return m

def test_query_ok():
    with patch("aigis_web.app.run_query", return_value=_mock_outcome(rows=[(292,)], geojson={"type": "FeatureCollection", "features": []})):
        c = TestClient(create_app())
        r = c.post("/api/query", json={"question": "三环内有多少个公园"})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] and body["row_count"] == 1 and body["sql"] == "SELECT 1"

def test_query_llm_error_502():
    from aigis.llm import LLMError
    with patch("aigis_web.app.run_query", side_effect=LLMError("缺少 LLM_API_KEY")):
        c = TestClient(create_app())
        r = c.post("/api/query", json={"question": "x"})
        assert r.status_code == 502 and "LLM_API_KEY" in r.json()["error"]

def test_query_db_error_502():
    import psycopg
    with patch("aigis_web.app.run_query", side_effect=psycopg.OperationalError("conn refused")):
        c = TestClient(create_app())
        r = c.post("/api/query", json={"question": "x"})
        assert r.status_code == 502 and "数据库" in r.json()["error"]

def test_query_timeout_502():
    from psycopg.errors import QueryCanceled  # 继承 OperationalError，sqlstate=57014
    with patch("aigis_web.app.run_query",
               side_effect=QueryCanceled("canceling statement due to statement timeout")):
        c = TestClient(create_app())
        r = c.post("/api/query", json={"question": "x"})
        assert r.status_code == 502 and "超时" in r.json()["error"]

def test_empty_question_422():
    c = TestClient(create_app())
    r = c.post("/api/query", json={"question": "  "})
    assert r.status_code == 422
