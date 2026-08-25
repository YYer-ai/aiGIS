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
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

def load_config(env_file: str | None = None) -> Config:
    # 显式指定 env 文件时文件值优先（否则被 shell 已有环境变量遮蔽）；
    # 未指定时保持惯例：系统环境变量优先于项目 .env
    load_dotenv(env_file, override=env_file is not None)
    return Config(
        db_host=os.getenv("POSTGRES_HOST", "localhost"),
        db_port=int(os.getenv("POSTGRES_PORT", "5432")),
        db_name=os.getenv("POSTGRES_DB", "aigis"),
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", ""),
    )
