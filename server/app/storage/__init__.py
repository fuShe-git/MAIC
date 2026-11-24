"""课件资产存储层：导出 course_db 的公共函数，并暴露 folder_db / llm_config_db 模块。"""

from app.storage import folder_db  # noqa: F401  （文件夹持久化模块，按需 from app.storage import folder_db）
from app.storage import llm_config_db  # noqa: F401  （大模型配置持久化，按需 from app.storage import llm_config_db）
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
    "folder_db",
    "llm_config_db",
]
