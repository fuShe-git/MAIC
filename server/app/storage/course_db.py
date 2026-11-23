"""课件资产库（SQLite）：记录课件元数据与产物快照。

专业名词解释：SQLite 是 Python 标准库自带的嵌入式本地单文件数据库（sqlite3），
零第三方依赖；WAL（Write-Ahead Logging）是一种日志模式，读写并发更好、锁竞争更少。

约定：
- 本模块全部是同步函数，调用方必须用 asyncio.to_thread 包裹，避免阻塞事件循环；
- 所有异常只记日志并返回安全默认值，绝不阻断课件生成主流程。
"""

import json
import logging
import sqlite3
from contextlib import contextmanager
from typing import Optional

from app.core.paths import DB_PATH, artifact_url

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS courses (
    id           TEXT PRIMARY KEY,
    topic        TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'done',
    page_count   INTEGER NOT NULL DEFAULT 0,
    html_path    TEXT NOT NULL DEFAULT '',
    catalog_json TEXT NOT NULL DEFAULT '',
    page_json    TEXT NOT NULL DEFAULT '',
    audio_json   TEXT NOT NULL DEFAULT '',
    quiz_json    TEXT NOT NULL DEFAULT ''
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
    """打开连接的上下文：事务提交/回滚后统一关闭连接，避免句柄泄漏。"""
    conn = _connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _dumps(value) -> str:
    """JSON 序列化兜底：任何异常都退化为空串，不让写库失败影响生成。"""
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        logger.debug("快照序列化失败", exc_info=True)
        return ""


def _loads(text: str, default):
    try:
        return json.loads(text) if text else default
    except Exception:
        logger.debug("快照反序列化失败", exc_info=True)
        return default


def _row_to_course(row: sqlite3.Row) -> dict:
    """数据库行 → 对外字典，附带反序列化后的快照字段。"""
    return {
        "id": row["id"],
        "topic": row["topic"],
        "createdAt": row["created_at"],
        "status": row["status"],
        "pageCount": int(row["page_count"] or 0),
        "htmlPath": row["html_path"],
        # 浏览器可直接访问的同源地址；htmlPath 仍保留绝对路径供后端文件 IO
        "htmlUrl": artifact_url(row["html_path"], "html"),
        "catalogList": _loads(row["catalog_json"], []),
        "pageList": _loads(row["page_json"], []),
        "audioList": _loads(row["audio_json"], []),
        "quizList": _loads(row["quiz_json"], []),
    }


def init_db() -> None:
    """初始化数据库（建库建表）。"""
    try:
        with _open():
            pass
    except Exception:
        logger.warning("课件库初始化失败", exc_info=True)


def save_course(
    course_id: str,
    topic: str,
    created_at: str,
    status: str,
    page_count: int,
    html_path: str,
    catalog_list: Optional[list] = None,
    page_list: Optional[list] = None,
    audio_list: Optional[list] = None,
    quiz_list: Optional[list] = None,
) -> bool:
    """写入/更新一条课件记录（含产物快照列），主键冲突时整体覆盖。"""
    try:
        with _open() as conn:
            conn.execute(
                """
                INSERT INTO courses
                    (id, topic, created_at, status, page_count, html_path,
                     catalog_json, page_json, audio_json, quiz_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    topic=excluded.topic,
                    status=excluded.status,
                    page_count=excluded.page_count,
                    html_path=excluded.html_path,
                    catalog_json=excluded.catalog_json,
                    page_json=excluded.page_json,
                    audio_json=excluded.audio_json,
                    quiz_json=excluded.quiz_json
                """,
                (
                    course_id,
                    topic,
                    created_at,
                    status,
                    page_count,
                    html_path,
                    _dumps(catalog_list),
                    _dumps(page_list),
                    _dumps(audio_list),
                    _dumps(quiz_list),
                ),
            )
        return True
    except Exception:
        logger.warning("课件写库失败：%s", course_id, exc_info=True)
        return False


def list_courses(page: int = 1, page_size: int = 10) -> dict:
    """分页倒序列出课件（不含大字段快照的完整页面内容也会返回，供列表页展示）。"""
    try:
        page = max(1, int(page))
        page_size = min(50, max(1, int(page_size)))
        offset = (page - 1) * page_size
        with _open() as conn:
            total = conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
            rows = conn.execute(
                "SELECT * FROM courses ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (page_size, offset),
            ).fetchall()
        return {
            "total": int(total),
            "page": page,
            "pageSize": page_size,
            "items": [_row_to_course(row) for row in rows],
        }
    except Exception:
        logger.warning("课件列表查询失败", exc_info=True)
        return {"total": 0, "page": 1, "pageSize": page_size, "items": []}


def get_course(course_id: str) -> Optional[dict]:
    """取一条课件记录（含全部产物快照）；不存在或异常返回 None。"""
    try:
        with _open() as conn:
            row = conn.execute(
                "SELECT * FROM courses WHERE id = ?", (course_id,)
            ).fetchone()
        return _row_to_course(row) if row else None
    except Exception:
        logger.warning("课件查询失败：%s", course_id, exc_info=True)
        return None


def delete_course(course_id: str) -> bool:
    """删除一条课件记录；返回是否真的删掉了。"""
    try:
        with _open() as conn:
            cursor = conn.execute("DELETE FROM courses WHERE id = ?", (course_id,))
            removed = cursor.rowcount > 0
        return removed
    except Exception:
        logger.warning("课件删除失败：%s", course_id, exc_info=True)
        return False


def update_status(course_id: str, status: str) -> bool:
    """更新课件状态（done / partial）。"""
    try:
        with _open() as conn:
            cursor = conn.execute(
                "UPDATE courses SET status = ? WHERE id = ?", (status, course_id)
            )
            updated = cursor.rowcount > 0
        return updated
    except Exception:
        logger.warning("课件状态更新失败：%s", course_id, exc_info=True)
        return False
