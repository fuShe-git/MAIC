"""大模型服务商 / 模型配置（持久化）：SQLite 存储，替代浏览器 localStorage。

专业名词解释：SQLite 是 Python 标准库自带的嵌入式单文件数据库（sqlite3），
零第三方依赖；WAL（Write-Ahead Logging）是一种日志模式，读写并发更好。

约定：
- 一个服务商（llm_configs）下挂多个模型（llm_models），模型按 sort_order 排序；
- 前端用「配置 id:模型 id」复合标识定位一个可用模型；
- 本模块全部是同步函数，调用方必须用 asyncio.to_thread 包裹，避免阻塞事件循环；
- 列表接口返回脱敏密钥；更新时空密钥 / 脱敏占位表示保留原密钥；
- 所有异常只记日志并返回安全默认值，绝不阻断主流程。
"""

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Optional
from uuid import uuid4

from app.core.paths import DB_PATH

logger = logging.getLogger(__name__)

# 密钥脱敏前缀：前端原样回传时视为「未修改」
MASK_PREFIX = "****"
DEFAULT_TIMEOUT = 120
# 前端「系统默认」哨兵值：交给 .env 里的默认模型处理
PLATFORM_DEFAULT_ID = "platform-default"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_configs (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL DEFAULT '',
    base_url   TEXT NOT NULL DEFAULT '',
    api_key    TEXT NOT NULL DEFAULT '',
    timeout    INTEGER NOT NULL DEFAULT 120,
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS llm_models (
    config_id  TEXT NOT NULL,
    id         TEXT NOT NULL,
    name       TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (config_id, id)
);

CREATE INDEX IF NOT EXISTS idx_llm_models_config
    ON llm_models (config_id, sort_order);
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
    """打开连接的上下文：事务提交/回滚后统一关闭连接，避免句柄泄漏。"""
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


def _is_masked(api_key: str) -> bool:
    return api_key.startswith(MASK_PREFIX)


def _clean_models(models: Optional[list[dict]]) -> list[dict]:
    """清洗模型列表：去掉空名，并按「模型名 / id」同时去重，保留输入顺序。"""
    cleaned: list[dict] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for item in models or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name.casefold() in seen_names:
            continue
        model_id = str(item.get("id") or "").strip() or uuid4().hex
        if model_id in seen_ids:
            model_id = uuid4().hex
        cleaned.append({"id": model_id, "name": name})
        seen_ids.add(model_id)
        seen_names.add(name.casefold())
    return cleaned


def _replace_models(conn: sqlite3.Connection, config_id: str, models: list[dict]) -> None:
    """整体覆盖某个服务商下的模型列表（先删后插，保证顺序与内容一致）。"""
    conn.execute("DELETE FROM llm_models WHERE config_id = ?", (config_id,))
    for index, model in enumerate(_clean_models(models)):
        conn.execute(
            "INSERT INTO llm_models (config_id, id, name, sort_order) VALUES (?, ?, ?, ?)",
            (config_id, model["id"], model["name"], index),
        )


def _config_row(conn: sqlite3.Connection, row: sqlite3.Row, reveal_key: bool) -> dict:
    """数据库行 → 对外字典；默认密钥脱敏。"""
    models = conn.execute(
        "SELECT id, name FROM llm_models WHERE config_id = ? ORDER BY sort_order, rowid",
        (row["id"],),
    ).fetchall()
    api_key = row["api_key"] or ""
    return {
        "id": row["id"],
        "name": row["name"],
        "timeout": int(row["timeout"] or DEFAULT_TIMEOUT),
        "baseUrl": row["base_url"],
        "apiKey": api_key if reveal_key else mask_api_key(api_key),
        "models": [{"id": item["id"], "name": item["name"]} for item in models],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _get_row(conn: sqlite3.Connection, config_id: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM llm_configs WHERE id = ?", (config_id,)
    ).fetchone()


def _identity(name: str, base_url: str) -> tuple[str, str]:
    """供应商合并判据：名称不区分大小写，Base URL 忽略首尾空白与末尾斜杠。"""
    return (name.strip().casefold(), base_url.strip().rstrip("/"))


def _models_for(conn: sqlite3.Connection, config_id: str) -> list[dict]:
    """读出某个服务商下的模型（含 id，供合并时保留原有标识）。"""
    rows = conn.execute(
        "SELECT id, name FROM llm_models WHERE config_id = ? ORDER BY sort_order, rowid",
        (config_id,),
    ).fetchall()
    return [{"id": row["id"], "name": row["name"]} for row in rows]


def _merge_models(primary: list[dict], incoming: list[dict]) -> list[dict]:
    """按模型名去重合并：保留已有 id 与顺序，只追加新模型。"""
    merged: list[dict] = []
    seen_names: set[str] = set()
    seen_ids: set[str] = set()
    for item in list(primary) + list(incoming):
        name = str(item.get("name") or "").strip()
        if not name or name.casefold() in seen_names:
            continue
        model_id = str(item.get("id") or "").strip() or uuid4().hex
        if model_id in seen_ids:
            model_id = uuid4().hex
        merged.append({"id": model_id, "name": name})
        seen_names.add(name.casefold())
        seen_ids.add(model_id)
    return merged


def _find_by_identity(
    conn: sqlite3.Connection,
    name: str,
    base_url: str,
    exclude_id: Optional[str] = None,
) -> Optional[sqlite3.Row]:
    """查找「同名 + 同 Base URL」的另一条配置；exclude_id 用于编辑时跳过自己。"""
    target = _identity(name, base_url)
    rows = conn.execute("SELECT * FROM llm_configs").fetchall()
    for row in rows:
        if exclude_id and row["id"] == exclude_id:
            continue
        if _identity(row["name"], row["base_url"]) == target:
            return row
    return None


def _pick_keep_row(left: sqlite3.Row, right: sqlite3.Row) -> sqlite3.Row:
    """重复配置合并时保留创建更早的一条，id 更稳定（下拉里可能正被选中）。"""
    return left if (left["created_at"] or "", left["id"]) <= (right["created_at"] or "", right["id"]) else right


def _merge_duplicate_rows(conn: sqlite3.Connection) -> int:
    """把库里已有的「同名 + 同 Base URL」配置合并为一条；返回删除的重复条数。"""
    rows = conn.execute(
        "SELECT * FROM llm_configs ORDER BY created_at, id"
    ).fetchall()
    keepers: dict[tuple[str, str], sqlite3.Row] = {}
    removed = 0
    for row in rows:
        key = _identity(row["name"], row["base_url"])
        keeper = keepers.get(key)
        if keeper is None:
            keepers[key] = row
            continue

        # 合并前重新读一次，避免连续三条重复时用到过期的密钥 / 超时
        current = _get_row(conn, keeper["id"]) or keeper
        merged = _merge_models(
            _models_for(conn, keeper["id"]),
            _models_for(conn, row["id"]),
        )
        next_key = (current["api_key"] or "") or (row["api_key"] or "")
        next_timeout = max(
            int(current["timeout"] or DEFAULT_TIMEOUT),
            int(row["timeout"] or DEFAULT_TIMEOUT),
        )
        conn.execute(
            """
            UPDATE llm_configs
               SET api_key = ?, timeout = ?, updated_at = ?
             WHERE id = ?
            """,
            (next_key, next_timeout, _now(), keeper["id"]),
        )
        _replace_models(conn, keeper["id"], merged)
        # 两表没有数据库级外键约束，这里显式清理被合并掉的那条
        conn.execute("DELETE FROM llm_configs WHERE id = ?", (row["id"],))
        conn.execute("DELETE FROM llm_models WHERE config_id = ?", (row["id"],))
        keepers[key] = _get_row(conn, keeper["id"]) or keeper
        removed += 1

    if removed:
        logger.info("已合并 %d 条重复的大模型配置", removed)
    return removed


def merge_duplicate_configs() -> int:
    """对外暴露的一次性合并入口（应用启动时可调用）；失败返回 0。"""
    try:
        with _open() as conn:
            return _merge_duplicate_rows(conn)
    except Exception:
        logger.warning("重复大模型配置合并失败", exc_info=True)
        return 0


def list_configs() -> dict:
    """列出全部服务商配置（新建的在前），密钥均已脱敏。

    读取前顺手合并历史遗留的重复配置，保证前端永远看到「一个供应商一张卡」。
    """
    try:
        with _open() as conn:
            _merge_duplicate_rows(conn)
            rows = conn.execute(
                "SELECT * FROM llm_configs ORDER BY created_at DESC, id DESC"
            ).fetchall()
            items = [_config_row(conn, row, reveal_key=False) for row in rows]
        return {"items": items}
    except Exception:
        logger.warning("大模型配置列表读取失败", exc_info=True)
        return {"items": []}


def create_config(
    name: str,
    base_url: str,
    api_key: str,
    timeout: int,
    models: Optional[list[dict]],
) -> Optional[dict]:
    """新建服务商配置；同名 + 同 Base URL 已存在时把模型合并进去，不再新建卡片。"""
    cleaned_models = _clean_models(models)
    now = _now()
    try:
        with _open() as conn:
            existing = _find_by_identity(conn, name, base_url)
            if existing:
                merged = _merge_models(
                    _models_for(conn, existing["id"]), cleaned_models
                )
                final_key = (api_key or "").strip() or (existing["api_key"] or "")
                conn.execute(
                    """
                    UPDATE llm_configs
                       SET name = ?, base_url = ?, api_key = ?, timeout = ?, updated_at = ?
                     WHERE id = ?
                    """,
                    (name, base_url, final_key, int(timeout), now, existing["id"]),
                )
                _replace_models(conn, existing["id"], merged)
                row = _get_row(conn, existing["id"])
                logger.info("供应商已存在，模型已合并：%s", existing["id"])
                return _config_row(conn, row, reveal_key=False) if row else None

            config_id = uuid4().hex
            conn.execute(
                """
                INSERT INTO llm_configs
                    (id, name, base_url, api_key, timeout, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (config_id, name, base_url, api_key, int(timeout), now, now),
            )
            _replace_models(conn, config_id, cleaned_models)
            row = _get_row(conn, config_id)
            return _config_row(conn, row, reveal_key=False) if row else None
    except Exception:
        logger.warning("大模型配置创建失败：%s", name, exc_info=True)
        return None


def update_config(
    config_id: str,
    name: str,
    base_url: str,
    api_key: str,
    timeout: int,
    models: Optional[list[dict]],
) -> Optional[dict]:
    """更新服务商配置；若改成与另一条同名 + 同 Base URL，则两条合并成一条。

    api_key 为空或脱敏占位时保留原密钥。
    """
    try:
        with _open() as conn:
            row = _get_row(conn, config_id)
            if not row:
                return None

            next_key = (api_key or "").strip()
            if not next_key or _is_masked(next_key):
                next_key = row["api_key"] or ""

            duplicate = _find_by_identity(
                conn, name, base_url, exclude_id=config_id
            )
            if duplicate:
                # 保留创建更早的那条（id 更稳定），另一条的模型并进来后删除
                keep_row = _pick_keep_row(row, duplicate)
                drop_row = duplicate if keep_row["id"] == config_id else row
                # 本次提交的模型列表是「正在编辑那条」的权威值（可能删过模型），
                # 另一条则取库里的现有模型，两边按名称去重合并
                other_row = duplicate if keep_row["id"] == config_id else row
                merged = _merge_models(
                    _clean_models(models),
                    _models_for(conn, other_row["id"]),
                )
                final_key = (
                    next_key or (keep_row["api_key"] or "") or (drop_row["api_key"] or "")
                )
                conn.execute(
                    """
                    UPDATE llm_configs
                       SET name = ?, base_url = ?, api_key = ?, timeout = ?, updated_at = ?
                     WHERE id = ?
                    """,
                    (name, base_url, final_key, int(timeout), _now(), keep_row["id"]),
                )
                _replace_models(conn, keep_row["id"], merged)
                if drop_row["id"] != keep_row["id"]:
                    conn.execute(
                        "DELETE FROM llm_configs WHERE id = ?", (drop_row["id"],)
                    )
                    conn.execute(
                        "DELETE FROM llm_models WHERE config_id = ?", (drop_row["id"],)
                    )
                logger.info(
                    "编辑后供应商重复，已合并：keep=%s drop=%s",
                    keep_row["id"],
                    drop_row["id"],
                )
                updated = _get_row(conn, keep_row["id"])
                return _config_row(conn, updated, reveal_key=False) if updated else None

            conn.execute(
                """
                UPDATE llm_configs
                   SET name = ?, base_url = ?, api_key = ?, timeout = ?, updated_at = ?
                 WHERE id = ?
                """,
                (name, base_url, next_key, int(timeout), _now(), config_id),
            )
            _replace_models(conn, config_id, _clean_models(models))
            updated = _get_row(conn, config_id)
            return _config_row(conn, updated, reveal_key=False) if updated else None
    except Exception:
        logger.warning("大模型配置更新失败：%s", config_id, exc_info=True)
        return None


def delete_config(config_id: str) -> bool:
    """删除服务商配置及其模型；返回是否真的删掉了。"""
    try:
        with _open() as conn:
            cursor = conn.execute("DELETE FROM llm_configs WHERE id = ?", (config_id,))
            removed = cursor.rowcount > 0
            # 两张表没有数据库级外键约束，这里显式清理，避免残留脏数据
            conn.execute("DELETE FROM llm_models WHERE config_id = ?", (config_id,))
        return removed
    except Exception:
        logger.warning("大模型配置删除失败：%s", config_id, exc_info=True)
        return False


def get_runtime_model(model_id: str) -> Optional[dict]:
    """解析「配置 id:模型 id」，返回调用大模型所需的完整参数；未命中返回 None。

    返回结构：{"model": 模型名, "api_key": 密钥, "base_url": 接口地址, "timeout": 秒}
    只有后端内部使用，密钥不经过前端。
    """
    raw = (model_id or "").strip()
    if not raw or raw == PLATFORM_DEFAULT_ID or ":" not in raw:
        return None
    config_id, _, model_key = raw.partition(":")
    if not config_id or not model_key:
        return None
    try:
        with _open() as conn:
            config = _get_row(conn, config_id)
            if not config:
                return None
            model = conn.execute(
                "SELECT name FROM llm_models WHERE config_id = ? AND id = ?",
                (config_id, model_key),
            ).fetchone()
            if not model:
                return None
            return {
                "model": model["name"],
                "api_key": config["api_key"] or "",
                "base_url": config["base_url"] or "",
                "timeout": float(config["timeout"] or DEFAULT_TIMEOUT),
            }
    except Exception:
        logger.warning("大模型配置解析失败：%s", raw, exc_info=True)
        return None
