"""PPT 样式主题生成 agent。

这里的“主题”指课件视觉样式（配色、版式、字体层级），不是页面顶部的明/暗主题。
一次调用生成 3 个候选；模型失败或 JSON 不合法时返回内置样式并用 degraded=True 标记。
"""

import json
import logging
import re
from typing import Optional

from app.agent.nodes import _invoke_text, _parse_json

logger = logging.getLogger(__name__)

THEME_PROMPT = """你是 MAIC 的课件视觉设计师，请为这堂课设计 3 个互不相同的 PPT 样式模板。

要求：
1. 每个模板包含 id、name、description、previewColors、stylePrompt 五个字段；
2. name 是 4~8 个字的中文风格名，description 是一句话说明适用场景；
3. previewColors 含 background/primary/accent/text 四个字段，值必须是 6 位十六进制颜色（例如 #2F5BEA）；
4. stylePrompt 是给页面生成模型看的视觉规范，写清背景色、主色、卡片样式、标题层级和底部横幅风格；
5. 三个模板要在配色和气质上明显不同，例如学术蓝、青绿笔记、暮紫剧场；
6. 所有文字用中文，不要 markdown 符号；只输出 JSON，不要解释文字或代码块围栏。

输出格式：
{"themes": [{"id": "style-a", "name": "深蓝课堂", "description": "适合知识点密集的讲解", "previewColors": {"background": "#F4F7FF", "primary": "#2F5BEA", "accent": "#6C8CFF", "text": "#1B2440"}, "stylePrompt": "使用深蓝与白色为主的 academic 版式……"}]}"""

FALLBACK_THEMES: list[dict] = [
    {
        "id": "fallback-ink-blue",
        "name": "深蓝课堂",
        "description": "沉稳的蓝白 academic 风格，适合知识点密集的讲解。",
        "previewColors": {"background": "#F4F7FF", "primary": "#2F5BEA", "accent": "#6C8CFF", "text": "#1B2440"},
        "stylePrompt": "使用深蓝与白色为主的 academic 版式：浅蓝背景 #F4F7FF，主色 #2F5BEA，标题加粗，信息卡片为半透明白色并带细蓝边，底部横幅使用主色。",
    },
    {
        "id": "fallback-forest",
        "name": "青绿笔记",
        "description": "清爽的青绿笔记风，适合循序渐进的基础教学。",
        "previewColors": {"background": "#F2FBF7", "primary": "#147D64", "accent": "#4FC3A1", "text": "#17342C"},
        "stylePrompt": "使用青绿笔记风格：背景 #F2FBF7，主色 #147D64，卡片带左侧色条与浅绿描边，标题清新有力，底部横幅用深绿色。",
    },
    {
        "id": "fallback-dusk",
        "name": "暮紫剧场",
        "description": "带有舞台感的紫色渐变风格，适合故事化与场景化课程。",
        "previewColors": {"background": "#F7F3FF", "primary": "#6B3DE0", "accent": "#B69CFF", "text": "#2B1B4D"},
        "stylePrompt": "使用暮紫剧场风格：背景 #F7F3FF，主色 #6B3DE0，标题带轻微渐变，卡片为白色加紫色阴影，底部横幅使用紫到深紫的渐变。",
    },
]

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _color(value: object, fallback: str) -> str:
    """只接受 6 位十六进制颜色，避免模型输出把 CSS 注入页面。"""
    text = str(value or "").strip()
    return text if _HEX_COLOR.match(text) else fallback


def _normalize_themes(data: dict, fallback: list[dict]) -> tuple[list[dict], bool]:
    """校验模型输出；不足 3 个时用内置样式补齐并标记降级。"""
    rows = data.get("themes") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return [dict(item) for item in fallback], True

    themes: list[dict] = []
    used_ids: set[str] = set()
    for index, row in enumerate(rows[:3]):
        if not isinstance(row, dict):
            continue
        colors = row.get("previewColors") if isinstance(row.get("previewColors"), dict) else {}
        base = fallback[index % len(fallback)]
        style_prompt = str(row.get("stylePrompt") or "").strip()
        name = str(row.get("name") or "").strip()
        if not style_prompt or not name:
            continue
        theme_id = str(row.get("id") or f"theme-{index + 1}").strip() or f"theme-{index + 1}"
        if theme_id in used_ids:
            theme_id = f"{theme_id}-{index + 1}"
        used_ids.add(theme_id)
        themes.append(
            {
                "id": theme_id,
                "name": name[:20],
                "description": str(row.get("description") or "").strip()[:120] or base["description"],
                "previewColors": {
                    "background": _color(colors.get("background"), base["previewColors"]["background"]),
                    "primary": _color(colors.get("primary"), base["previewColors"]["primary"]),
                    "accent": _color(colors.get("accent"), base["previewColors"]["accent"]),
                    "text": _color(colors.get("text"), base["previewColors"]["text"]),
                },
                "stylePrompt": style_prompt[:600],
            }
        )

    if len(themes) < 3:
        existing = {item["id"] for item in themes}
        for item in fallback:
            if len(themes) >= 3:
                break
            if item["id"] not in existing:
                themes.append(dict(item))
        return themes, True
    return themes, False


async def generate_theme_templates(
    message: str,
    feedback: str = "",
    current_themes: Optional[list[dict]] = None,
) -> tuple[list[dict], bool]:
    """生成 3 个 PPT 样式模板，返回 (模板列表, 是否降级)。"""
    parts = [f"课程主题：{message}"]
    if feedback:
        parts.append(f"用户修改意见：{feedback}")
    if current_themes:
        parts.append("当前候选样式：" + json.dumps(current_themes, ensure_ascii=False)[:2000])
        parts.append("请根据修改意见调整这些样式；没有提到的部分保持原样。")
    else:
        parts.append("请从零设计 3 个风格差异明显的模板。")

    try:
        raw = await _invoke_text(THEME_PROMPT, "\n\n".join(parts), temperature=0.6)
        themes, degraded = _normalize_themes(_parse_json(raw), FALLBACK_THEMES)
        if degraded:
            logger.warning("样式模板解析不完整，已用内置样式补齐")
        return themes, degraded
    except Exception as exc:
        logger.warning("样式模板生成失败，使用内置样式：%s", exc)
        return [dict(item) for item in FALLBACK_THEMES], True
