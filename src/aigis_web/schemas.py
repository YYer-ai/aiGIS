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

class ProviderRequest(BaseModel):
    name: str                    # 供应商显示名（如"生产-Qwen"）
    base_url: str
    api_key: str = ""            # 掩码（***xxxx）或空 = 沿用原值；明文覆盖
    model: str
    verify: bool = False         # True：保存前先验证连接，失败不落盘
    id: str | None = None        # 带值为更新，缺省新增

class VerifyRequest(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    id: str | None = None  # 已存供应商按 id 验证（后端取库内明文，前端只见脱敏）

class LLMSettingsRequest(BaseModel):
    # config.toml [llm]/[session] 段；全部可选，未传键不改动
    thinking: str | None = None        # off/low/medium/high
    temperature: float | None = None
    timeout_seconds: float | None = None
    history_rounds: int | None = None
    history_char_limit: int | None = None

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
    scenario: dict | None = None  # 场景规划结果 {type,title,cards[,...]}（行程/选址等）
    layer_style: dict | None = None  # 场景图层默认样式（classify/gradient/label）
