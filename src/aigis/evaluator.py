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
    单行单列（count 类）取值本身，否则取行数；取值非数值（如带单位文本）时记 None，
    表示无法粗检）。
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
            in_range = lo <= n <= hi if isinstance(n, (int, float)) else None
        results.append({"id": q["id"], "category": q.get("category", ""),
                        "question": q["question"], "ok": out.ok,
                        "attempts": out.attempts, "sql": out.sql,
                        "rows": len(out.rows), "in_range": in_range,
                        "sample_rows": [repr(r)[:120] for r in out.rows[:3]],
                        "human_pass": q.get("human_pass")})
        if progress:
            typer.echo(f"[{q['id']}] {'OK' if out.ok else 'FAIL'} 尝试{out.attempts}次 行数{len(out.rows)}")
    annotated = [r for r in results if r["human_pass"] is not None]
    acc = (sum(1 for r in annotated if r["human_pass"]) / len(annotated)) if annotated else None
    return {"total": len(results), "exec_ok": sum(1 for r in results if r["ok"]),
            "repaired": repaired, "accuracy_annotated": acc, "results": results}


def _cell(text: str) -> str:
    """Markdown 表格单元格转义：竖线转义、换行转 <br>。"""
    return str(text).replace("|", "\\|").replace("\n", "<br>")


def write_report(rep: dict, path: str) -> None:
    """把评估结果写为 Markdown 人工标注报告（逐题明细表 + 尾部统计）。"""
    lines = [
        "# 评估报告（人工标注用）",
        "",
        f"- 总计 {rep['total']} | 执行成功 {rep['exec_ok']} | 经自修复 {rep['repaired']}"
        f" | 人工标注准确率 {rep['accuracy_annotated']}",
        "",
        "| id | category | ok | attempts | rows | in_range | question | SQL | 结果前3行 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rep["results"]:
        sql = " ".join((r["sql"] or "").split())  # 压缩为单行，避免破坏表格
        sample = "<br>".join(_cell(s) for s in r["sample_rows"])
        lines.append(f"| {r['id']} | {_cell(r['category'])} "
                     f"| {'OK' if r['ok'] else 'FAIL'} | {r['attempts']} | {r['rows']} "
                     f"| {r['in_range']} | {_cell(r['question'])} | `{_cell(sql)}` | {sample} |")
    in_range_bad = [r["id"] for r in rep["results"] if r["in_range"] is False]
    failed = [r["id"] for r in rep["results"] if not r["ok"]]
    lines += ["", f"- in_range 异常题号（False）：{in_range_bad or '无'}",
              f"- 失败题号：{failed or '无'}", ""]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines), encoding="utf-8")


@app.command()
def main(only_annotated: bool = typer.Option(False, "--only-annotated",
                                             help="只跑已人工标注（human_pass 非 null）的题"),
         report: str = typer.Option("eval/report.md", "--report",
                                    help="Markdown 报告输出路径")):
    """跑题集并打印汇总报告。"""
    qs = yaml.safe_load(Path("eval/questions.yaml").read_text(encoding="utf-8"))
    if only_annotated:
        qs = [q for q in qs if q.get("human_pass") is not None]
    rep = run_eval(qs, load_config())
    typer.echo(f"\n总计 {rep['total']} | 执行成功 {rep['exec_ok']} | "
               f"经自修复 {rep['repaired']} | 人工标注准确率 {rep['accuracy_annotated']}")
    write_report(rep, report)
    typer.echo(f"报告已写入 {report}")


if __name__ == "__main__":
    app()
