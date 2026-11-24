"""Embedding（向量化）配置持久化：SQLite 单条记录。

与 llm_configs 的多条配置不同，向量化服务只有一个全局配置，
因此表用 `id = 1` 加 CHECK 约束，数据库层面保证「只能有一个值」。

约定：
- 未保存过配置时，读取结果回退 server/.env 的平台默认值，并标记 isDefault=True；
- 密钥对外脱敏，空值或脱敏占位回传时表示保留原密钥；
- 库里没填密钥时，运行时仍可使用 .env 里的 VECTOR_API_KEY；
- 本模块全部是同步函数，调用方需用 asyncio.to_thread 包裹。
"""

import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from app.core.paths import DB_PATH

# 显式指定 server/.env，避免其他模块先导入时读不到配置
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

logger = logging.getLogger(__name__)

MASK_PREFIX = "****"
ROW_ID = 1
DEFAULT_BASE_URL = os.getenv(
    "VECTOR_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
)
DEFAULT_MODEL = os.getenv("VECTOR_MODEL", "text-embedding-v4")
DEFAULT_DIMENSIONS = 1024
DEFAULT_TIMEOUT = 120

_SCHEMA = """
CREATE TABLE IF NOT EXISTS embedding_config (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    base_url    TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL DEFAULT '',
    dimensions  INTEGER NOT NULL DEFAULT 1024,
    timeout     INTEGER NOT NULL DEFAULT 120,
    api_key     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT ''
);
"""


def _connect() -> sqlite3.Connection:
    """打开连接并启用 WAL；表不存在时建表。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


@contextmanager
def _open():
    """事务提交/回滚后统一关闭连接，避免句柄泄漏。"""
    conn = _connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _now() -> str:
    return datetime.now().isoformat(timespec="microseconds")


def mask_api_key(api_key: str) -> str:
    """密钥脱敏：只回传末 4 位；过短的密钥完全不回传。"""
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return MASK_PREFIX
    return f"{MASK_PREFIX}{api_key[-4:]}"


def _env_api_key() -> str:
    return (os.getenv("VECTOR_API_KEY") or "").strip()


def _default_config(reveal_key: bool) -> dict:
    """未落库时使用 .env 的平台默认值。"""
    key = _env_api_key()
    return {
        "baseUrl": DEFAULT_BASE_URL,
        "model": DEFAULT_MODEL,
        "dimensions": DEFAULT_DIMENSIONS,
        "timeout": DEFAULT_TIMEOUT,
        "apiKey": key if reveal_key else mask_api_key(key),
        "isDefault": True,
        "updatedAt": "",
    }


def _row_config(row: sqlite3.Row, reveal_key: bool) -> dict:
    """数据库行 → 对外字典；默认密钥脱敏。"""
    key = row["api_key"] or ""
    if reveal_key and not key:
        key = _env_api_key()
    return {
        "baseUrl": row["base_url"] or "",
        "model": row["model"] or "",
        "dimensions": int(row["dimensions"] or DEFAULT_DIMENSIONS),
        "timeout": int(row["timeout"] or DEFAULT_TIMEOUT),
        "apiKey": key if reveal_key else mask_api_key(key),
        "isDefault": False,
        "updatedAt": row["updated_at"] or "",
    }


def get_config(reveal_key: bool = False) -> dict:
    """读取唯一的向量化配置；没有记录时返回平台默认值。"""
    try:
        with _open() as conn:
            row = conn.execute(
                "SELECT * FROM embedding_config WHERE id = ?", (ROW_ID,)
            ).fetchone()
            return _row_config(row, reveal_key) if row else _default_config(reveal_key)
    except Exception:
        logger.warning("向量化配置读取失败，回退平台默认配置", exc_info=True)
        return _default_config(reveal_key)


def get_runtime_config() -> dict:
    """后端内部使用：返回含真实密钥的配置，供 embedding 客户端初始化。"""
    return get_config(reveal_key=True)


def save_config(
    base_url: str,
    model: str,
    dimensions: int,
    timeout: int,
    api_key: str,
) -> Optional[dict]:
    """新增或覆盖唯一配置；apiKey 留空 / 脱敏占位时保留原密钥。

    失败返回 None，由接口层转成 500，避免前端误以为保存成功。
    """
    now = _now()
    try:
        with _open() as conn:
            row = conn.execute(
                "SELECT api_key FROM embedding_config WHERE id = ?", (ROW_ID,)
            ).fetchone()
            key = (api_key or "").strip()
            if not key or key.startswith(MASK_PREFIX):
                key = (row["api_key"] or "") if row else ""

            conn.execute(
                """
                INSERT INTO embedding_config
                    (id, base_url, model, dimensions, timeout, api_key, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    base_url = excluded.base_url,
                    model = excluded.model,
                    dimensions = excluded.dimensions,
                    timeout = excluded.timeout,
                    api_key = excluded.api_key,
                    updated_at = excluded.updated_at
                """,
                (ROW_ID, base_url, model, int(dimensions), int(timeout), key, now, now),
            )
            updated = conn.execute(
                "SELECT * FROM embedding_config WHERE id = ?", (ROW_ID,)
            ).fetchone()
            return _row_config(updated, reveal_key=False) if updated else None
    except Exception:
        logger.warning("向量化配置保存失败", exc_info=True)
        return None
