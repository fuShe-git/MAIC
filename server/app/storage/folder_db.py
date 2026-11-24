"""课件文件夹（持久化）：SQLite 存储，替代早期只存在浏览器 localStorage 的方案。

约定：
- 一门课程最多归属一个文件夹（folder_courses.course_id 建唯一索引）；
- 文件夹内顺序按插入顺序（folder_courses 的 rowid），移入即排到末尾；
- 本模块全部是同步函数，调用方必须用 asyncio.to_thread 包裹；
- 所有异常只记日志并返回安全默认值，绝不阻断页面使用。
"""

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Optional
from uuid import uuid4

from app.core.paths import DB_PATH

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS folder_courses (
    folder_id TEXT NOT NULL,
    course_id TEXT NOT NULL,
    PRIMARY KEY (folder_id, course_id)
);

-- 一门课程只能在一个文件夹里，靠唯一索引兜底
CREATE UNIQUE INDEX IF NOT EXISTS idx_folder_courses_course
    ON folder_courses (course_id);
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


def _folder_rows(conn: sqlite3.Connection) -> list[dict]:
    """读出全部文件夹并按课程顺序组装 courseIds。"""
    rows = conn.execute("SELECT * FROM folders ORDER BY created_at DESC, id DESC").fetchall()
    links = conn.execute(
        "SELECT folder_id, course_id FROM folder_courses ORDER BY rowid"
    ).fetchall()

    courses: dict[str, list[str]] = {}
    for link in links:
        courses.setdefault(link["folder_id"], []).append(link["course_id"])

    return [
        {
            "id": row["id"],
            "name": row["name"],
            "courseIds": courses.get(row["id"], []),
            "createdAt": row["created_at"],
        }
        for row in rows
    ]


def _get_folder(conn: sqlite3.Connection, folder_id: str) -> Optional[dict]:
    """按 id 取单个文件夹（含 courseIds）；不存在返回 None。"""
    row = conn.execute("SELECT * FROM folders WHERE id = ?", (folder_id,)).fetchone()
    if not row:
        return None
    links = conn.execute(
        "SELECT course_id FROM folder_courses WHERE folder_id = ? ORDER BY rowid",
        (folder_id,),
    ).fetchall()
    return {
        "id": row["id"],
        "name": row["name"],
        "courseIds": [link["course_id"] for link in links],
        "createdAt": row["created_at"],
    }


def list_folders() -> dict:
    """列出全部文件夹（新建的在前）。"""
    try:
        with _open() as conn:
            items = _folder_rows(conn)
        return {"items": items}
    except Exception:
        logger.warning("文件夹列表读取失败", exc_info=True)
        return {"items": []}


def create_folder(name: str, course_ids: Optional[list[str]] = None) -> Optional[dict]:
    """新建文件夹；可同时把若干课程移进去（会先从别的文件夹摘掉）。"""
    folder_id = uuid4().hex
    created_at = datetime.now().isoformat(timespec="microseconds")
    try:
        with _open() as conn:
            conn.execute(
                "INSERT INTO folders (id, name, created_at) VALUES (?, ?, ?)",
                (folder_id, name, created_at),
            )
            for course_id in course_ids or []:
                if not course_id:
                    continue
                conn.execute(
                    "DELETE FROM folder_courses WHERE course_id = ?", (course_id,)
                )
                conn.execute(
                    "INSERT INTO folder_courses (folder_id, course_id) VALUES (?, ?)",
                    (folder_id, course_id),
                )
            return _get_folder(conn, folder_id)
    except Exception:
        logger.warning("文件夹创建失败：%s", name, exc_info=True)
        return None


def rename_folder(folder_id: str, name: str) -> Optional[dict]:
    """重命名文件夹；不存在返回 None。"""
    try:
        with _open() as conn:
            cursor = conn.execute(
                "UPDATE folders SET name = ? WHERE id = ?", (name, folder_id)
            )
            if cursor.rowcount <= 0:
                return None
            return _get_folder(conn, folder_id)
    except Exception:
        logger.warning("文件夹重命名失败：%s", folder_id, exc_info=True)
        return None


def delete_folder(folder_id: str) -> bool:
    """只删文件夹本身（课程回到未分组），返回是否真的删掉了。"""
    try:
        with _open() as conn:
            cursor = conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
            removed = cursor.rowcount > 0
            conn.execute(
                "DELETE FROM folder_courses WHERE folder_id = ?", (folder_id,)
            )
        return removed
    except Exception:
        logger.warning("文件夹删除失败：%s", folder_id, exc_info=True)
        return False


def assign_course(course_id: str, folder_id: str) -> bool:
    """设置课程所属文件夹；folder_id 为空表示移出（回到未分组）。"""
    try:
        with _open() as conn:
            # 先校验目标文件夹存在，避免「目标不存在却把原归属删了」
            if folder_id:
                exists = conn.execute(
                    "SELECT 1 FROM folders WHERE id = ?", (folder_id,)
                ).fetchone()
                if not exists:
                    return False
            conn.execute("DELETE FROM folder_courses WHERE course_id = ?", (course_id,))
            if folder_id:
                conn.execute(
                    "INSERT INTO folder_courses (folder_id, course_id) VALUES (?, ?)",
                    (folder_id, course_id),
                )
        return True
    except Exception:
        logger.warning("课程归类失败：%s → %s", course_id, folder_id, exc_info=True)
        return False


def remove_course(course_id: str) -> bool:
    """把课程从所有文件夹里摘掉（课件被删除时清理用）。"""
    try:
        with _open() as conn:
            conn.execute("DELETE FROM folder_courses WHERE course_id = ?", (course_id,))
        return True
    except Exception:
        logger.warning("课程归类清理失败：%s", course_id, exc_info=True)
        return False
