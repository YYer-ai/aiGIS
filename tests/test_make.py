# tests/test_make.py
import json
from unittest.mock import MagicMock

import psycopg
import pytest

from aigis.config import Config
from aigis.make import (_existing_layers_text, drop_maker_layer, run_make_task,
                        save_geojson_layer, validate_make)

# ---------- validate_make 矩阵 ----------

GOOD = [
    # 基本 CTAS：缓冲派生（米制 geography）
    "CREATE TABLE user_layers.parks_buf AS SELECT name, "
    "ST_Buffer(geom::geography, 500)::geometry AS geom "
    "FROM osm_areas WHERE leisure='park'",
    # 内层 WITH（CTE）：CTAS 的 AS 后接带 CTE 的 SELECT
    "CREATE TABLE user_layers.ring_parks AS WITH t AS ("
    "SELECT * FROM osm_areas WHERE leisure='park') "
    "SELECT t.name, t.geom FROM t JOIN ring_areas r ON r.ring_name='三环' "
    "WHERE ST_Contains(r.geom, t.geom)",
    # 图层叠加：内层引用 user_layers 既有合法图层表
    "CREATE TABLE user_layers.parks_near AS SELECT a.name, a.geom "
    "FROM user_layers.parks_buf a WHERE ST_DWithin(a.geom::geography, "
    "(SELECT geom FROM osm_pois WHERE name LIKE '%故宫%' LIMIT 1), 1000)",
    # AS 后括号包裹的子查询
    "CREATE TABLE user_layers.pts AS (SELECT name, geom FROM osm_pois LIMIT 10)",
    # 内层 UNION
    "CREATE TABLE user_layers.mixed AS SELECT name, geom FROM osm_pois "
    "UNION SELECT name, geom FROM osm_areas",
    # 保存图层属性统计：属性存 properties jsonb 列，用 ->> 提取（few-shot 同款模式）
    "CREATE TABLE user_layers.stat_by_name AS SELECT properties->>'name' AS name, "
    "count(*) AS cnt FROM user_layers.saved_parks GROUP BY 1",
]

BAD = [
    # DML/DDL 伪装 CTAS：制作仅派生新表，一切直接写语句全拒
    ("INSERT INTO user_layers.x SELECT * FROM osm_pois", "仅允许 CREATE TABLE"),
    ("UPDATE user_layers.x SET name='a'", "仅允许 CREATE TABLE"),
    ("DELETE FROM user_layers.x", "仅允许 CREATE TABLE"),
    ("DROP TABLE user_layers.x", "仅允许 CREATE TABLE"),
    ("ALTER TABLE user_layers.x ADD COLUMN c int", "仅允许 CREATE TABLE"),
    ("TRUNCATE TABLE user_layers.x", "仅允许 CREATE TABLE"),
    ("CREATE INDEX idx ON user_layers.x (geom)", "仅允许 CREATE TABLE"),
    # 非 CTAS 的建表（列定义式，无 AS SELECT）
    ("CREATE TABLE user_layers.x (a int)", "仅允许 CREATE TABLE"),
    # 目标表名非法：大写 / 保留名 / 系统前缀
    ("CREATE TABLE user_layers.EvilName AS SELECT 1 AS n", "非法"),
    ("CREATE TABLE user_layers.registry AS SELECT 1 AS n", "非法"),
    ("CREATE TABLE user_layers.pg_tables AS SELECT 1 AS n", "非法"),
    ("CREATE TABLE user_layers.sql_x AS SELECT 1 AS n", "非法"),
    ("CREATE TABLE user_layers.1abc AS SELECT 1 AS n", "解析失败"),
    # 目标 schema 必须显式 user_layers
    ("CREATE TABLE public.x AS SELECT 1 AS n", "schema"),
    ("CREATE TABLE x AS SELECT 1 AS n", "schema"),
    # 多语句
    ("CREATE TABLE user_layers.a AS SELECT 1 AS n; DROP TABLE user_layers.a", "单条"),
    # 内层 SELECT 违规：危险函数 / 白名单外表 / CTE 内写 / 引用非法图层名
    ("CREATE TABLE user_layers.x AS SELECT pg_sleep(10)", "内层"),
    ("CREATE TABLE user_layers.x AS SELECT * FROM pg_tables", "内层"),
    ("CREATE TABLE user_layers.x AS SELECT * FROM evil_src", "内层"),
    # 内层 CTE 写：被整树写节点扫描先拒（防御纵深，消息含"数据修改"）
    ("CREATE TABLE user_layers.x AS WITH t AS (DELETE FROM osm_pois RETURNING *) "
     "SELECT * FROM t", "数据修改"),
    ("CREATE TABLE user_layers.x AS SELECT * FROM user_layers.pg_evil", "非法"),
]


