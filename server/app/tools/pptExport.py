"""把课件 HTML 的每一页导出成图片版 PPTX。

课件 HTML 由 courseAssemble.build_course_html 组装：每页是
`<iframe class="page" srcdoc="...">`，页面内容经 html.escape(quote=True) 转义后
放在 srcdoc 属性里。导出流程：
    提取 srcdoc → 还原每页 HTML → 逐页截图（复用 videoExport 的渲染链路）
    → python-pptx 拼成 16:9 全幅图片幻灯片。

对外可用函数：
    extract_page_htmls()  从课件 HTML 中按顺序提取每页完整 HTML
    build_pptx()          把 PNG 字节列表写成 16:9 全幅图片的 .pptx
    export_pptx()         提取 + 渲染 + 生成，返回落盘路径
"""

import asyncio
import logging
import os
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from app.tools.videoExport import render_page_frames

logger = logging.getLogger(__name__)

# 逐页截图沿用 htmlCheck.DEFAULT_VIEWPORT 的 1280×720（16:9），正好铺满幻灯片
# PowerPoint 16:9 画布：13.333 × 7.5 英寸
SLIDE_WIDTH_INCHES = 13.333
SLIDE_HEIGHT_INCHES = 7.5


class _PageIframeParser(HTMLParser):
    """提取课件容器里 class 含 page 的 iframe 的 srcdoc。

    HTMLParser 会自动反转义属性值，因此拿到的 srcdoc 就是每页的原始 HTML，
    不需要再手动 html.unescape（也就不会把页面里真实的 &amp; 二次还原错）。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.pages: list[str] = []
        # 是否见过课件页 iframe：用于区分「旧格式单页 HTML」与「课件页全为空」
        self.saw_page = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "iframe":
            return
        attr_map = {name.lower(): (value or "") for name, value in attrs}
        if "page" not in attr_map.get("class", "").split():
            return
        self.saw_page = True
        srcdoc = attr_map.get("srcdoc", "")
        if srcdoc.strip():
            self.pages.append(srcdoc)


def extract_page_htmls(course_html: str) -> list[str]:
    """从课件 HTML 中按文档顺序提取每页完整 HTML。

    Args:
        course_html: 课件容器 HTML 文本。

    Returns:
        每页 HTML 列表；课件页面全为空时返回空列表。
        没有找到课件页 iframe 时（旧格式或直接传入单页 HTML），整份文档作为一页返回。
    """
    text = course_html or ""
    parser = _PageIframeParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        logger.warning("课件页面解析失败，按单页兜底", exc_info=True)

    if parser.saw_page:
        return parser.pages
    return [text] if text.strip() else []


def build_pptx(
    frames: list[bytes],
    out_path: str | Path,
    title: str = "",
) -> Path:
    """把 PNG 字节列表按顺序写成 16:9 全幅图片幻灯片。

    Args:
        frames: 每页画面的 PNG 字节，顺序即幻灯片顺序；调用方需先过滤失败页。
        out_path: 输出 .pptx 路径，父目录不存在时自动创建。
        title: 写入文档核心属性的标题。

    Returns:
        Path: 实际落盘的 .pptx 路径。

    Raises:
        ValueError: frames 为空。
        RuntimeError: 未安装 python-pptx。
    """
    if not frames:
        raise ValueError("没有可用的页面截图，无法导出 PPT")

    try:
        from pptx import Presentation
        from pptx.util import Inches
    except ImportError as exc:
        raise RuntimeError("缺少 python-pptx 依赖，请先执行 pip install python-pptx") from exc

    presentation = Presentation()
    presentation.slide_width = Inches(SLIDE_WIDTH_INCHES)
    presentation.slide_height = Inches(SLIDE_HEIGHT_INCHES)
    # 空白版式：没有标题/正文占位符，避免默认占位符压在图片上
    blank_layout = presentation.slide_layouts[6]

    for frame in frames:
        slide = presentation.slides.add_slide(blank_layout)
        slide.shapes.add_picture(
            BytesIO(frame),
            0,
            0,
            width=presentation.slide_width,
            height=presentation.slide_height,
        )

    if title:
        presentation.core_properties.title = title
    presentation.core_properties.author = "MAIC"

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # 先写临时文件再原子替换：生成中途失败时不会留下半截缓存
    tmp_path = out_path.with_name(out_path.name + ".tmp")
    try:
        presentation.save(str(tmp_path))
        os.replace(tmp_path, out_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    return out_path


async def export_pptx(
    course_html: str,
    out_dir: str | Path,
    title: str = "",
    file_stem: str | None = None,
) -> Path:
    """把课件导出成图片版 PPTX 并落盘。

    Args:
        course_html: 课件容器 HTML 文本。
        out_dir: 输出目录，不存在时自动创建。
        title: 写入 PPT 文档属性的标题。
        file_stem: 输出文件名（不含扩展名）；为空时用 uuid，避免并发覆盖。

    Returns:
        Path: 生成的 .pptx 路径。

    Raises:
        ValueError: 课件里没有可用页面。
        RuntimeError: 所有页面截图失败，或缺少 python-pptx。
    """
    pages = extract_page_htmls(course_html)
    if not pages:
        raise ValueError("课件中没有找到可导出的页面")

    # 复用视频导出的逐页渲染：单页失败置 None，不阻断其他页
    frames = await render_page_frames(pages)
    usable = [frame for frame in frames if frame]
    if not usable:
        raise RuntimeError("所有页面渲染失败，无法导出 PPT")

    out_dir = Path(out_dir)
    out_path = out_dir / f"{file_stem or uuid4().hex}.pptx"
    await asyncio.to_thread(build_pptx, usable, out_path, title)
    logger.info("PPT 导出完成：%s（%d/%d 页）", out_path, len(usable), len(pages))
    return out_path

