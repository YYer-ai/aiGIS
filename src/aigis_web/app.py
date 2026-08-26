from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from aigis.config import load_config
from aigis.llm import LLMError
from aigis.repair import run_query
from aigis_web.schemas import QueryRequest, QueryResponse
import psycopg

def create_app() -> FastAPI:
    app = FastAPI(title="AI-GIS 操作台")
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"],
                       allow_methods=["*"], allow_headers=["*"])

    @app.post("/api/query")
    def query(req: QueryRequest):
        if not req.question.strip():
            return JSONResponse(status_code=422, content={"error": "问题不能为空"})
        try:
            out = run_query(req.question.strip(), load_config())
        except LLMError as e:
            return JSONResponse(status_code=502, content=QueryResponse(error=str(e)).model_dump())
        except psycopg.OperationalError as e:
            return JSONResponse(status_code=502,
                content=QueryResponse(error=f"数据库连接失败，请确认 aigis-postgis 容器在运行：{e}").model_dump())
        return QueryResponse(sql=out.sql, reasoning=out.reasoning, attempts=out.attempts,
                             ok=out.ok, row_count=len(out.rows), columns=out.columns,
                             geojson=out.geojson, error=out.error)

    dist = Path(__file__).resolve().parent.parent.parent / "web" / "dist"
    if dist.exists():
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=dist, html=True), name="static")
    return app

app = create_app()
