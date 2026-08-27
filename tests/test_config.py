# tests/test_config.py
from pathlib import Path
from aigis.config import Config, load_config

def test_defaults():
    c = Config()
    assert c.db_host == "localhost" and c.db_port == 5432 and c.db_name == "aigis"
    assert c.db_user == "aigis_readonly"      # 引擎执行账号
    assert c.admin_user == "aigis"             # 管理账号（schema 导出/预处理）
    assert c.maker_user == "aigis_maker"       # 制作写通道账号（仅 user_layers 可写）
    assert c.llm_model == "qwen3827b"

def test_load_env(tmp_path: Path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("LLM_API_KEY=sk-test\n", encoding="utf-8")
    # 模拟 shell 已有同名变量：显式 env_file 时文件值必须优先（override 契约）
    monkeypatch.setenv("LLM_API_KEY", "shell-conflict-value")
    c = load_config(str(f))
    assert c.llm_api_key == "sk-test"

def test_load_env_db_credentials(tmp_path: Path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("POSTGRES_USER=u_env\nPOSTGRES_PASSWORD=p_env\n", encoding="utf-8")
    monkeypatch.setenv("POSTGRES_USER", "u_shell")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p_shell")
    c = load_config(str(f))
    # POSTGRES_* 是超管凭据 -> 落到管理账号；执行账号不受 .env 污染
    assert c.admin_user == "u_env" and c.admin_password == "p_env"
    assert c.db_user == "aigis_readonly" and c.db_password == "aigis_readonly"

def test_db_credentials_default_when_unset(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("POSTGRES_USER", raising=False)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    c = load_config(str(tmp_path / "none.env"))  # 不存在的 env 文件：不注入任何值
    assert c.admin_user == "aigis" and c.admin_password == "aigis_dev_2026"
    assert c.db_user == "aigis_readonly" and c.db_password == "aigis_readonly"

def test_maker_env_override(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MAKER_USER", "maker_env")
    monkeypatch.setenv("MAKER_PASSWORD", "maker_pw_env")
    c = load_config(str(tmp_path / "none.env"))
    assert c.maker_user == "maker_env" and c.maker_password == "maker_pw_env"
    monkeypatch.delenv("MAKER_USER", raising=False)
    monkeypatch.delenv("MAKER_PASSWORD", raising=False)
    c = load_config(str(tmp_path / "none.env"))
    # 无环境变量时恒为 maker 默认，不走 .env，保住写通道凭据可预期
    assert c.maker_user == "aigis_maker" and c.maker_password == "aigis_maker"
