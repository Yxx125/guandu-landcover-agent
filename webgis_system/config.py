"""应用配置。

所有生产配置均由环境变量提供；默认值只用于本机开发。敏感信息不得写入源码。
"""

from __future__ import annotations

import os
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


APP_ENV = os.getenv("APP_ENV", "development")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "none").strip().lower()
if MODEL_PROVIDER not in {"cloud", "ollama", "none"}:
    raise ValueError("MODEL_PROVIDER 只能是 cloud、ollama 或 none")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")

DATABASE_HOST = os.getenv("DATABASE_HOST", "127.0.0.1")
DATABASE_PORT = int(os.getenv("DATABASE_PORT", "5432"))
DATABASE_NAME = os.getenv("DATABASE_NAME", "guandu_landcover")
DATABASE_USER = os.getenv("DATABASE_USER", "postgres")
DATABASE_PASSWORD = os.getenv("DATABASE_PASSWORD", os.getenv("PGPASSWORD", ""))

GEOSERVER_INTERNAL_URL = os.getenv(
    "GEOSERVER_INTERNAL_URL", "http://localhost:8080/geoserver"
).rstrip("/")
GEOSERVER_PUBLIC_URL = os.getenv(
    "GEOSERVER_PUBLIC_URL", "http://localhost:8080/geoserver"
).rstrip("/")

CLCD_DIR = Path(
    os.getenv("CLCD_DIR", str(PROJECT_DIR.parent / "3_output" / "clcd_guandu"))
)
CHROMA_DIR = Path(os.getenv("CHROMA_DIR", str(PROJECT_DIR / "data" / "chroma")))
DOCUMENTS_DIR = Path(os.getenv("DOCUMENTS_DIR", str(PROJECT_DIR / "documents")))


def database_connect_kwargs() -> dict:
    values = {
        "host": DATABASE_HOST,
        "port": DATABASE_PORT,
        "dbname": DATABASE_NAME,
        "user": DATABASE_USER,
        "connect_timeout": 5,
    }
    if DATABASE_PASSWORD:
        values["password"] = DATABASE_PASSWORD
    return values
