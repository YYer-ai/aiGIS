# tests/test_settings.py
"""模型配置持久层与路由冒烟（providers.json / config.toml / .env 激活）。

路径全部 monkeypatch 到 tmp_path，绝不触真实 data/、config.toml 与 .env；
LLM_* 环境变量经 monkeypatch.setenv 占位，activate 的进程内写入在用例结束时自动还原。
"""
import json

from fastapi.testclient import TestClient

from aigis_web import settings_store
from aigis_web.app import create_app


def _redirect_paths(monkeypatch, tmp_path):
    providers = tmp_path / "providers.json"
    toml = tmp_path / "config.toml"
    env = tmp_path / ".env"
    monkeypatch.setattr(settings_store, "_PROVIDERS_PATH", providers)
    monkeypatch.setattr(settings_store, "_TOML_PATH", toml)
    monkeypatch.setattr(settings_store, "_ENV_PATH", env)
    return providers, toml, env


def _occupy_llm_env(monkeypatch):
    for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.setenv(k, "placeholder")


def test_provider_roundtrip_and_activation(monkeypatch, tmp_path):
    providers, _, env = _redirect_paths(monkeypatch, tmp_path)
    _occupy_llm_env(monkeypatch)

    saved = settings_store.upsert_provider(
        None, "生产", "http://a/v1", "sk-test-1234", "qwen")
    assert saved["api_key"] == "***1234"  # 返回视图脱敏
    assert json.loads(providers.read_text("utf-8"))["providers"][0]["name"] == "生产"

    # 列表脱敏、不回明文
    listed = settings_store.list_providers()
    assert listed[0]["api_key"] == "***1234" and "sk-test" not in str(listed)

    # 掩码/空 key 更新 = 沿用原值；明文覆盖
    settings_store.upsert_provider(saved["id"], "生产", "http://b/v1",
                                   "***1234", "qwen2")
    assert settings_store.get_provider_plain(saved["id"])["api_key"] == "sk-test-1234"
    settings_store.upsert_provider(saved["id"], "生产", "http://b/v1",
                                   "sk-new-9999", "qwen2")
    assert settings_store.get_provider_plain(saved["id"])["api_key"] == "sk-new-9999"

    # 激活：写 .env + 同步进程环境（热生效关键路径）
    act = settings_store.activate_provider(saved["id"])
    assert act["id"] == saved["id"]
    env_text = env.read_text("utf-8")
    # set_key 以引号形式写值（dotenv 惯例，load_dotenv 读回时剥离）
    assert "LLM_BASE_URL='http://b/v1'" in env_text and "sk-new-9999" in env_text
    import os
    assert os.environ["LLM_BASE_URL"] == "http://b/v1"
    assert settings_store.get_active_id() == saved["id"]

    # 删除激活者 → active 回空，其余保留
    settings_store.delete_provider(saved["id"])
    assert settings_store.get_active_id() is None
    assert settings_store.list_providers() == []
    try:
        settings_store.delete_provider(saved["id"])
        assert False, "已删 id 应抛 KeyError"
    except KeyError:
        pass


def test_toml_settings_roundtrip_keeps_comments(monkeypatch, tmp_path):
    _, toml, _ = _redirect_paths(monkeypatch, tmp_path)
    toml.write_text("# 顶部注释\n[user_notes]\nkeep = 1\n", "utf-8")

    out = settings_store.write_llm_settings({"thinking": "high",
                                             "history_rounds": 6})
    assert out["thinking"] == "high" and out["history_rounds"] == 6
    text = toml.read_text("utf-8")
    assert "# 顶部注释" in text and "keep = 1" in text  # 用户内容不丢
    again = settings_store.read_llm_settings()
    assert again["thinking"] == "high" and again["temperature"] == 0.0  # 未传键回落默认


def test_config_loads_toml_fields(monkeypatch, tmp_path):
    import aigis.config as cfgmod
    _, toml, _ = _redirect_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(cfgmod, "_TOML_PATH", toml)
    settings_store.write_llm_settings({"thinking": "low", "temperature": 0.3,
                                       "timeout_seconds": 120,
                                       "history_rounds": 5,
                                       "history_char_limit": 300})
    cfg = cfgmod.load_config(env_file=str(tmp_path / ".env"))
    assert cfg.llm_thinking == "low" and cfg.llm_temperature == 0.3
    assert cfg.llm_timeout == 120 and cfg.history_rounds == 5
    assert cfg.history_char_limit == 300


def test_settings_routes(monkeypatch, tmp_path):
    _redirect_paths(monkeypatch, tmp_path)
    _occupy_llm_env(monkeypatch)
    client = TestClient(create_app())

    r = client.get("/api/settings")
    assert r.status_code == 200 and r.json()["providers"] == []

    # 保存（不验证，避免外网依赖）→ 激活 → 再读
    r = client.post("/api/providers", json={
        "name": "测试", "base_url": "http://x/v1", "api_key": "sk-abc",
        "model": "m", "verify": False})
    assert r.status_code == 200
    pid = r.json()["id"]

    r = client.post(f"/api/providers/{pid}/activate")
    assert r.status_code == 200
    r = client.get("/api/settings")
    assert r.json()["active_id"] == pid
    assert r.json()["providers"][0]["api_key"] == "***-abc"  # sk-abc 脱敏，不回明文

    # 运行参数：非法 thinking 422；合法写入生效
    assert client.post("/api/settings/llm", json={"thinking": "deep"}).status_code == 422
    r = client.post("/api/settings/llm", json={"thinking": "medium",
                                               "history_rounds": 8})
    assert r.status_code == 200 and r.json()["thinking"] == "medium"
    assert client.get("/api/settings").json()["llm"]["history_rounds"] == 8

    # 未知 id 404；删除成功
    assert client.post("/api/providers/nope/activate").status_code == 404
    assert client.delete(f"/api/providers/{pid}").status_code == 200
    assert client.get("/api/settings").json()["providers"] == []
