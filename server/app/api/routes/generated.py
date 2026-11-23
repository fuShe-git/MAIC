"""生成文件浏览接口：扫描 outputs/videos 下的课件 HTML 并对外提供访问。

- GET /generated/files 列出目录下全部课件（按修改时间倒序）；
- GET /generated/files/{file_name}/html 返回课件 HTML 文本，供播放器同源 iframe 加载
  （同源才能读取 contentDocument，实现页码同步与练习态检测）；
- 文件名走严格白名单（32 位 hex + .html），杜绝路径穿越；
- 文件 IO 用 asyncio.to_thread 包裹，异常只记日志并返回安全默认值，不抛 500。
"""

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from app.core.paths import AUDIO_DIR, VIDEOS_DIR

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/generated", tags=["generated"])

# 生成流程统一用 uuid4().hex + .html 命名，按此白名单校验可访问文件
_FILE_NAME_RE = re.compile(r"^[0-9a-f]{32}\.html$")
_MEDIA_NAME_RE = re.compile(r"^[0-9a-f]{32}\.(?:mp3|mp4)$")
# 从 HTML 头部提取 <title>（课件标题在文件前几行）
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _read_topic(path: Path) -> str:
    """读课件 <title> 作为展示标题；读取失败回退文件名。"""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            head = handle.read(8192)
        match = _TITLE_RE.search(head)
        if match:
            title = re.sub(r"\s+", " ", match.group(1)).strip()
            if title:
                return title
    except Exception:
        logger.debug("课件标题读取失败：%s", path, exc_info=True)
    return path.stem


def _scan_files() -> dict:
    """同步扫描 videos 目录，返回课件列表（按修改时间倒序）。"""
    try:
        VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
        items = []
        for path in VIDEOS_DIR.glob("*.html"):
            if not path.is_file() or not _FILE_NAME_RE.match(path.name):
                continue
            stat = path.stat()
            items.append({
                "id": path.stem,
                "fileName": path.name,
                "topic": _read_topic(path),
                "createdAt": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                "size": int(stat.st_size),
            })
        items.sort(key=lambda row: row["createdAt"], reverse=True)
        return {"total": len(items), "items": items}
    except Exception:
        logger.warning("生成文件目录扫描失败", exc_info=True)
        return {"total": 0, "items": []}


@router.get("/files")
async def list_generated_files():
    """列出 videos 目录下所有生成的课件。"""
    return await asyncio.to_thread(_scan_files)


@router.get("/files/{file_name}/html", response_class=HTMLResponse)
async def get_generated_html(file_name: str):
    """返回课件 HTML 文本；非法文件名 400、文件缺失 404。"""
    if not _FILE_NAME_RE.match(file_name):
        raise HTTPException(status_code=400, detail="非法的课件文件名")
    path = VIDEOS_DIR / file_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="课件文件不存在")
    try:
        content = await asyncio.to_thread(
            lambda: path.read_text(encoding="utf-8", errors="replace")
        )
    except Exception:
        logger.warning("课件文件读取失败：%s", path, exc_info=True)
        raise HTTPException(status_code=500, detail="课件文件读取失败")
    return HTMLResponse(content)


@router.get("/files/{file_name}/media")
async def get_generated_media(file_name: str):
    """返回生成的 mp3/mp4；文件名白名单 + 目录限定，避免任意文件读取。"""
    if not _MEDIA_NAME_RE.match(file_name):
        raise HTTPException(status_code=400, detail="非法的媒体文件名")

    path = VIDEOS_DIR / file_name
    if not await asyncio.to_thread(path.is_file):
        path = AUDIO_DIR / file_name
        if not await asyncio.to_thread(path.is_file):
            raise HTTPException(status_code=404, detail="媒体文件不存在")

    media_type = "video/mp4" if path.suffix.lower() == ".mp4" else "audio/mpeg"
    return FileResponse(path, media_type=media_type)
