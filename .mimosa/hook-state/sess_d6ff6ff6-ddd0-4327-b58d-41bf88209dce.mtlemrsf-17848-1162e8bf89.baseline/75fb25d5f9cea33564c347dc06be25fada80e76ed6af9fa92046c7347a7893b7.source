# src/aigis/cli.py
"""命令行入口：aigis "中文空间问题" → SQL → 校验 → 只读执行 → GeoJSON。"""
import json
from pathlib import Path

import psycopg
import typer

from aigis.config import load_config
from aigis.llm import LLMError
from aigis.repair import run_query

app = typer.Typer(add_completion=False, help="AI-GIS 中文空间查询引擎")


@app.command()
def query(
    question: str = typer.Argument(..., help="中文空间问题"),
    out: Path = typer.Option(Path("out/result.geojson"), "--out", "-o",
                             help="GeoJSON 输出路径"),
    show_sql: bool = typer.Option(False, "--show-sql",
                                  help="显式开关；SQL 默认即打印，便于核对"),
):
    """中文自然语言空间查询：生成 SQL → 校验 → 只读执行 → 输出 GeoJSON。"""
    cfg = load_config()
    try:
        result = run_query(question, cfg)
    except LLMError as e:
        typer.echo(f"错误：{e}", err=True)
        raise typer.Exit(1)
    except psycopg.OperationalError:  # DB 断连（schema 导出/建连失败）给用户明确提示
        typer.echo("数据库连接失败，请确认 aigis-postgis 容器在运行", err=True)
        raise typer.Exit(1)
    # 工具过程可见（README）：始终打印生成的 SQL，便于人工核对
    typer.echo(f"生成的 SQL（尝试 {result.attempts} 次）：\n{result.sql}\n")
    if not result.ok:
        typer.echo(f"查询失败：{result.error}", err=True)
        raise typer.Exit(1)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result.geojson, ensure_ascii=False), encoding="utf-8")
    typer.echo(f"结果 {len(result.rows)} 行，GeoJSON 已写入 {out}")


if __name__ == "__main__":
    app()
