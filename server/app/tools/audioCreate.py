"""TTS 音频合成：把一段讲稿用 edge-tts 念成 mp3 文件。

edge-tts 是微软 Edge「在线朗读」服务的 Python 客户端，需要联网。
本模块只做一次性合成：入参是清洗后的纯文本，出参是落盘路径。
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 晓晓：微软中文旗舰音色，长期在线；不要用偏门音色，微软会随时下架导致 NoAudioReceived
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"


async def tts_save(text: str, out_path: str | Path, voice: str = DEFAULT_VOICE) -> Path:
    """把 text 念成 mp3 写到 out_path。

    Args:
        text: 已清洗、可直接朗读的口播讲稿。
        out_path: mp3 落盘路径（父目录需已存在）。
        voice: edge-tts 音色，默认晓晓 zh-CN-XiaoxiaoNeural。

    Returns:
        Path: 实际写出的 mp3 路径。

    Raises:
        ValueError: 讲稿为空。
        RuntimeError: 缺少 edge-tts，或合成/落盘失败。异常信息带原文前 80 字方便定位。
    """
    cleaned = (text or "").strip()
    if not cleaned:
        raise ValueError("讲稿为空，无法合成音频")

    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError("缺少 edge-tts 依赖，请先执行 pip install edge-tts") from exc

    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    preview = cleaned[:80] + ("…" if len(cleaned) > 80 else "")
    try:
        # 使用edge_tts生成音频
        communicate = edge_tts.Communicate(cleaned, voice, rate="+0%")
        # 将音频保存在指定位置
        await communicate.save(str(target))
    except Exception as exc:
        hint = ""
        if "No audio was received" in str(exc):
            hint = "（常见原因：该音色已被微软下架，请换 zh-CN-XiaoxiaoNeural 等在线音色）"
        raise RuntimeError(f"TTS 合成失败（{preview}）：{exc}{hint}") from exc

    if not target.is_file() or target.stat().st_size == 0:
        raise RuntimeError(f"TTS 未产出音频文件（{preview}）")

    logger.info("TTS 合成完成：%s（%.1f KB）", target.name, target.stat().st_size / 1024)
    return target