@pytest.mark.parametrize("sql", GOOD)
def test_make_good_sql_passes(sql):
    ok, reason = validate_make(sql)
    assert ok, reason


@pytest.mark.parametrize("sql,expect", BAD)
def test_make_bad_sql_rejected(sql, expect):
    ok, reason = validate_make(sql)
    assert not ok and expect in reason, f"{sql} -> {reason}"


# ---------- run_make_task / drop_maker_layer ----------

LAYER = "test_m3_layer"
CTAS = json.dumps({
    "table_name": LAYER,
    "sql": f"CREATE TABLE user_layers.{LAYER} AS SELECT name, geom FROM osm_pois LIMIT 20",
    "label": "M3测试图层",
}, ensure_ascii=False)


def _admin_connect(cfg: Config):
    return psycopg.connect(host=cfg.db_host, port=cfg.db_port, dbname=cfg.db_name,
                           user=cfg.admin_user, password=cfg.admin_password)


@pytest.fixture()
def clean_layer():
    """测试前后幂等清理，防失败残留污染后续用例。"""
    drop_maker_layer(LAYER, Config())
    yield
    drop_maker_layer(LAYER, Config())


def test_run_make_task_llm_output_not_json(monkeypatch):
    """LLM 输出非 JSON：直接失败，不触碰数据库。"""
    monkeypatch.setattr("aigis.make._cached_schema", lambda cfg: "SCHEMA")
    provider = MagicMock()
    provider.generate.return_value = "我不会写 SQL"
    out = run_make_task("x", Config(), provider=provider)
    assert not out.ok and not out.table_name and "解析" in out.error


@pytest.mark.integration
def test_run_make_task_creates_registers_and_samples(clean_layer):
    """端到端：mock provider 返回合法 CTAS → 真库建表 → registry 注册 → 采样返回。"""
    provider = MagicMock()
    provider.generate.return_value = CTAS
    out = run_make_task("做一个测试图层", Config(), provider=provider)
    assert out.ok, out.error
    assert out.table_name == LAYER
    assert out.label == "M3测试图层"
    assert out.feature_count == 20
    assert out.geojson and len(out.geojson["features"]) == 20
    with _admin_connect(Config()) as conn, conn.cursor() as cur:
        cur.execute("SELECT label, feature_count FROM user_layers.registry "
                    "WHERE layer_name=%s", (LAYER,))
        row = cur.fetchone()
        assert row == ("M3测试图层", 20)
        cur.execute("SELECT to_regclass(%s)", (f"user_layers.{LAYER}",))
        assert cur.fetchone()[0] is not None


@pytest.mark.integration
def test_run_make_task_bad_sql_not_created_not_registered(clean_layer):
    """BAD provider：校验拦下 → 不建表、不注册。"""
    provider = MagicMock()
    provider.generate.return_value = json.dumps({
        "table_name": LAYER, "sql": "DROP TABLE user_layers.registry", "label": "x"})
    out = run_make_task("x", Config(), provider=provider)
    assert not out.ok and "校验" in out.error
    with _admin_connect(Config()) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (f"user_layers.{LAYER}",))
        assert cur.fetchone()[0] is None
        cur.execute("SELECT count(*) FROM user_layers.registry WHERE layer_name=%s",
                    (LAYER,))
        assert cur.fetchone()[0] == 0


@pytest.mark.integration
def test_run_make_task_duplicate_name_errors(clean_layer):
    """表已存在：转中文提示换名，不抛异常。"""
    provider = MagicMock()
    provider.generate.return_value = CTAS
    assert run_make_task("x", Config(), provider=provider).ok
    out2 = run_make_task("x", Config(), provider=provider)
    assert not out2.ok and "已存在" in out2.error and "换" in out2.error


