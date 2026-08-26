# tests/test_cli.py
# CLI 错误出口锁定：mock run_query，不依赖真库/真 LLM。
import psycopg
from typer.testing import CliRunner

from aigis.cli import app

runner = CliRunner()


def test_query_db_down_prints_hint_and_exits_1(monkeypatch):
    def fake_run_query(question, cfg):
        raise psycopg.OperationalError("connection failed")
    monkeypatch.setattr("aigis.cli.run_query", fake_run_query)
    result = runner.invoke(app, ["北京有多少兴趣点"])
    assert result.exit_code == 1
    assert "数据库连接失败，请确认 aigis-postgis 容器在运行" in result.output
