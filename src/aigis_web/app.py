import json
import queue
import re
import threading
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from psycopg import sql as pgsql
from aigis.config import Config, load_config
from aigis.geojson_out import rows_to_geojson
from aigis.llm import LLMError, summarize
from aigis.make import drop_maker_layer, run_make_task, valid_layer_name
from aigis.repair import run_query, run_query_stream
from aigis_web.schemas import QueryRequest, QueryResponse
import psycopg

# 意图路由（spec §4）：命中制作关键词 → 转制作流；不引入 LLM 分类（YAGNI）
_MAKE_INTENT_RE = re.compile(
    r"做成|生成.{0,6}图层|保存为图层|新建图层|缓冲.{0,8}图层|叠加.{0,4}图层|合并.{0,6}图层")


def _db_error_message(e: psycopg.OperationalError) -> str:
    # QueryCanceled(57014, statement_timeout) 先于连接失败文案区分
    if getattr(e, "sqlstate", None) == "57014":
        return "查询超时（15秒限制），请缩小查询范围或简化条件"
    return f"数据库连接失败，请确认 aigis-postgis 容器在运行：{e}"


def _to_response(out) -> QueryResponse:
    return QueryResponse(sql=out.sql, reasoning=out.reasoning, attempts=out.attempts,
                         ok=out.ok, row_count=len(out.rows), columns=out.columns,
                         sample_rows=[[str(v) for v in row] for row in out.rows[:10]],
                         geojson=out.geojson, error=out.error)


def _summary_sample(rows) -> list[list[str]]:
    """summarize 用前 5 行样本（单元格 str 化）。"""
    return [[str(v) for v in row] for row in rows[:5]]


