# src/aigis/config.py
from dataclasses import dataclass
import os
from dotenv import load_dotenv

@dataclass
class Config:
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "aigis"
    db_user: str = "aigis_readonly"       # 只读执行
    db_password: str = "aigis_readonly"
    admin_user: str = "aigis"             # 导出 schema / 预处理
    admin_password: str = "aigis_dev_2026"
    maker_user: str = "aigis_maker"       # 制作写通道（仅 user_layers schema 可写）
    maker_password: str = "aigis_maker"
    llm_api_key: str = ""
    llm_base_url: str = "http://223.92.35.113:8001/v1"
    llm_model: str = "qwen3827b"

def load_config(env_file: str | None = None) -> Config:
    # 显式指定 env 文件时文件值优先（否则被 shell 已有环境变量遮蔽）；
    # 未指定时保持惯例：系统环境变量优先于项目 .env
    load_dotenv(env_file, override=env_file is not None)
    # POSTGRES_USER/PASSWORD 是 .env 里的超管凭据 -> 挂管理账号（schema 导出/预处理）；
    # 引擎执行账号 db_user/db_password 恒为只读默认，不走 .env，保住只读执行纵深
    return Config(
        db_host=os.getenv("POSTGRES_HOST", "localhost"),
        db_port=int(os.getenv("POSTGRES_PORT", "5432")),
        db_name=os.getenv("POSTGRES_DB", "aigis"),
        admin_user=os.getenv("POSTGRES_USER", "aigis"),
        admin_password=os.getenv("POSTGRES_PASSWORD", "aigis_dev_2026"),
        maker_user=os.getenv("MAKER_USER", "aigis_maker"),
        maker_password=os.getenv("MAKER_PASSWORD", "aigis_maker"),
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        llm_base_url=os.getenv("LLM_BASE_URL", "http://223.92.35.113:8001/v1"),
        llm_model=os.getenv("LLM_MODEL", "qwen3827b"),
    )
