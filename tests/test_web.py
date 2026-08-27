import json
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient
from aigis_web.app import _MAKE_INTENT_RE, create_app

def _mock_outcome(ok=True, rows=None, geojson=None, error=""):
    m = MagicMock()
    m.ok = ok; m.sql = "SELECT 1"; m.reasoning = "r"; m.attempts = 1
    m.rows = rows or []; m.columns = ["count"]; m.geojson = geojson; m.error = error
    return m

def test_query_ok():
    with patch("aigis_web.app.run_query", return_value=_mock_outcome(rows=[(292,), (7,)], geojson={"type": "FeatureCollection", "features": []})), \
         patch("aigis_web.app.summarize", side_effect=lambda *a: iter(["查询", "完成"])):
        c = TestClient(create_app())
        r = c.post("/api/query", json={"question": "三环内有多少个公园"})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] and body["row_count"] == 2 and body["sql"] == "SELECT 1"
        assert body["sample_rows"] == [["292"], ["7"]]  # 前 10 行 str(v)，供前端表格
        assert body["answer"] == "查询完成"  # 同步端点非流式拿全 answer

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
    """SSE 事件按序：status(理解问题) → status(生成SQL) → delta → status(总结中) → answer_delta → result。"""
    seen_questions = []

    def fake_run(question, cfg, on_delta=None, on_status=None, **kw):
        seen_questions.append(question)  # 中文 q 经 URL 解码后原样到达
        on_status("生成SQL（第1次）")
        on_delta("SELECT 1")
        on_delta(" FR\nOM parks")  # 含换行的 chunk：SSE data 内 JSON 转义后应原样往返
        return _mock_outcome(rows=[(292,)],
                             geojson={"type": "FeatureCollection", "features": []})

    with patch("aigis_web.app.run_query_stream", side_effect=fake_run), \
         patch("aigis_web.app.summarize", side_effect=lambda *a: iter(["查询完成"])):
        c = TestClient(create_app())
        lines = _stream_lines(c, "三环内有多少个公园")

    assert seen_questions == ["三环内有多少个公园"]
    events = [l for l in lines if l.startswith("event: ")]
    assert events == ["event: status", "event: status", "event: delta", "event: delta",
                      "event: status", "event: answer_delta", "event: result"]
    joined = "\n".join(lines)
    assert '{"stage": "理解问题"}' in joined
    assert '{"stage": "生成SQL（第1次）"}' in joined
    assert '{"stage": "总结中"}' in joined
    assert '{"text": "SELECT 1"}' in joined
    assert '{"text": " FR\\nOM parks"}' in joined  # 换行经 JSON 转义，单行 data 原样往返
    assert '{"text": "查询完成"}' in joined
    assert '"sql": "SELECT 1"' in joined and '"ok": true' in joined
    assert '"sample_rows": [["292"]]' in joined
    assert '"answer": "查询完成"' in joined  # result 内含完整 answer


def test_stream_answer_multi_delta():
    """answer_delta 逐 token 流出，result.answer 为拼接全文。"""
    def fake_run(question, cfg, on_delta=None, on_status=None, **kw):
        return _mock_outcome(rows=[(292,)],
                             geojson={"type": "FeatureCollection", "features": []})

    tokens = ["三环内", "共有 ", "292 个公园"]
    with patch("aigis_web.app.run_query_stream", side_effect=fake_run), \
         patch("aigis_web.app.summarize", side_effect=lambda *a: iter(tokens)):
        c = TestClient(create_app())
        lines = _stream_lines(c, "三环内有多少个公园")

    events = [l for l in lines if l.startswith("event: ")]
    assert events == ["event: status", "event: status",
                      "event: answer_delta", "event: answer_delta", "event: answer_delta",
                      "event: result"]
    joined = "\n".join(lines)
    for t in tokens:
        assert f'{{"text": "{t}"}}' in joined
    assert '"answer": "三环内共有 292 个公园"' in joined


def test_stream_no_answer_on_failed_query():
    """执行失败（ok=False）：不进入总结，result 无 answer 内容（空串）。"""
    def fake_run(question, cfg, on_delta=None, on_status=None, **kw):
        return _mock_outcome(ok=False, error="SQL 语法错误")

    with patch("aigis_web.app.run_query_stream", side_effect=fake_run), \
         patch("aigis_web.app.summarize", side_effect=lambda *a: iter(["不应被调用"])) as sm:
        c = TestClient(create_app())
        lines = _stream_lines(c, "x")

    sm.assert_not_called()
    events = [l for l in lines if l.startswith("event: ")]
    assert events == ["event: status", "event: result"]
    assert '"answer": ""' in "\n".join(lines)


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


