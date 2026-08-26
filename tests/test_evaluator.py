# tests/test_evaluator.py
# 统计纯函数测试：mock run_query 返回固定 Outcome，不依赖真库/真 LLM。
from unittest.mock import MagicMock

from aigis.config import Config
from aigis.evaluator import run_eval
from aigis.repair import Outcome


def _make_run_query(outcomes_by_question: dict[str, Outcome]):
    """构造按问题文本分发的 run_query 替身。"""
    fake = MagicMock(side_effect=lambda q, cfg: outcomes_by_question[q])
    return fake


def test_run_eval_counts_exec_ok_and_repaired(monkeypatch):
    """题1 一次成功 / 题2 两次成功（计入 repaired）/ 题3 失败。"""
    outs = {
        "一次成功": Outcome(question="一次成功", ok=True, attempts=1, rows=[(42,)]),
        "修复成功": Outcome(question="修复成功", ok=True, attempts=2, rows=[(7,)]),
        "失败": Outcome(question="失败", ok=False, attempts=3, error="数据库执行错误"),
    }
    monkeypatch.setattr("aigis.evaluator.run_query", _make_run_query(outs))
    questions = [
        {"id": 1, "question": "一次成功", "expect_rows_range": [10, 100], "human_pass": None},
        {"id": 2, "question": "修复成功", "expect_rows_range": [5, 10], "human_pass": None},
        {"id": 3, "question": "失败", "expect_rows_range": [1, 5], "human_pass": None},
    ]
    rep = run_eval(questions, Config(), progress=False)
    assert rep["total"] == 3
    assert rep["exec_ok"] == 2
    assert rep["repaired"] == 1          # 仅题2（ok 且 attempts>1）
    assert [r["attempts"] for r in rep["results"]] == [1, 2, 3]
    assert rep["results"][2]["ok"] is False and rep["results"][2]["in_range"] is None


def test_run_eval_accuracy_annotated(monkeypatch):
    """准确率只统计已标注题：1 对 / 1 错 / 1 未标注 → 0.5。"""
    outs = {q: Outcome(question=q, ok=True, attempts=1, rows=[(1,)])
            for q in ("对", "错", "未标注")}
    monkeypatch.setattr("aigis.evaluator.run_query", _make_run_query(outs))
    questions = [
        {"id": 1, "question": "对", "human_pass": True},
        {"id": 2, "question": "错", "human_pass": False},
        {"id": 3, "question": "未标注", "human_pass": None},
    ]
    rep = run_eval(questions, Config(), progress=False)
    assert rep["accuracy_annotated"] == 0.5


def test_run_eval_in_range_count_vs_rows(monkeypatch):
    """count 类（单行单列）按数值判断；枚举类按行数判断；失败不判。"""
    outs = {
        "计数在区间": Outcome(question="计数在区间", ok=True, attempts=1, rows=[(120,)]),
        "计数超区间": Outcome(question="计数超区间", ok=True, attempts=1, rows=[(9999,)]),
        "枚举行数": Outcome(question="枚举行数", ok=True, attempts=1,
                           rows=[("甲", 1.0), ("乙", 2.0), ("丙", 3.0)]),
    }
    monkeypatch.setattr("aigis.evaluator.run_query", _make_run_query(outs))
    questions = [
        {"id": 1, "question": "计数在区间", "expect_rows_range": [100, 200]},
        {"id": 2, "question": "计数超区间", "expect_rows_range": [100, 200]},
        {"id": 3, "question": "枚举行数", "expect_rows_range": [1, 10]},
    ]
    rep = run_eval(questions, Config(), progress=False)
    got = {r["id"]: r["in_range"] for r in rep["results"]}
    assert got == {1: True, 2: False, 3: True}


def test_run_eval_in_range_non_numeric_cell(monkeypatch):
    """单行单列返回文本值（如 '12.3 平方公里'）：不抛异常，in_range=None（无法粗检）。"""
    outs = {"面积多少": Outcome(question="面积多少", ok=True, attempts=1, rows=[("12.3 平方公里",)])}
    monkeypatch.setattr("aigis.evaluator.run_query", _make_run_query(outs))
    questions = [{"id": 1, "question": "面积多少", "expect_rows_range": [10, 100]}]
    rep = run_eval(questions, Config(), progress=False)  # 修复前此处 TypeError
    assert rep["results"][0]["in_range"] is None


def test_run_eval_sample_rows_and_report(tmp_path, monkeypatch):
    """results 每项带 category/sample_rows（前3行、每行截120字符）；write_report 落盘含明细与统计。"""
    rows = [(f"行{i}", i * 1.5) for i in range(1, 6)]
    outs = {"多行": Outcome(question="多行", ok=True, attempts=1, rows=rows),
            "失败|带竖线": Outcome(question="失败|带竖线", ok=False, attempts=2,
                                  error="语法错误", sql="SELECT *\nFROM t | x")}
    monkeypatch.setattr("aigis.evaluator.run_query", _make_run_query(outs))
    questions = [
        {"id": 1, "category": "最近邻", "question": "多行", "expect_rows_range": [1, 3]},
        {"id": 2, "category": "地名模糊", "question": "失败|带竖线"},
    ]
    rep = run_eval(questions, Config(), progress=False)
    r = rep["results"][0]
    assert r["category"] == "最近邻"
    assert r["sample_rows"] == [repr(x)[:120] for x in rows[:3]]
    assert len(r["sample_rows"]) == 3
    assert rep["results"][1]["sample_rows"] == []

    from aigis.evaluator import write_report
    p = tmp_path / "report.md"
    write_report(rep, str(p))
    text = p.read_text(encoding="utf-8")
    assert "| id | category | ok | attempts | rows | in_range | question | SQL | 结果前3行 |" in text
    assert "总计 2 | 执行成功 1 | 经自修复 0" in text          # ok 且 attempts>1 才算 repaired
    assert "`SELECT * FROM t \\| x`" in text                  # SQL 压缩单行、竖线转义
    assert "失败\\|带竖线" in text and "行1" in text
    assert "in_range 异常题号（False）：[1]" in text           # 题1 行数5 超区间[1,3]
    assert "失败题号：[2]" in text