@pytest.mark.integration
def test_drop_maker_layer_removes_table_and_registry(clean_layer):
    provider = MagicMock()
    provider.generate.return_value = CTAS
    assert run_make_task("x", Config(), provider=provider).ok
    ok, err = drop_maker_layer(LAYER, Config())
    assert ok, err
    with _admin_connect(Config()) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (f"user_layers.{LAYER}",))
        assert cur.fetchone()[0] is None
        cur.execute("SELECT count(*) FROM user_layers.registry WHERE layer_name=%s",
                    (LAYER,))
        assert cur.fetchone()[0] == 0


@pytest.mark.integration
def test_drop_nonexistent_layer_is_idempotent():
    ok, err = drop_maker_layer("test_m3_nothing", Config())
    assert ok, err


@pytest.mark.parametrize("bad", ["pg_evil", "registry", "Evil", "1x",
                                 "a; DROP TABLE x", "", "a" * 49])
def test_drop_rejects_bad_names_without_db(bad):
    """非法表名在连接数据库前即被拒（正则同制作校验）。"""
    ok, err = drop_maker_layer(bad, Config())
    assert not ok and "非法" in err


# ---------- M3 补强：图层清单注入 + 失败回喂重试 ----------

def test_make_prompt_injects_existing_layers(monkeypatch):
    """prompt user 消息注入已有图层清单（叠加制作的引用依据）。"""
    monkeypatch.setattr("aigis.make._cached_schema", lambda cfg: "SCHEMA")
    monkeypatch.setattr("aigis.make._existing_layers_text",
                        lambda cfg: "已有图层: parks_buf(20要素)\n")
    provider = MagicMock()
    provider.generate.return_value = "不是 JSON"
    run_make_task("叠加图层", Config(), provider=provider)
    user_msg = provider.generate.call_args[0][1]
    assert "已有图层: parks_buf(20要素)" in user_msg


def test_run_make_task_exhausts_retries(monkeypatch):
    """始终非法：按 max_retries 停止重试，错误为最后一次回喂信息，不连库执行。"""
    monkeypatch.setattr("aigis.make._cached_schema", lambda cfg: "SCHEMA")
    monkeypatch.setattr("aigis.make._existing_layers_text", lambda cfg: "")
    provider = MagicMock()
    provider.generate.return_value = json.dumps(
        {"table_name": LAYER, "sql": "DROP TABLE user_layers.registry", "label": "x"})
    out = run_make_task("x", Config(), provider=provider, max_retries=2)
    assert not out.ok and provider.generate.call_count == 2
    assert "校验未通过" in out.error


@pytest.mark.integration
def test_run_make_task_retry_recovers(clean_layer):
    """回喂重试生效：第 1 次校验失败 → 第 2 次合法 CTAS → 端到端成功。"""
    provider = MagicMock()
    provider.generate.side_effect = [
        json.dumps({"table_name": LAYER, "sql": "DROP TABLE user_layers.registry",
                    "label": "x"}),
        CTAS]
    out = run_make_task("x", Config(), provider=provider)
    assert out.ok, out.error
    assert provider.generate.call_count == 2
    second_user = provider.generate.call_args_list[1][0][1]
    assert "上次尝试失败" in second_user and "校验未通过" in second_user


@pytest.mark.integration
def test_existing_layers_text_lists_registry(clean_layer):
    """真库 registry 注册后，清单文本含 图层名(N要素) 格式。"""
    provider = MagicMock()
    provider.generate.return_value = CTAS
    assert run_make_task("x", Config(), provider=provider).ok
    assert f"{LAYER}(20要素)" in _existing_layers_text(Config())


# ---------- save_geojson_layer（M5：前端保存临时图层） ----------

@pytest.mark.parametrize("name,geojson,expect", [
    ("Bad!", {"features": []}, "非法"),                  # 表名非法：连库前即拒
    ("ok_name", {"features": []}, "没有带几何"),         # 空 FeatureCollection
    ("ok_name", {"features": [{"properties": {"a": 1}}]}, "没有带几何"),  # 无 geometry
])
def test_save_geojson_layer_validates_without_db(name, geojson, expect):
    ok, err, count = save_geojson_layer(name, "x", geojson, Config())
    assert not ok and count == 0 and expect in err
