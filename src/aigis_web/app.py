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
from aigis.llm import LLMError, make_provider, summarize
from aigis.make import (drop_maker_layer, run_make_task, save_geojson_layer,
                        valid_layer_name)
from aigis.repair import run_query, run_query_stream
from aigis.scenarios import match_scenario
from aigis_web import store
from aigis_web import settings_store
from aigis_web.schemas import (LLMSettingsRequest, ProviderRequest,
                                QueryRequest, QueryResponse, SaveLayerRequest,
                                SessionCreate, SessionRename, VerifyRequest)
import psycopg

# 意图路由（spec §4）：命中制作关键词 → 转制作流；命中场景关键词 → 场景规划流；
# 其余走查询流。均不引入 LLM 分类（YAGNI），场景正则见 aigis.scenarios 各模块
# "新建.{0,6}图层"：放宽到"新建一个/新建一个XX图层"类间隔表述（M4 疑虑 1）
_MAKE_INTENT_RE = re.compile(
    r"做成|生成.{0,6}图层|保存为图层|新建.{0,6}图层|缓冲.{0,8}图层|叠加.{0,4}图层|合并.{0,6}图层")


def _db_error_message(e: psycopg.OperationalError) -> str:
    # QueryCanceled(57014, statement_timeout) 先于连接失败文案区分
    if getattr(e, "sqlstate", None) == "57014":
        return "查询超时（15秒限制），请缩小查询范围或简化条件"
    return f"数据库连接失败，请确认 aigis-postgis 容器在运行：{e}"


def _to_response(out) -> QueryResponse:
    return QueryResponse(sql=out.sql, reasoning=out.reasoning, attempts=out.attempts,
                         ok=out.ok, row_count=len(out.rows), columns=out.columns,
                         sample_rows=[[str(v) for v in row] for row in out.rows[:10]],
                         geojson=out.geojson, error=out.error,
                         chat_mode=out.chat_mode, answer=out.answer)


def _summary_sample(rows) -> list[list[str]]:
    """summarize 用前 5 行样本（单元格 str 化）。"""
    return [[str(v) for v in row] for row in rows[:5]]


# ---------- 混合记忆（会话上下文：摘要前缀 + 最近 N 轮完整对话） ----------
# 轮数/单条截断长度来自 config.toml [session] 段（模型配置界面可调）


def _record(sid: str | None, role: str, content: str, meta: dict | None = None) -> None:
    """记忆层写库失败不得影响主流程（worker 线程异常会污染 SSE 事件流）。"""
    if not sid or not content:
        return
    try:
        store.append_message(sid, role, content, meta)
    except Exception:
        pass


def _history_line(m: dict, limit: int = 200) -> str:
    """单条消息 → history 行；assistant 空 content 时回落 meta（图层/SQL）。"""
    if m["role"] == "user":
        return f"用户：{m['content'][:limit]}"
    meta = m.get("meta") or {}
    if m["content"]:
        text = m["content"][:limit]
    elif meta.get("table_name"):
        text = f"已创建图层{meta['table_name']}"
    elif meta.get("sql"):
        text = meta["sql"][:limit]
    else:
        text = "已回复"
    return f"助手：{text}"


def _merge_summary(sid: str, old_summary: str, msgs: list[dict], cfg: Config) -> str | None:
    """窗口外未摘要消息并入既有摘要（LLM 增量合并）；失败静默，下次请求再试。"""
    dialog = "".join(f"{_history_line(m, cfg.history_char_limit)}\n" for m in msgs)
    try:
        merged = make_provider(cfg).generate(
            "你是会话摘要助手，输出精炼的中文摘要。",
            "把以下对话要点并入既有摘要，输出不超过150字的中文摘要："
            f"既有摘要:{old_summary}\n新增对话:{dialog}").strip()
        if not merged:
            return None
    except Exception:
        return None
    store.set_summary(sid, merged)
    store.mark_summarized(sid, [m["id"] for m in msgs])
    return merged