# ---------- 意图路由（制作关键词正则） ----------

@pytest.mark.parametrize("q,hit", [
    ("把三环内的公园做500米缓冲区生成新图层", True),   # 生成.{0,6}图层
    ("把道路裁剪到四环范围内，做成新图层", True),        # 做成
    ("把这个结果保存为图层", True),                     # 保存为图层
    ("帮我新建图层存放区县质心", True),               # 新建图层（连续字面）
    ("把公园和河流叠加成图层", True),                   # 叠加.{0,4}图层
    ("把同名公园面合并成新图层", True),                 # 合并.{0,6}图层
    ("对河流做200米缓冲后保存为图层", True),            # 缓冲.{0,8}图层
    ("三环内有多少个公园", False),
    ("查询地铁站在三环内的分布", False),
    ("长安街有多长", False),
])
def test_make_intent_regex(q, hit):
    assert bool(_MAKE_INTENT_RE.search(q)) is hit


def _mock_make_outcome(ok=True, error="", table_name="x", label="测试图层",
                       feature_count=20, geojson=None):
    m = MagicMock()
    m.ok = ok; m.error = error; m.table_name = table_name; m.label = label
    m.feature_count = feature_count
    m.sql = f"CREATE TABLE user_layers.{table_name} AS SELECT 1"
    m.geojson = geojson if geojson is not None else {
        "type": "FeatureCollection", "features": []}
    return m


def test_stream_make_flow_event_sequence():
    """制作关键词命中 → make 流：status(理解制作需求)→status(生成建图SQL)→delta
    →status(总结中)→answer_delta→result（QueryResponse 兼容结构）。"""
    def fake_make(question, cfg, on_delta=None, on_status=None, **kw):
        on_status("生成建图SQL（第1次）")
        on_delta("CREATE TABLE user_layers.x AS SELECT 1")
        return _mock_make_outcome()
    with patch("aigis_web.app.run_make_task", side_effect=fake_make), \
         patch("aigis_web.app.run_query_stream") as rq, \
         patch("aigis_web.app.summarize", side_effect=lambda *a: iter(["已生成图层"])):
        c = TestClient(create_app())
        lines = _stream_lines(c, "把三环内的公园做500米缓冲区生成新图层")
    rq.assert_not_called()  # 路由命中：不进查询流
    events = [l for l in lines if l.startswith("event: ")]
    assert events == ["event: status", "event: status", "event: delta",
                      "event: status", "event: answer_delta", "event: result"]
    joined = "\n".join(lines)
    assert '{"stage": "理解制作需求"}' in joined
    assert '{"stage": "生成建图SQL（第1次）"}' in joined
    assert '"columns": ["layer_name", "label"]' in joined
    assert '"sample_rows": [["x", "测试图层"]]' in joined
    assert '"row_count": 20' in joined and '"ok": true' in joined
    assert '"answer": "已生成图层"' in joined


def test_stream_make_failure_error_event():
    """make 失败：SSE error 事件带原始错误 + 改用查询表述提示，无 result。"""
    with patch("aigis_web.app.run_make_task",
               return_value=_mock_make_outcome(ok=False, error="制作 SQL 校验未通过：仅允许 CREATE TABLE")), \
         patch("aigis_web.app.summarize") as sm:
        c = TestClient(create_app())
        lines = _stream_lines(c, "生成一个测试图层")
    sm.assert_not_called()
    joined = "\n".join(lines)
    assert "event: error" in lines and "event: result" not in lines
    assert "校验未通过" in joined and "查询表述" in joined


def test_stream_non_make_question_goes_query_flow():
    """普通查询不进 make 流，首事件仍为「理解问题」。"""
    with patch("aigis_web.app.run_make_task") as rm, \
         patch("aigis_web.app.run_query_stream",
               side_effect=lambda q, cfg, on_delta=None, on_status=None, **kw:
                     _mock_outcome(rows=[(1,)], geojson={"type": "FeatureCollection", "features": []})), \
         patch("aigis_web.app.summarize", side_effect=lambda *a: iter(["查询完成"])):
        c = TestClient(create_app())
        lines = _stream_lines(c, "三环内有多少个公园")
    rm.assert_not_called()
    assert '{"stage": "理解问题"}' in "\n".join(lines)


