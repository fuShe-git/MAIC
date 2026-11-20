"""音频合成子 agent（迁自旧 audio_node）。

台词直接复用大纲里的 context（本就是可朗读的口播讲稿），不调模型二次生成；
各模块并发合成，单页 TTS 失败上报失败清单（stage=audio）并走时长估算兜底，
不阻断整体。支持只重跑指定 ind（pageTargets，与页面重试同一口径）。
"""

import asyncio
import logging
from uuid import uuid4

from app.agent.nodes import OUTPUT_DIR, _clean_script, _emit
from app.tools.audioCreate import tts_save
from app.tools.videoExport import estimate_duration, read_duration

logger = logging.getLogger(__name__)


async def audio_agent(state: dict) -> dict:
    """把每个模块的口播讲稿清洗后合成 TTS 音频，并读回时长。"""
    catalog: list = state.get("catalogList") or []
    if not catalog:
        return {"audioList": [], "stageDone": {"audio": True}}

    existing = {
        int(item.get("ind") or 0): item
        for item in (state.get("audioList") or [])
        if isinstance(item, dict)
    }
    targets = {int(ind) for ind in (state.get("pageTargets") or [])}

    todo = [
        module
        for module in catalog
        if int(module.get("ind") or 0) not in existing
        or (targets and int(module.get("ind") or 0) in targets)
    ]
    if not todo:
        return {"stageDone": {"audio": True}}

    total = len(todo)
    _emit("audio", "start", total=total, message="正在合成音频…")
    audio_dir = OUTPUT_DIR / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    failures: list[dict] = []
    audio_done = 0

    async def _synthesize(module: dict) -> tuple[int, dict]:
        nonlocal audio_done
        ind = int(module.get("ind") or 0)
        script = _clean_script("\n".join(module.get("context") or []))
        audio_path = ""
        duration = estimate_duration(script)

        if script:
            target = audio_dir / f"{uuid4().hex}.mp3"
            try:
                await tts_save(script, target)
                audio_path = str(target)
                try:
                    duration = read_duration(target)
                except Exception as exc:
                    logger.warning("第 %s 页音频时长读取失败，按字数估算：%s", ind, exc)
            except Exception as exc:
                # 单页失败进失败清单，该页无旁白但不阻断整体
                logger.warning("第 %s 页 TTS 合成失败，该页将无旁白：%s", ind, exc)
                failures.append({"ind": ind, "stage": "audio", "error": str(exc)[:200]})
                _emit("audio", "failed", ind=ind, message=f"第 {ind} 页 · 音频合成失败")

        audio_done += 1
        _emit("audio", "progress", current=audio_done, total=total)
        return ind, {
            "ind": ind,
            "context": script,
            "audio_path": audio_path,
            "duration": duration,
        }

    results = await asyncio.gather(*(_synthesize(module) for module in todo))
    new_items = dict(results)

    audio_list = [
        new_items.get(int(module.get("ind") or 0))
        or existing.get(int(module.get("ind") or 0))
        or {
            "ind": int(module.get("ind") or 0),
            "context": _clean_script("\n".join(module.get("context") or [])),
            "audio_path": "",
            "duration": estimate_duration(""),
        }
        for module in catalog
    ]

    return {
        "audioList": audio_list,
        "failedPages": failures,
        "stageDone": {"audio": True},
    }
