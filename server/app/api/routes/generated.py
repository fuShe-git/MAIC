"""生成文件浏览接口：扫描 outputs/videos 下的课件 HTML 并对外提供访问。

- GET /generated/files 列出目录下全部课件（按修改时间倒序）；
- GET /generated/files/{file_name}/html 返回课件 HTML 文本，供播放器同源 iframe 加载
  （同源才能读取 contentDocument，实现页码同步与练习态检测）；
- GET /generated/files/{file_name}/cover 返回课件首屏封面 PNG（首屏渲染 → 落盘缓存）；
- GET /generated/files/{file_name}/pptx 导出图片版 PPTX（逐页截图 → python-pptx，按课件缓存）；
- PATCH /generated/files/{file_name} 修改课件备注名（落库，列表接口回带）；
- DELETE /generated/files/{file_name} 删除课件（HTML + 封面/PPTX 缓存 + 资产库记录）；
- 文件名走严格白名单（32 位 hex + .html），杜绝路径穿越；
- 文件 IO 用 asyncio.to_thread 包裹，异常只记日志并返回安全默认值，不抛 500。
"""

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

from app.core.paths import AUDIO_DIR, VIDEOS_DIR
from app.storage import course_db, folder_db
from app.tools.pptExport import cache_is_fresh, export_pptx

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/generated", tags=["generated"])

# 封面缓存目录：放在 videos 的隐藏子目录里，扫描 *.html 时不会误把它当课件
COVERS_DIR = VIDEOS_DIR / ".covers"
# PPTX 导出缓存：同样放在隐藏子目录，按课件文件名 stem 一一对应
PPTX_CACHE_DIR = VIDEOS_DIR / ".pptx"
# 与课件画布一致的 16:9 视口，截出来的首屏正好适合做卡片封面
_COVER_VIEWPORT = (1280, 720)
_COVER_TIMEOUT = 25.0
# 无头浏览器启动开销大：同一时刻只渲染一张封面，其余请求排队等缓存
_cover_lock = asyncio.Lock()
# PPT 导出要逐页渲染，开销更大：同一时刻只导出一份，其余请求排队等缓存
_pptx_lock = asyncio.Lock()
# 下载文件名里不能出现的字符（Windows 文件系统限制）
_UNSAFE_NAME_RE = re.compile(r'[\\/:*?"<>|\r\n]+')
_DOWNLOAD_NAME_MAX = 80

# 生成流程统一用 uuid4().hex + .html 命名，按此白名单校验可访问文件
_FILE_NAME_RE = re.compile(r"^[0-9a-f]{32}\.html$")
_MEDIA_NAME_RE = re.compile(r"^[0-9a-f]{32}\.(?:mp3|mp4)$")
# 从 HTML 头部提取 <title>（课件标题在文件前几行）
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
# 备注名长度上限：与前端输入框 maxLength 保持一致
_REMARK_MAX = 60


class RemarkPayload(BaseModel):
    """课件备注名；空串表示清除备注，回退课件自带标题。"""

    name: str = ""


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


def _cover_path(html_path: Path) -> Path:
    """课件首屏封面的缓存路径（按文件名 stem 一一对应）。"""
    return COVERS_DIR / f"{html_path.stem}.png"


def _pptx_cache_path(html_path: Path) -> Path:
    """PPTX 导出缓存的路径（按文件名 stem 一一对应）。"""
    return PPTX_CACHE_DIR / f"{html_path.stem}.pptx"


def _safe_download_name(title: str, fallback: str) -> str:
    """把课件标题收拾成可用的下载文件名（不含扩展名）。"""
    name = _UNSAFE_NAME_RE.sub(" ", title or "").strip()
    name = re.sub(r"\s+", " ", name)[:_DOWNLOAD_NAME_MAX].strip()
    return name or fallback


def _cover_is_fresh(cover: Path, html_path: Path) -> bool:
    """缓存是否可用：封面存在且不比课件 HTML 旧（课件被重写后自动失效）。"""
    try:
        return cover.is_file() and cover.stat().st_mtime >= html_path.stat().st_mtime
    except OSError:
        return False


async def _render_cover(html_path: Path, cover_path: Path) -> bytes:
    """把课件首屏渲染成 PNG 并写入缓存，返回 PNG 字节。

    复用 htmlCheck 的渲染能力（Playwright 优先，缺失时回退系统 Chrome/Edge）；
    渲染失败时向上抛，由调用方转成 503，前端会继续用渐变占位图。
    """
    html = await asyncio.to_thread(
        lambda: html_path.read_text(encoding="utf-8", errors="replace")
    )

    # 延迟导入：htmlCheck 会加载 .env 与模型依赖，避免影响接口启动速度
    from app.tools.htmlCheck import html_to_screenshot

    png, _metrics = await html_to_screenshot(
        html,
        viewport=_COVER_VIEWPORT,
        full_page=False,
        timeout=_COVER_TIMEOUT,
    )

    def _write_cache() -> None:
        cover_path.parent.mkdir(parents=True, exist_ok=True)
        cover_path.write_bytes(png)

    await asyncio.to_thread(_write_cache)
    return png


