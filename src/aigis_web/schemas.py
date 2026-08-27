from pydantic import BaseModel

class QueryRequest(BaseModel):
    question: str

class QueryResponse(BaseModel):
    sql: str = ""
    reasoning: str = ""
    attempts: int = 0
    ok: bool = False
    row_count: int = 0
    columns: list[str] = []
    sample_rows: list[list[str]] = []  # 前 10 行（单元格 repr），前端表格展示
    geojson: dict | None = None
    error: str = ""
    answer: str = ""  # 自然语言回答（LLM 总结失败时为模板文本，如"查询完成，共 N 行结果。"）
