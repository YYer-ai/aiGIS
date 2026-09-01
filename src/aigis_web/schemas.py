from pydantic import BaseModel

class QueryRequest(BaseModel):
    question: str

class SaveLayerRequest(BaseModel):
    name: str          # 表名（小写字母开头，仅小写字母/数字/下划线）
    label: str = ""    # 中文图层名，缺省回落到 name
    geojson: dict      # 前端临时图层的 FeatureCollection

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
