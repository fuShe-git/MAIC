"""产物路径与对外 URL 的统一入口。

开发/生产共用一套地址：物理文件放在 MAIC_OUTPUT_DIR（默认 server/outputs），
对外只暴露 /api 开头的同源 URL，前端不再依赖 Vite 的 /@fs 开发期机制。
"""

from pathlib import Path
from urllib.parse import quote

from app.core.config import get_settings

_DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[2] / "outputs"


def resolve_output_dir() -> Path:
    """产物根目录：优先环境变量 MAIC_OUTPUT_DIR，缺省为 server/outputs。"""
    configured = (get_settings().maic_output_dir or "").strip()
    path = Path(configured).expanduser() if configured else _DEFAULT_OUTPUT_DIR
    return path.resolve()


OUTPUT_DIR = resolve_output_dir()
VIDEOS_DIR = OUTPUT_DIR / "videos"
AUDIO_DIR = OUTPUT_DIR / "audio"
DB_PATH = OUTPUT_DIR / "maic.db"


def api_prefix() -> str:
    """当前 API 前缀，去掉尾部斜杠，便于拼接 URL。"""
    return (get_settings().api_prefix or "").rstrip("/")


def artifact_url(path: str | Path, kind: str = "html") -> str:
    """把产物绝对路径转成浏览器可访问的同源 URL。

    - 空值返回空串，保持原有「未生成」语义；
    - 已经是 URL / 站内绝对路径的值原样透传（兼容历史数据与自定义前缀）；
    - 本地路径只取文件名，拼到 /generated/files/{name}/html|media。
      HTML 与 mp4/mp3 都走后端接口，开发（Vite 代理 /api）与生产（Nginx 反代 /api）均可用。
    """
    text = str(path or "").strip()
    if not text:
        return ""
    if text.startswith(("http://", "https://", "data:", "blob:", "/")):
        return text
    name = text.replace("\\", "/").rsplit("/", 1)[-1]
    if not name or "/" in name:
        return ""
    segment = "html" if kind == "html" else "media"
    return f"{api_prefix()}/generated/files/{quote(name, safe='')}/{segment}"
