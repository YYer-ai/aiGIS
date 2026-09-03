from pydantic import BaseModel

class QueryRequest(BaseModel):
    question: str
    session_id: str | None = None  # 带会话则记录消息并注入对话上下文

class SessionCreate(BaseModel):
    title: str = ""  # 空标题由首条消息自动生成（store 逻辑）

class SessionRename(BaseModel):
    title: str

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
    chat_mode: bool = False  # AI 判定无需 SQL：answer 即直接回复，无行数据
