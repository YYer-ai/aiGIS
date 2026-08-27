from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from aigis_web.app import create_app

def _mock_outcome(ok=True, rows=None, geojson=None, error=""):
    m = MagicMock()
    m.ok = ok; m.sql = "SELECT 1"; m.reasoning = "r"; m.attempts = 1
    m.rows = rows or []; m.columns = ["count"]; m.geojson = geojson; m.error = error
    return m

def test_query_ok():
    with patch("aigis_web.app.run_query", return_value=_mock_outcome(rows=[(292,), (7,)], geojson={"type": "FeatureCollection", "features": []})):
        c = TestClient(create_app())
        r = c.post("/api/query", json={"question": "三环内有多少个公园"})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] and body["row_count"] == 2 and body["sql"] == "SELECT 1"
        assert body["sample_rows"] == [["292"], ["7"]]  # 前 10 行 str(v)，供前端表格

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


def _stream_lines(client, q):
    with client.stream("GET", "/api/query/stream", params={"q": q}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        return list(r.iter_lines())


def test_stream_event_sequence():
    """SSE 事件按序：status(理解问题) → status(生成SQL) → delta → result。"""
    seen_questions = []

    def fake_run(question, cfg, on_delta=None, on_status=None, **kw):
        seen_questions.append(question)  # 中文 q 经 URL 解码后原样到达
        on_status("生成SQL（第1次）")
        on_delta("SELECT 1")
        on_delta(" FR\nOM parks")  # 含换行的 chunk：SSE data 内 JSON 转义后应原样往返
        return _mock_outcome(rows=[(292,)],
                             geojson={"type": "FeatureCollection", "features": []})

    with patch("aigis_web.app.run_query_stream", side_effect=fake_run):
        c = TestClient(create_app())
        lines = _stream_lines(c, "三环内有多少个公园")

    assert seen_questions == ["三环内有多少个公园"]
    events = [l for l in lines if l.startswith("event: ")]
    assert events == ["event: status", "event: status", "event: delta", "event: delta", "event: result"]
    joined = "\n".join(lines)
    assert '{"stage": "理解问题"}' in joined
    assert '{"stage": "生成SQL（第1次）"}' in joined
    assert '{"text": "SELECT 1"}' in joined
    assert '{"text": " FR\\nOM parks"}' in joined  # 换行经 JSON 转义，单行 data 原样往返
    assert '"sql": "SELECT 1"' in joined and '"ok": true' in joined
    assert '"sample_rows": [["292"]]' in joined


def test_stream_llm_error_event():
    from aigis.llm import LLMError
    with patch("aigis_web.app.run_query_stream", side_effect=LLMError("缺少 LLM_API_KEY")):
        c = TestClient(create_app())
        lines = _stream_lines(c, "x")
    assert "event: error" in lines
    assert "LLM_API_KEY" in "\n".join(lines)


def test_stream_empty_q_422():
    c = TestClient(create_app())
    r = c.get("/api/query/stream", params={"q": "   "})
    assert r.status_code == 422 and "问题不能为空" in r.json()["error"]
