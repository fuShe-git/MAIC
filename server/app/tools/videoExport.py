"""把一组静态页面截图与逐页音频合成为一个 MP4 视频。

路线（静态分页截帧，非实时录制）：
    页面是纯静态的，每页只截 1 帧 → FFmpeg `-loop 1 -t {时长}` 生成该页视频分段
    （缺音频的页配一段静音轨，保证所有分段流结构一致）→ 各分段 concat 成整条视频。
    这样画面停留时长与语音长度严格对齐，总耗时≈编码耗时而非课件时长。

对外可用函数：
    read_duration()      读 mp3 时长（mutagen），失败抛异常由调用方回退
    estimate_duration()  按字数估算时长，读时长失败或缺少音频时的兜底
    render_page_frames() 把多份页面 HTML 渲染成 PNG 字节列表（复用 htmlCheck 双引擎）
    export_mp4()         把帧 + 音频 + 时长合成为 mp4，返回落盘路径
"""

import logging
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger(__name__)

DEFAULT_FPS = 24
TAIL_PAD = 0.5             # 每页结尾补半秒留白，避免末字被视频截断
DEFAULT_PAGE_DURATION = 8.0   # 音频与文本都缺失时的兜底停留秒数
CHARS_PER_SECOND = 4.5     # 中文旁白约 4.5 字/秒，用于时长估算


def read_duration(path: str | Path) -> float:
    """读取 mp3 的时长（秒）。依赖缺失或读取失败时抛异常，由调用方回退估算。"""
    try:
        from mutagen.mp3 import MP3
    except ImportError as exc:
        raise RuntimeError("缺少 mutagen 依赖，请先执行 pip install mutagen") from exc
    return float(MP3(str(path)).info.length)


def estimate_duration(text: str) -> float:
    """按字数估算旁白时长（秒）；文本为空时给一个保守的兜底值。"""
    chars = len((text or "").strip())
    if chars == 0:
        return DEFAULT_PAGE_DURATION
    return max(3.0, round(chars / CHARS_PER_SECOND, 2))


def _resolve_ffmpeg() -> str:
    """优先用系统 ffmpeg，找不到再用 imageio-ffmpeg 自带的静态二进制。"""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise RuntimeError(
            "未找到 ffmpeg：请安装 ffmpeg，或执行 pip install imageio-ffmpeg"
        ) from exc


def _run(cmd: list[str], cwd: str | None = None) -> None:
    """运行命令"""
    logger.debug("ffmpeg: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, check=False, cwd=cwd)
    if result.returncode != 0:
        stderr = (result.stderr or b"").decode("utf-8", errors="ignore").strip()
        raise RuntimeError(f"ffmpeg 失败（exit {result.returncode}）：{stderr[-500:]}")


async def render_page_frames(htmls: list[str]) -> list[bytes | None]:
    """把多份页面 HTML 渲染成 PNG 字节，顺序与入参一致；单页失败置 None 不阻断。"""
    from app.tools.htmlCheck import html_to_screenshot

    frames: list[bytes | None] = []
    for index, html in enumerate(htmls):
        if not (html or "").strip():
            frames.append(None)
            continue
        try:
            png, _ = await html_to_screenshot(html)
            frames.append(png)
        except Exception as exc:
            logger.warning("第 %s 页截图失败，跳过该页：%s", index + 1, exc)
            frames.append(None)
    return frames


def export_mp4(
    frames: list[bytes | None],
    audio_paths: list[str],
    durations: list[float],
    out_dir: str | Path,
    fps: int = DEFAULT_FPS,
    tail_pad: float = TAIL_PAD,
) -> Path:
    """把帧、音频、时长合成为一个 MP4。
    三个列表按页对齐（长度相同）；某页帧为 None 时整页跳过。
    输出文件按 uuid 命名，临时工作目录在退出时清理。
    Args:
        frames: 每页画面的 PNG 截图字节列表，与页一一对应；某页为 None 表示截图失败，该页整页跳过。
        audio_paths: 每页旁白 mp3 的文件路径列表，与页一一对应；空串或文件不存在的页配静音轨。
        durations: 每页画面停留时长（秒）列表，与页一一对应；决定该页视频分段长度（+ tail_pad 留白）。
        out_dir: 输出目录，最终 MP4 与临时工作目录（.tmp_{uid}）都建在它下面，不存在会自动创建。
        fps: 帧率（frames per second，每秒画面帧数），默认 24；静态图按此帧率重复成视频分段。
        tail_pad: 每页结尾追加的留白秒数，默认 0.5，避免旁白末字被视频截断。
    Returns:
        Path: 合成后的最终 MP4 文件路径。
    Raises:
        ValueError: frames 为空（没有可用截图）。
        RuntimeError: 未找到 ffmpeg，或任一 ffmpeg 子命令执行失败。
    """
    if not frames:
        raise ValueError("没有可用的页面截图，无法导出视频")

    ffmpeg = _resolve_ffmpeg()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    uid = uuid4().hex
    work = out_dir / f".tmp_{uid}"
    work.mkdir(parents=True, exist_ok=True)
    final = out_dir / f"{uid}.mp4"

    segments: list[Path] = []
    try:
        for index, (frame, audio, duration) in enumerate(zip(frames, audio_paths, durations)):
            if frame is None:
                continue
            seg_dur = max(1.0, float(duration or 0) + tail_pad)
            img = work / f"img_{index:03d}.png"
            img.write_bytes(frame)

            # 一个普通列表，用来执行ffmpeg 命令
            cmd = [
                ffmpeg, "-y",
                "-loop", "1", "-framerate", str(fps), "-t", f"{seg_dur:.3f}", "-i", str(img),
            ]
            has_audio = bool(audio) and Path(audio).is_file()
            if has_audio:
                cmd += ["-i", str(audio)]
            else:
                cmd += ["-f", "lavfi", "-t", f"{seg_dur:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
            seg = work / f"seg_{index:03d}.mp4"
            cmd += [
                "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p", "-r", str(fps),
                "-c:a", "aac", "-b:a", "192k", "-t", f"{seg_dur:.3f}",
                str(seg),
            ]
            # 执行ffmpeg 命令，将每一段音频+图片生成一小段视频
            _run(cmd)
            segments.append(seg)

        if not segments:
            raise RuntimeError("所有页面均截图失败，没有生成任何视频分段")

        concat_file = work / "concat.txt"
        concat_file.write_text(
            "".join(f"file '{seg.name}'\n" for seg in segments), encoding="utf-8"
        )
        # 分段流结构一致，concat 直接复制流；+faststart 把 moov 提到文件头，利于网页边下边播
        # 将上一次_run得到的多个视频分段合并成一个视频
        _run(
            [
                ffmpeg, "-y", "-f", "concat", "-safe", "0",
                "-i", str(concat_file), "-c", "copy", "-movflags", "+faststart",
                str(final),
            ],
            cwd=str(work),
        )
    finally:
        # 删除过程中生成的临时目录和文件
        shutil.rmtree(work, ignore_errors=True)

    logger.info("视频导出完成：%s", final)
    return final
