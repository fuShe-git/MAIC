"""课件资产接口（新增能力 5）：课件库的列表 / 详情 / 删除 / HTML 直开。

- 分页倒序（按创建时间）；DELETE 同步删除落盘的课件 HTML 文件；
- GET /courses/{id}/html 直接返回课件 HTML 文本，供前端新窗口打开学习；
- SQLite 同步 IO 全部 to_thread 包裹；DB 操作失败返回安全默认值，不抛 500。
"""

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app.storage import course_db, folder_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/courses", tags=["courses"])


@router.get("")
async def list_courses(page: int = 1, page_size: int = 10):
    """分页倒序列出课件。"""
    return await asyncio.to_thread(course_db.list_courses, page, page_size)


@router.get("/{course_id}")
async def get_course(course_id: str):
    """课件详情（含大纲/页面/音频/练习快照）。"""
    record = await asyncio.to_thread(course_db.get_course, course_id)
    if not record:
        raise HTTPException(status_code=404, detail="课件不存在")
    return record


@router.delete("/{course_id}")
async def delete_course(course_id: str):
    """删除课件：删 DB 记录 + 落盘的课件 HTML 文件。"""
    record = await asyncio.to_thread(course_db.get_course, course_id)
    if not record:
        raise HTTPException(status_code=404, detail="课件不存在")

    removed = await asyncio.to_thread(course_db.delete_course, course_id)

    html_path = str(record.get("htmlPath") or "")
    if html_path:
        try:
            await asyncio.to_thread(Path(html_path).unlink, missing_ok=True)
        except Exception:
            logger.warning("课件文件删除失败：%s", html_path, exc_info=True)

    # 同步清掉文件夹归属，避免分组里残留已删除的课件
    await asyncio.to_thread(folder_db.remove_course, course_id)

    return {"deleted": bool(removed), "id": course_id}


@router.get("/{course_id}/html", response_class=HTMLResponse)
async def get_course_html(course_id: str):
    """直接返回课件 HTML 文本，前端新窗口打开即可学习。"""
    record = await asyncio.to_thread(course_db.get_course, course_id)
    if not record:
        raise HTTPException(status_code=404, detail="课件不存在")

    html_path = str(record.get("htmlPath") or "")
    if not html_path or not Path(html_path).is_file():
        raise HTTPException(status_code=404, detail="课件文件不存在")

    try:
        content = await asyncio.to_thread(
            lambda: Path(html_path).read_text(encoding="utf-8")
        )
    except Exception:
        logger.warning("课件文件读取失败：%s", html_path, exc_info=True)
        raise HTTPException(status_code=500, detail="课件文件读取失败")
    return HTMLResponse(content)