# ---------- 图层库 CRUD ----------

def _mock_conn(rows=None, fetchone=None, description=None):
    """psycopg.connect 的 mock：with conn, with cur 两层上下文均直通。"""
    cur = MagicMock()
    cur.fetchall.return_value = rows or []
    cur.fetchone.return_value = fetchone
    cur.description = description
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    conn.cursor.return_value.__enter__.return_value = cur
    conn.cursor.return_value.__exit__.return_value = False
    return conn


def test_list_layers_200():
    import datetime
    conn = _mock_conn(rows=[("parks_buf", "公园缓冲", 20,
                             datetime.datetime(2026, 8, 27, 12, 0))])
    with patch("aigis_web.app.psycopg.connect", return_value=conn):
        c = TestClient(create_app())
        r = c.get("/api/layers")
    assert r.status_code == 200
    assert r.json() == [{"layer_name": "parks_buf", "label": "公园缓冲",
                         "feature_count": 20, "created_at": "2026-08-27 12:00:00"}]


def test_layer_geojson_unregistered_404():
    conn = _mock_conn(fetchone=None)  # registry 查无此层
    with patch("aigis_web.app.psycopg.connect", return_value=conn):
        c = TestClient(create_app())
        r = c.get("/api/layers/evil_layer/geojson")
    assert r.status_code == 404


def test_layer_geojson_bad_name_404():
    c = TestClient(create_app())
    r = c.get("/api/layers/Registry/geojson")  # 大写非法名：正则层直接拒，不连库
    assert r.status_code == 404


def test_delete_layer_unregistered_404():
    conn = _mock_conn(fetchone=None)
    with patch("aigis_web.app.psycopg.connect", return_value=conn):
        c = TestClient(create_app())
        r = c.delete("/api/layers/no_such")
    assert r.status_code == 404


def test_delete_layer_ok():
    conn = _mock_conn(fetchone=(1,))
    with patch("aigis_web.app.psycopg.connect", return_value=conn), \
         patch("aigis_web.app.drop_maker_layer", return_value=(True, "")) as drop:
        c = TestClient(create_app())
        r = c.delete("/api/layers/parks_buf")
    assert r.status_code == 200 and r.json() == {"deleted": "parks_buf"}
    drop.assert_called_once()


@pytest.fixture()
def clean_layer():
    """真库 CRUD 用例前后幂等清理（同 test_make.py）。"""
    from aigis.config import Config
    from aigis.make import drop_maker_layer
    drop_maker_layer("test_m3_layer", Config())
    yield
    drop_maker_layer("test_m3_layer", Config())


@pytest.mark.integration
def test_layers_crud_roundtrip(clean_layer):
    """真库三态全链路：make 建注册 → 列表 → geojson → DELETE 清理 → 404。"""
    from aigis.config import Config
    from aigis.make import run_make_task
    provider = MagicMock()
    provider.generate.return_value = json.dumps({
        "table_name": "test_m3_layer",
        "sql": "CREATE TABLE user_layers.test_m3_layer AS "
               "SELECT name, geom FROM osm_pois LIMIT 20",
        "label": "M4测试图层"}, ensure_ascii=False)
    assert run_make_task("x", Config(), provider=provider).ok
    c = TestClient(create_app())

    names = [item["layer_name"] for item in c.get("/api/layers").json()]
    assert "test_m3_layer" in names

    r = c.get("/api/layers/test_m3_layer/geojson")
    assert r.status_code == 200
    gj = r.json()
    assert gj["type"] == "FeatureCollection" and len(gj["features"]) == 20
    assert all("geom" not in f["properties"] for f in gj["features"])  # WKB 列已剔除

    assert c.delete("/api/layers/test_m3_layer").json() == {"deleted": "test_m3_layer"}
    assert c.get("/api/layers/test_m3_layer/geojson").status_code == 404
    assert c.delete("/api/layers/test_m3_layer").status_code == 404
    assert "test_m3_layer" not in [
        item["layer_name"] for item in c.get("/api/layers").json()]
