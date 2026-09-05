# src/aigis/scenarios/__init__.py
"""场景规划引擎：领域场景（行程规划/选址等）= LLM 参数抽取 + 确定性 SQL/算法编排。

与查询流（repair.py 单条 NL→SQL）、制作流（make.py CTAS）并列的第三条通道：
LLM 只负责理解需求与总结，评分/排序/路线等由确定性代码完成，结果结构化
（cards 供前端场景卡渲染，geojson 走既有图层管线）。

新场景接入：实现模块级 KEYWORDS（正则列表，高置信触发）与 run(...)，注册进 REGISTRY。
"""
import re
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class ScenarioOutcome:
    ok: bool = False
    scenario_type: str = ""     # 前端卡片类型（itinerary/camping/...）
    title: str = ""             # 场景结果标题（图层名/卡片标题）
    cards: list[dict] = field(default_factory=list)   # 结构化卡片数据（随 result 与 meta 持久化）
    geojson: dict | None = None                       # 点/线/面要素（含 day/seq 等属性）
    layer_style: dict | None = None                   # 前端默认图层样式（分类设色/标注）
    answer: str = ""            # LLM 总结文本（流式推送后回填）
    params: dict = field(default_factory=dict)        # 抽取参数（日志/调试，不持久化）
    row_count: int = 0          # 要素数（QueryResponse.row_count）
    error: str = ""


# run 签名与查询/制作流对齐（provider 由调用方注入便于测试）
RunFn = Callable[..., ScenarioOutcome]


@dataclass
class Scenario:
    id: str
    label: str                  # 阶段名展示（"规划行程"/"选址评估"）
    keywords: list[re.Pattern]  # 高置信触发正则（任一命中即走本场景）
    run: RunFn

    def matches(self, question: str) -> bool:
        return any(p.search(question) for p in self.keywords)


def _build_registry() -> dict[str, Scenario]:
    # 延迟导入避免循环依赖（场景模块 import 本包的 Scenario/Outcome）
    from aigis.scenarios import camping, runride, trip
    return {
        "trip": Scenario("trip", "规划行程", trip.KEYWORDS, trip.run),
        "camping": Scenario("camping", "露营选址", camping.KEYWORDS, camping.run),
        "runride": Scenario("runride", "检索绿道", runride.KEYWORDS, runride.run),
    }


REGISTRY: dict[str, Scenario] = _build_registry()


def match_scenario(question: str) -> Scenario | None:
    """路由入口：注册表顺序优先返回命中的场景；未命中返回 None（走查询流）。"""
    for s in REGISTRY.values():
        if s.matches(question):
            return s
    return None
