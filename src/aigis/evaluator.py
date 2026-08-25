# src/aigis/evaluator.py
"""评估器：跑 eval/questions.yaml 题集，统计执行成功率 / 自修复命中率 / 人工标注准确率。"""
from pathlib import Path

import typer
import yaml

from aigis.config import load_config
from aigis.repair import run_query

app = typer.Typer(add_completion=False, help="AI-GIS 题集评估（执行成功率/自修复/人工准确率）")


def run_eval(questions: list[dict], cfg, progress: bool = True) -> dict:
    """逐题跑 run_query 并汇总统计。

    in_range：结果数值/行数落在 expect_rows_range 内（数量级粗检，仅 ok 且有行时计算；
    单行单列（count 类）取值本身，否则取行数）。
    """
    results, repaired = [], 0
    for q in questions:
        out = run_query(q["question"], cfg)
        if out.ok and out.attempts > 1:
            repaired += 1
        in_range = None
        if out.ok and q.get("expect_rows_range") and out.rows:
            lo, hi = q["expect_rows_range"]
            n = out.rows[0][0] if len(out.rows) == 1 and len(out.rows[0]) == 1 else len(out.rows)
            in_range = lo <= n <= hi
        results.append({"id": q["id"], "question": q["question"], "ok": out.ok,
                        "attempts": out.attempts, "sql": out.sql,
                        "rows": len(out.rows), "in_range": in_range,
                        "human_pass": q.get("human_pass")})
        if progress:
            typer.echo(f"[{q['id']}] {'OK' if out.ok else 'FAIL'} 尝试{out.attempts}次 行数{len(out.rows)}")
    annotated = [r for r in results if r["human_pass"] is not None]
    acc = (sum(1 for r in annotated if r["human_pass"]) / len(annotated)) if annotated else None
    return {"total": len(results), "exec_ok": sum(1 for r in results if r["ok"]),
            "repaired": repaired, "accuracy_annotated": acc, "results": results}


@app.command()
def main(only_annotated: bool = typer.Option(False, "--only-annotated",
                                             help="只跑已人工标注（human_pass 非 null）的题")):
    """跑题集并打印汇总报告。"""
    qs = yaml.safe_load(Path("eval/questions.yaml").read_text(encoding="utf-8"))
    if only_annotated:
        qs = [q for q in qs if q.get("human_pass") is not None]
    rep = run_eval(qs, load_config())
    typer.echo(f"\n总计 {rep['total']} | 执行成功 {rep['exec_ok']} | "
               f"经自修复 {rep['repaired']} | 人工标注准确率 {rep['accuracy_annotated']}")


if __name__ == "__main__":
    app()