def create_app() -> FastAPI:
    app = FastAPI(title="AI-GIS 操作台")
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"],
                       allow_methods=["*"], allow_headers=["*"])

    @app.post("/api/query")
    def query(req: QueryRequest):
        if not req.question.strip():
            return JSONResponse(status_code=422, content={"error": "问题不能为空"})
        try:
            cfg = load_config()
            out = run_query(req.question.strip(), cfg)
        except LLMError as e:
            return JSONResponse(status_code=502, content=QueryResponse(error=str(e)).model_dump())
        except psycopg.OperationalError as e:
            return JSONResponse(status_code=502,
                content=QueryResponse(error=_db_error_message(e)).model_dump())
        resp = _to_response(out)
        if out.ok:  # 回答生成失败时 summarize 自行降级为模板文本，不影响主流程
            resp.answer = "".join(summarize(req.question.strip(), out.columns,
                                            _summary_sample(out.rows), len(out.rows), cfg))
        return resp

    # ---------- 图层库（registry）端点 ----------

    def _admin_connect(cfg: Config):
        """registry 归管理账号所有（建表脚本只授了 maker），列表/存在性查询走管理通道。"""
        return psycopg.connect(host=cfg.db_host, port=cfg.db_port, dbname=cfg.db_name,
                               user=cfg.admin_user, password=cfg.admin_password)

    def _readonly_connect(cfg: Config):
        """图层表数据读取走只读通道（建表 default privileges 已授 readonly SELECT）。"""
        return psycopg.connect(host=cfg.db_host, port=cfg.db_port, dbname=cfg.db_name,
                               user=cfg.db_user, password=cfg.db_password,
                               options="-c statement_timeout=15000")

    def _layer_registered(cur, name: str) -> bool:
        """registry 存在性（参数化查询，Mimosa 约束：外部输入参数绑定）。"""
        cur.execute("SELECT 1 FROM user_layers.registry WHERE layer_name = %s", (name,))
        return cur.fetchone() is not None

    @app.get("/api/layers")
    def list_layers():
        try:
            cfg = load_config()
            with _admin_connect(cfg) as conn, conn.cursor() as cur:
                cur.execute("SELECT layer_name, label, feature_count, created_at "
                            "FROM user_layers.registry ORDER BY created_at DESC")
                rows = cur.fetchall()
        except psycopg.OperationalError as e:
            return JSONResponse(status_code=502, content={"error": _db_error_message(e)})
        return [{"layer_name": n, "label": l, "feature_count": c, "created_at": str(t)}
                for n, l, c, t in rows]

    @app.get("/api/layers/{name}/geojson")
    def layer_geojson(name: str):
        # 动态表名无法参数化（Mimosa 约束：外部输入参数绑定）——双校验后拼接：
        # 1) 表名正则（valid_layer_name：小写字母开头、仅小写字母/数字/下划线、拒保留名）；
        # 2) registry 存在性（参数化 WHERE layer_name=%s）。
        # 两者都通过后，才以固定 schema 前缀 user_layers + 校验后表名经
        # psycopg.Identifier（等价白名单字符集内的安全引用）拼入 SQL。
        if not valid_layer_name(name):
            return JSONResponse(status_code=404, content={"error": f"图层 {name} 不存在"})
        try:
            cfg = load_config()
            with _admin_connect(cfg) as conn, conn.cursor() as cur:
                if not _layer_registered(cur, name):
                    return JSONResponse(status_code=404,
                                        content={"error": f"图层 {name} 不存在"})
            with _readonly_connect(cfg) as conn, conn.cursor() as cur:
                cur.execute(pgsql.SQL(
                    "SELECT ST_AsGeoJSON(geom) AS geometry, * "
                    "FROM {} LIMIT 2000").format(
                    pgsql.Identifier("user_layers", name)))
                cols = [d.name for d in cur.description]
                gj = rows_to_geojson(cols, cur.fetchall())
        except psycopg.OperationalError as e:
            return JSONResponse(status_code=502, content={"error": _db_error_message(e)})
        except psycopg.Error as e:  # 表结构异常（如缺 geom 列）等
            return JSONResponse(status_code=500, content={"error": str(e).strip()})
        for f in gj["features"]:  # `*` 会带回原始 geom 列（WKB hex），从属性中剔除
            f["properties"].pop("geom", None)
        return gj

    @app.delete("/api/layers/{name}")
    def delete_layer(name: str):
        # 双校验同 geojson 端点：正则 + registry 存在性（参数化），drop_maker_layer 内再校验一次
        if not valid_layer_name(name):
            return JSONResponse(status_code=404, content={"error": f"图层 {name} 不存在"})
        try:
            cfg = load_config()
            with _admin_connect(cfg) as conn, conn.cursor() as cur:
                if not _layer_registered(cur, name):
                    return JSONResponse(status_code=404,
                                        content={"error": f"图层 {name} 不存在"})
        except psycopg.OperationalError as e:
            return JSONResponse(status_code=502, content={"error": _db_error_message(e)})
        ok, err = drop_maker_layer(name, cfg)
        if not ok:
            return JSONResponse(status_code=500, content={"error": err})
        return {"deleted": name}

    @app.get("/api/query/stream")
    def query_stream(q: str):
        question = q.strip()
        if not question:
            return JSONResponse(status_code=422, content={"error": "问题不能为空"})

        def sse(event: str, data) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        def gen():
            events: queue.Queue = queue.Queue()

            def query_worker():
                try:
                    cfg = load_config()
                    out = run_query_stream(question, cfg,
                                           on_delta=lambda t: events.put(("delta", {"text": t})),
                                           on_status=lambda s: events.put(("status", {"stage": s})))
                    resp = _to_response(out)
                    if out.ok:  # 总结在 worker 线程内 result 前同线程执行
                        events.put(("status", {"stage": "总结中"}))
                        parts: list[str] = []
                        for token in summarize(question, out.columns,
                                               _summary_sample(out.rows),
                                               len(out.rows), cfg):
                            parts.append(token)
                            events.put(("answer_delta", {"text": token}))
                        resp.answer = "".join(parts)
                    events.put(("result", resp.model_dump()))
                except LLMError as e:
                    events.put(("error", {"error": str(e)}))
                except psycopg.OperationalError as e:
                    events.put(("error", {"error": _db_error_message(e)}))
                except Exception as e:  # 后台线程异常不可见，必须有出口
                    events.put(("error", {"error": f"内部错误：{e}"}))
                finally:
                    events.put(None)  # 流结束哨兵

            def make_worker():
                try:
                    cfg = load_config()
                    out = run_make_task(question, cfg,
                                        on_delta=lambda t: events.put(("delta", {"text": t})),
                                        on_status=lambda s: events.put(("status", {"stage": s})))
                    if not out.ok:
                        # 误判兜底（spec §4）：提示可用查询表述重试
                        events.put(("error", {"error": (
                            f"{out.error}\n制作未成功；若想查询而非制作图层，"
                            "请改用查询表述（如「三环内有多少公园」）重新提问")}))
                        return
                    # QueryResponse 兼容结构：columns/sample_rows 约定 [layer_name, label]
                    resp = QueryResponse(
                        sql=out.sql, ok=True, row_count=out.feature_count,
                        columns=["layer_name", "label"],
                        sample_rows=[[out.table_name, out.label]], geojson=out.geojson)
                    events.put(("status", {"stage": "总结中"}))
                    parts: list[str] = []
                    for token in summarize(question, resp.columns, resp.sample_rows,
                                           out.feature_count, cfg):
                        parts.append(token)
                        events.put(("answer_delta", {"text": token}))
                    resp.answer = "".join(parts)
                    events.put(("result", resp.model_dump()))
                except LLMError as e:
                    events.put(("error", {"error": str(e)}))
                except psycopg.OperationalError as e:
                    events.put(("error", {"error": _db_error_message(e)}))
                except Exception as e:
                    events.put(("error", {"error": f"内部错误：{e}"}))
                finally:
                    events.put(None)

            is_make = _MAKE_INTENT_RE.search(question) is not None
            threading.Thread(target=make_worker if is_make else query_worker,
                             daemon=True).start()
            yield sse("status", {"stage": "理解制作需求" if is_make else "理解问题"})
            while True:
                item = events.get()
                if item is None:
                    break
                yield sse(*item)

        return StreamingResponse(gen(), media_type="text/event-stream")

    dist = Path(__file__).resolve().parent.parent.parent / "web" / "dist"
    if dist.exists():
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=dist, html=True), name="static")
    return app

app = create_app()
