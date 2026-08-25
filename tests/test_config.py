# tests/test_config.py
from pathlib import Path
from aigis.config import Config, load_config

def test_defaults():
    c = Config()
    assert c.db_host == "localhost" and c.db_port == 5432 and c.db_name == "aigis"
    assert c.db_user == "aigis_readonly"      # 引擎执行账号
    assert c.admin_user == "aigis"             # 管理账号（schema 导出/预处理）
    assert c.deepseek_model == "deepseek-chat"

def test_load_env(tmp_path: Path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("DEEPSEEK_API_KEY=sk-test\n", encoding="utf-8")
    # 模拟 shell 已有同名变量：显式 env_file 时文件值必须优先（override 契约）
    monkeypatch.setenv("DEEPSEEK_API_KEY", "shell-conflict-value")
    c = load_config(str(f))
    assert c.deepseek_api_key == "sk-test"