def _build_history(sid: str, cfg: Config, allow_summary: bool) -> str | None:
    """混合记忆组装：摘要前缀 + 最近 RECENT_N 轮完整对话（summarized=0 尾部窗口）。

    allow_summary=True（SSE 主路径）时窗口外未摘要消息 ≥4 条触发增量摘要合并；
    False（同步端点轻量）只取窗口不合并。会话无内容返回 None（不注入上下文）。
    """
    session = store.get_session(sid)
    if session is None:
        return None
    pending = [m for m in store.get_messages(sid) if not m["summarized"]]
    keep, outside = pending[-cfg.history_rounds * 2:], pending[:-cfg.history_rounds * 2]
    summary = session["summary"]
    if allow_summary and len(outside) >= 4:
        summary = _merge_summary(sid, summary, outside, cfg) or summary
    parts = ([f"历史摘要：{summary}"] if summary else []) + \
            [_history_line(m, cfg.history_char_limit) for m in keep]
    return "\n".join(parts) if parts else None


def create_app() -> FastAPI:
    app = FastAPI(title="AI-GIS 操作台")
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"],
                       allow_methods=["*"], allow_headers=["*"])

    @app.post("/api/query")
    def query(req: QueryRequest):
        question = req.question.strip()
        if not question:
            return JSONResponse(status_code=422, content={"error": "问题不能为空"})
        history = None
        if req.session_id:
            if store.get_session(req.session_id) is None:
                return JSONResponse(status_code=404,
                                    content={"error": f"会话 {req.session_id} 不存在"})
            # 同步端点轻量：组装窗口但不做摘要触发
            history = _build_history(req.session_id, load_config(), allow_summary=False)
            _record(req.session_id, "user", question)
        try:
            cfg = load_config()
            out = run_query(question, cfg, history=history)
        except LLMError as e:
            _record(req.session_id, "assistant", str(e))
            return JSONResponse(status_code=502, content=QueryResponse(error=str(e)).model_dump())
        except psycopg.OperationalError as e:
            _record(req.session_id, "assistant", _db_error_message(e))
            return JSONResponse(status_code=502,
                content=QueryResponse(error=_db_error_message(e)).model_dump())
        resp = _to_response(out)
        # chat 模式 answer 已由引擎填好，跳过 summarize；总结失败时 summarize 自行降级为模板文本
        if out.ok and not out.chat_mode:
            resp.answer = "".join(summarize(question, out.columns,
                                            _summary_sample(out.rows), len(out.rows), cfg))
        _record(req.session_id, "assistant", resp.answer or out.error,
                {"sql": out.sql, "chat_mode": out.chat_mode, "row_count": len(out.rows)})
        return resp

    # ---------- 会话（混合记忆载体）端点 ----------

    @app.get("/api/sessions")
    def sessions_list():
        return store.list_sessions()

    @app.post("/api/sessions", status_code=201)
    def sessions_create(req: SessionCreate):
        return store.create_session(req.title.strip())

    @app.patch("/api/sessions/{sid}")
    def sessions_rename(sid: str, req: SessionRename):
        title = req.title.strip()
        if not title:
            return JSONResponse(status_code=400, content={"error": "标题不能为空"})
        if not store.rename_session(sid, title):
            return JSONResponse(status_code=404, content={"error": f"会话 {sid} 不存在"})
        return store.get_session(sid)

    @app.delete("/api/sessions/{sid}")
    def sessions_delete(sid: str):
        if not store.delete_session(sid):
            return JSONResponse(status_code=404, content={"error": f"会话 {sid} 不存在"})
        return {"deleted": sid}

    @app.get("/api/sessions/{sid}/messages")
    def session_messages(sid: str):
        if store.get_session(sid) is None:
            return JSONResponse(status_code=404, content={"error": f"会话 {sid} 不存在"})
        return store.get_messages(sid)

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
            # 前端保存的图层属性整存于 properties jsonb 列，取出后呈
            # {"properties": {...}} 嵌套——仅当属性恰好只有这一个键且为 dict 时上提
            # （CTAS 制作表列名几乎不可能恰为此形态，影响面可忽略）
            if (set(f["properties"]) == {"properties"}
                    and isinstance(f["properties"]["properties"], dict)):
                f["properties"] = f["properties"]["properties"]
        return gj

    @app.post("/api/layers/save")
    def save_layer(req: SaveLayerRequest):
        """临时查询结果 → 持久图层（spec §3）：建表+参数化批量 INSERT 走
        make.save_geojson_layer（管理账号，仅动 user_layers schema）。"""
        name = req.name.strip()
        label = req.label.strip() or name
        if not valid_layer_name(name):
            return JSONResponse(status_code=400, content={
                "error": "图层名非法：需小写字母开头、仅含小写字母/数字/下划线，长度 1-48"})
        feats = req.geojson.get("features") if isinstance(req.geojson, dict) else None
        if (not isinstance(feats, list)
                or not any(isinstance(f, dict) and f.get("geometry") for f in feats)):
            return JSONResponse(status_code=400,
                                content={"error": "geojson 中没有带几何的要素"})
        try:
            cfg = load_config()
            ok, err, count = save_geojson_layer(name, label, req.geojson, cfg)
        except psycopg.OperationalError as e:
            return JSONResponse(status_code=502, content={"error": _db_error_message(e)})
        if not ok:  # 已存在→409；非法/空要素（预检遗漏形态）→400；库级错误→500
            status = (409 if "已存在" in err
                      else 400 if ("非法" in err or "没有" in err) else 500)
            return JSONResponse(status_code=status, content={"error": err})
        return {"layer_name": name, "label": label, "feature_count": count}

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

    # ---------- 模型配置（运维界面）端点 ----------

    def _settings_error(e: Exception, status: int = 404) -> JSONResponse:
        return JSONResponse(status_code=status, content={"error": str(e)})

    @app.get("/api/settings")
    def settings_get():
        """供应商列表（api_key 脱敏）+ 激活标记 + config.toml 运行参数 + .env 现值。"""
        return {"providers": settings_store.list_providers(),
                "active_id": settings_store.get_active_id(),
                "llm": settings_store.read_llm_settings()}

    @app.post("/api/settings/llm")
    def settings_llm(req: LLMSettingsRequest):
        """config.toml [llm]/[session] 段合并写入（未传键不动），下一请求即生效。"""
        if req.thinking is not None and req.thinking not in settings_store.THINKING_LEVELS:
            return JSONResponse(status_code=422, content={
                "error": f"thinking 取值需为 {'/'.join(settings_store.THINKING_LEVELS)}"})
        return settings_store.write_llm_settings(req.model_dump(exclude_none=True))

    @app.post("/api/providers")
    def provider_upsert(req: ProviderRequest):
        name = req.name.strip()
        if not name or not req.base_url.strip() or not req.model.strip():
            return JSONResponse(status_code=422,
                                content={"error": "名称 / Base URL / 模型不能为空"})
        if req.verify:
            plain = req.api_key if req.api_key and not req.api_key.startswith("***") else (
                settings_store.get_provider_plain(req.id)["api_key"]
                if req.id else "")
            ok, msg = settings_store.verify_connection(
                req.base_url.strip(), plain, req.model.strip())
            if not ok:
                return JSONResponse(status_code=400, content={
                    "error": f"验证失败，未保存：{msg}"})
        try:
            return settings_store.upsert_provider(
                req.id, name, req.base_url.strip(), req.api_key, req.model.strip())
        except KeyError as e:
            return _settings_error(e)

    @app.post("/api/providers/verify")
    def provider_verify(req: VerifyRequest):
        """即时连接验证（不落盘）：带 id 按存档明文验证，否则按表单值验证。"""
        if req.id:
            try:
                p = settings_store.get_provider_plain(req.id)
            except KeyError as e:
                return _settings_error(e)
            ok, msg = settings_store.verify_connection(
                p["base_url"], p["api_key"], p["model"])
            return {"ok": ok, "message": msg}
        if not req.base_url.strip() or not req.model.strip():
            return JSONResponse(status_code=422,
                                content={"error": "Base URL / 模型不能为空"})
        ok, msg = settings_store.verify_connection(
            req.base_url.strip(), req.api_key, req.model.strip())
        return {"ok": ok, "message": msg}

    @app.post("/api/providers/{pid}/activate")
    def provider_activate(pid: str):
        try:
            return settings_store.activate_provider(pid)
        except KeyError as e:
            return _settings_error(e)

    @app.delete("/api/providers/{pid}")
    def provider_delete(pid: str):
        try:
            settings_store.delete_provider(pid)
        except KeyError as e:
            return _settings_error(e)
        return {"deleted": pid}

    @app.get("/api/query/stream")
    def query_stream(q: str, session_id: str | None = None):
        question = q.strip()
        if not question:
            return JSONResponse(status_code=422, content={"error": "问题不能为空"})
        history = None
        if session_id:
            if store.get_session(session_id) is None:
                return JSONResponse(status_code=404,
                                    content={"error": f"会话 {session_id} 不存在"})
            # 混合记忆组装 + 摘要合并（同步低频：窗口外 ≥4 条未摘要才触发 LLM）
            history = _build_history(session_id, load_config(), allow_summary=True)
            _record(session_id, "user", question)

        def sse(event: str, data) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        def record(content: str, meta: dict | None = None) -> None:
            _record(session_id, "assistant", content, meta)

        def gen():
            events: queue.Queue = queue.Queue()

            def query_worker():
                try:
                    cfg = load_config()
                    out = run_query_stream(question, cfg, history=history,
                                           on_delta=lambda t: events.put(("delta", {"text": t})),
                                           on_status=lambda s: events.put(("status", {"stage": s})))
                    resp = _to_response(out)
                    if out.ok and not out.chat_mode:
                        # 总结在 worker 线程内 result 前同线程执行；chat 模式 answer 已有则跳过
                        events.put(("status", {"stage": "总结中"}))
                        parts: list[str] = []
                        for token in summarize(question, out.columns,
                                               _summary_sample(out.rows),
                                               len(out.rows), cfg):
                            parts.append(token)
                            events.put(("answer_delta", {"text": token}))
                        resp.answer = "".join(parts)
                    record(resp.answer or out.error,
                           {"sql": out.sql, "chat_mode": out.chat_mode,
                            "row_count": len(out.rows)})
                    events.put(("result", resp.model_dump()))
                except LLMError as e:
                    record(str(e))
                    events.put(("error", {"error": str(e)}))
                except psycopg.OperationalError as e:
                    record(_db_error_message(e))
                    events.put(("error", {"error": _db_error_message(e)}))
                except Exception as e:  # 后台线程异常不可见，必须有出口
                    record(f"内部错误：{e}")
                    events.put(("error", {"error": f"内部错误：{e}"}))
                finally:
                    events.put(None)  # 流结束哨兵

            def make_worker():
                try:
                    cfg = load_config()
                    out = run_make_task(question, cfg, history=history,
                                        on_delta=lambda t: events.put(("delta", {"text": t})),
                                        on_status=lambda s: events.put(("status", {"stage": s})))
                    if not out.ok:
                        # 误判兜底（spec §4）：提示可用查询表述重试
                        record(out.error)
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
                    record(resp.answer, {"sql": out.sql, "table_name": out.table_name,
                                         "row_count": out.feature_count})
                    events.put(("result", resp.model_dump()))
                except LLMError as e:
                    record(str(e))
                    events.put(("error", {"error": str(e)}))
                except psycopg.OperationalError as e:
                    record(_db_error_message(e))
                    events.put(("error", {"error": _db_error_message(e)}))
                except Exception as e:
                    record(f"内部错误：{e}")
                    events.put(("error", {"error": f"内部错误：{e}"}))
                finally:
                    events.put(None)

            def scenario_worker(scenario):
                try:
                    cfg = load_config()
                    out = scenario.run(question, cfg, history=history,
                                       on_delta=lambda t: events.put(("delta", {"text": t})),
                                       on_status=lambda s: events.put(("status", {"stage": s})))
                    if not out.ok:
                        record(out.error)
                        events.put(("error", {"error": (
                            f"{out.error}\n场景规划未成功；若想查询数据而非规划，"
                            "请改用查询表述（如「三环内有多少公园」）重新提问")}))
                        return
                    resp = QueryResponse(ok=True, row_count=out.row_count, geojson=out.geojson,
                                         answer=out.answer,
                                         scenario={"type": out.scenario_type,
                                                   "title": out.title, "cards": out.cards},
                                         layer_style=out.layer_style)
                    record(out.answer, {"scenario": resp.scenario, "row_count": out.row_count,
                                        "layer_style": out.layer_style})
                    events.put(("result", resp.model_dump()))
                except LLMError as e:
                    record(str(e))
                    events.put(("error", {"error": str(e)}))
                except psycopg.OperationalError as e:
                    record(_db_error_message(e))
                    events.put(("error", {"error": _db_error_message(e)}))
                except Exception as e:
                    record(f"内部错误：{e}")
                    events.put(("error", {"error": f"内部错误：{e}"}))
                finally:
                    events.put(None)

            is_make = _MAKE_INTENT_RE.search(question) is not None
            scenario = None if is_make else match_scenario(question)
            if is_make:
                worker = make_worker
            elif scenario is not None:
                worker = lambda: scenario_worker(scenario)  # noqa: E731 闭包绑定当前场景
            else:
                worker = query_worker
            first_stage = ("理解制作需求" if is_make
                           else scenario.label if scenario is not None else "理解问题")
            threading.Thread(target=worker, daemon=True).start()
            yield sse("status", {"stage": first_stage})
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
