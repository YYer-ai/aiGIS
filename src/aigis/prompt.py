"""提示词组装：SYSTEM_TEMPLATE + few-shot 库 + schema/user 消息拼装（OpenAI messages 格式）。"""
import json
import os
from pathlib import Path

import yaml

SYSTEM_TEMPLATE = """你是空间查询专家，把中文问题转成一条 PostGIS SQL。
规则：
1. 只输出一个 JSON 对象：{{"sql": "...", "reasoning": "简短中文说明"}}（等价于 mode 取默认值 query，可省略），不要多余文本。
2. 优先判断：若问题所需数据不在 schema 中（实时交通/天气/主观感受/闲聊/与你能力无关），必须直接输出 {{"mode":"chat","reply":"..."}}，禁止编造 SQL 硬答；reply 要直接回答或说明数据局限并给出可查的替代建议。
3. 仅单条 SELECT；只能用 schema 中列出的表和列。
4. 几何一律用 ST_AsGeoJSON(geom) AS geometry 输出为 GeoJSON。
5. 面积计算用 geography 强转（米制）：ST_Area(geom::geography)。
6. "N环内" 用 ring_areas 表 ST_Contains；距离用 ST_DWithin(geom::geography)。
7. 地名模糊匹配用 name LIKE '%关键词%'。
以下是参考样例（中文问题 → SQL 或 chat 回复）：
{fewshot}"""


def _fewshot_path() -> Path:
    """few-shot 默认路径：AIGIS_ROOT/data（wheel 场景），否则仓库根 data/fewshot.yaml。

    src/aigis 上三级到仓库根，可编辑安装下成立。
    """
    if root := os.environ.get("AIGIS_ROOT"):
        return Path(root) / "data" / "fewshot.yaml"
    return Path(__file__).resolve().parent.parent.parent / "data" / "fewshot.yaml"


def load_fewshot(path: str | None = None) -> list[dict]:
    """加载 few-shot 样例（键：question, sql 或 question, mode, reply）。"""
    p = Path(path) if path else _fewshot_path()
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def render_fewshot(shots: list[dict]) -> str:
    """渲染 few-shot：SQL 条目 → 问/SQL；chat 条目 → 问/答（chat JSON 示例）。"""
    lines = []
    for s in shots:
        if s.get("mode") == "chat":
            example = json.dumps({"mode": "chat", "reply": s["reply"]},
                                 ensure_ascii=False, separators=(",", ":"))
            lines.append(f"问：{s['question']}\n答（chat）：{example}")
        else:
            lines.append(f"问：{s['question']}\nSQL：{s['sql']}")
    return "\n".join(lines)


def build_messages(question: str, schema_text: str) -> list[dict]:
    """拼装 OpenAI messages：system（规则+few-shot）+ user（schema+问题）。"""
    fewshot = render_fewshot(load_fewshot())
    system = SYSTEM_TEMPLATE.format(fewshot=fewshot)
    user = f"数据库 schema（含中文注释与样本值）：\n{schema_text}\n\n问题：{question}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
