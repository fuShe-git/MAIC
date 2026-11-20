"""课件资产存储层：导出 course_db 的公共函数。"""

from app.storage.course_db import (
    delete_course,
    get_course,
    init_db,
    list_courses,
    save_course,
    update_status,
)

__all__ = [
    "init_db",
    "save_course",
    "list_courses",
    "get_course",
    "delete_course",
    "update_status",
]