def _scan_files() -> dict:
    """同步扫描 videos 目录，返回课件列表（按修改时间倒序）。"""
    try:
        VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
        # 备注名一次读全：列表按文件扫描，备注单独存表
        remarks = course_db.get_remarks()
        items = []
        for path in VIDEOS_DIR.glob("*.html"):
            if not path.is_file() or not _FILE_NAME_RE.match(path.name):
                continue
            stat = path.stat()
            items.append({
                "id": path.stem,
                "fileName": path.name,
                "topic": _read_topic(path),
                "remark": remarks.get(path.stem, ""),
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


@router.get("/files/{file_name}/cover")
async def get_generated_cover(file_name: str):
    """课件首屏封面 PNG：首次请求现场渲染并缓存，之后直接命中缓存。"""
    if not _FILE_NAME_RE.match(file_name):
        raise HTTPException(status_code=400, detail="非法的课件文件名")
    html_path = VIDEOS_DIR / file_name
    if not await asyncio.to_thread(html_path.is_file):
        raise HTTPException(status_code=404, detail="课件文件不存在")

    cover_path = _cover_path(html_path)
    headers = {"Cache-Control": "public, max-age=86400"}

    if await asyncio.to_thread(_cover_is_fresh, cover_path, html_path):
        return FileResponse(cover_path, media_type="image/png", headers=headers)

    async with _cover_lock:
        # 排队期间可能已被前面的请求渲染好，二次确认避免重复截图
        if await asyncio.to_thread(_cover_is_fresh, cover_path, html_path):
            return FileResponse(cover_path, media_type="image/png", headers=headers)
        try:
            png = await _render_cover(html_path, cover_path)
        except Exception:
            logger.warning("课件封面渲染失败：%s", html_path, exc_info=True)
            raise HTTPException(status_code=503, detail="封面生成失败")

    return Response(content=png, media_type="image/png", headers=headers)


@router.get("/files/{file_name}/pptx")
async def download_generated_pptx(file_name: str):
    """把课件导出为图片版 PPTX 并下载；结果按课件缓存，重复下载直接命中。"""
    if not _FILE_NAME_RE.match(file_name):
        raise HTTPException(status_code=400, detail="非法的课件文件名")
    html_path = VIDEOS_DIR / file_name
    if not await asyncio.to_thread(html_path.is_file):
        raise HTTPException(status_code=404, detail="课件文件不存在")

    cache_path = _pptx_cache_path(html_path)
    if not await asyncio.to_thread(cache_is_fresh, cache_path, html_path):
        async with _pptx_lock:
            # 排队期间可能已被前面的请求导出，二次确认避免重复渲染
            if not await asyncio.to_thread(cache_is_fresh, cache_path, html_path):
                try:
                    html = await asyncio.to_thread(
                        lambda: html_path.read_text(encoding="utf-8", errors="replace")
                    )
                    title = await asyncio.to_thread(_read_topic, html_path)
                    await export_pptx(
                        html, PPTX_CACHE_DIR, title, file_stem=html_path.stem
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=503, detail=str(exc))
                except Exception:
                    logger.warning("PPT 导出失败：%s", html_path, exc_info=True)
                    raise HTTPException(status_code=503, detail="PPT 导出失败，请稍后重试")

    if not await asyncio.to_thread(cache_path.is_file):
        raise HTTPException(status_code=503, detail="PPT 导出失败，请稍后重试")

    # 下载名优先用课件标题；ASCII 回退名用稳定的文件 stem，避免响应头乱码
    title = await asyncio.to_thread(_read_topic, html_path)
    download_name = _safe_download_name(title, html_path.stem)
    headers = {
        "Content-Disposition": (
            f'attachment; filename="{html_path.stem}.pptx"; '
            f"filename*=UTF-8''{quote(download_name + '.pptx', safe='')}"
        ),
        "Cache-Control": "no-store",
    }
    return FileResponse(
        cache_path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers=headers,
    )


@router.patch("/files/{file_name}")
async def update_generated_remark(file_name: str, payload: RemarkPayload):
    """修改课件备注名：落库后，列表接口会带出新名字。"""
    if not _FILE_NAME_RE.match(file_name):
        raise HTTPException(status_code=400, detail="非法的课件文件名")
    html_path = VIDEOS_DIR / file_name
    if not await asyncio.to_thread(html_path.is_file):
        raise HTTPException(status_code=404, detail="课件文件不存在")

    # 超长直接截断而不是报错：前端 maxLength 已限长，这里只兜底
    remark = (payload.name or "").strip()[:_REMARK_MAX]
    ok = await asyncio.to_thread(course_db.set_remark, html_path.stem, remark)
    if not ok:
        raise HTTPException(status_code=500, detail="备注名保存失败")
    return {"id": html_path.stem, "remark": remark}


@router.delete("/files/{file_name}")
async def delete_generated_file(file_name: str):
    """删除课件：HTML 文件 + 封面缓存 + 资产库记录。

    列表是按文件扫描出来的，资产库记录可能不存在（历史文件），
    因此删库只做尽力而为，不影响文件删除结果。
    """
    if not _FILE_NAME_RE.match(file_name):
        raise HTTPException(status_code=400, detail="非法的课件文件名")
    html_path = VIDEOS_DIR / file_name
    if not await asyncio.to_thread(html_path.is_file):
        raise HTTPException(status_code=404, detail="课件文件不存在")

    def _remove_files() -> None:
        html_path.unlink(missing_ok=True)
        # 封面/PPTX 缓存与该课件一一对应，一并清掉避免残留
        _cover_path(html_path).unlink(missing_ok=True)
        _pptx_cache_path(html_path).unlink(missing_ok=True)

    try:
        await asyncio.to_thread(_remove_files)
    except Exception:
        logger.warning("课件文件删除失败：%s", html_path, exc_info=True)
        raise HTTPException(status_code=500, detail="课件文件删除失败")

    # 资产库记录删除失败只记日志：文件已经没了，前端列表会正常刷新
    await asyncio.to_thread(course_db.delete_course, html_path.stem)
    # 同步清掉文件夹归属，避免文件夹里残留不存在的课件
    await asyncio.to_thread(folder_db.remove_course, html_path.stem)
    return {"deleted": True, "id": html_path.stem}


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
