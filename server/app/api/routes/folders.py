"""课件文件夹接口：分组的持久化存储（SQLite）。

- GET    /folders             列出全部文件夹（含课程顺序）；
- POST   /folders             新建文件夹，可同时把一门课程移进去；
- PATCH  /folders/{id}        重命名；
- DELETE /folders/{id}        只删文件夹，课程回到未分组；
- PUT    /folders/assignment  设置某门课程所属文件夹（空串 = 移出）。

SQLite 同步 IO 全部用 to_thread 包裹，异常由存储层兜底，接口不抛 500。
"""

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.storage import folder_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/folders", tags=["folders"])

# 文件夹名长度上限：与前端输入框 maxLength 对齐
_NAME_MAX = 40


class FolderCreate(BaseModel):
    """新建文件夹；courseId 非空时同时把该课程移进去。"""

    name: str = ""
    courseId: str = ""


class FolderRename(BaseModel):
    name: str = ""


class AssignmentPayload(BaseModel):
    courseId: str = ""
    folderId: str = ""


def _clean_name(raw: str) -> str:
    """去掉首尾空格并限长；空名字直接 400，避免出现无名文件夹。"""
    name = (raw or "").strip()[:_NAME_MAX]
    if not name:
        raise HTTPException(status_code=400, detail="文件夹名称不能为空")
    return name


@router.get("")
async def list_folders():
    """列出全部文件夹（新建的在前）。"""
    return await asyncio.to_thread(folder_db.list_folders)


@router.post("")
async def create_folder(payload: FolderCreate):
    """新建文件夹；带上 courseId 时顺手完成归类（少一次往返）。"""
    name = _clean_name(payload.name)
    course_id = (payload.courseId or "").strip()
    folder = await asyncio.to_thread(
        folder_db.create_folder, name, [course_id] if course_id else []
    )
    if not folder:
        raise HTTPException(status_code=500, detail="文件夹创建失败")
    return folder


@router.put("/assignment")
async def assign_course(payload: AssignmentPayload):
    """设置课程归属：folderId 为空串表示移出所有文件夹。"""
    course_id = (payload.courseId or "").strip()
    folder_id = (payload.folderId or "").strip()
    if not course_id:
        raise HTTPException(status_code=400, detail="缺少课件 id")
    ok = await asyncio.to_thread(folder_db.assign_course, course_id, folder_id)
    if not ok:
        raise HTTPException(status_code=404, detail="文件夹不存在")
    return {"courseId": course_id, "folderId": folder_id}


@router.patch("/{folder_id}")
async def rename_folder(folder_id: str, payload: FolderRename):
    """重命名文件夹。"""
    name = _clean_name(payload.name)
    folder = await asyncio.to_thread(folder_db.rename_folder, folder_id, name)
    if not folder:
        raise HTTPException(status_code=404, detail="文件夹不存在")
    return folder


@router.delete("/{folder_id}")
async def delete_folder(folder_id: str):
    """只删除文件夹本身；里面的课程回到「未分组」，课件不动。"""
    deleted = await asyncio.to_thread(folder_db.delete_folder, folder_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="文件夹不存在")
    return {"deleted": True, "id": folder_id}
