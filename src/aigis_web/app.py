import json
import queue
import threading
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from aigis.config import load_config
from aigis.llm import LLMError, summarize
from aigis.repair import run_query, run_query_stream
from aigis_web.schemas import QueryRequest, QueryResponse
import psycopg


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

    @app.get("/api/query/stream")
    def query_stream(q: str):
        question = q.strip()
        if not question:
            return JSONResponse(status_code=422, content={"error": "问题不能为空"})

        def sse(event: str, data) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        def gen():
            events: queue.Queue = queue.Queue()

            def worker():
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

            threading.Thread(target=worker, daemon=True).start()
            yield sse("status", {"stage": "理解问题"})
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
