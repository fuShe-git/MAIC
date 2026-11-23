"""联网搜索封装：Tavily（联网搜索服务）调用与结果规整。

专业名词解释：Tavily 是面向 LLM 应用的搜索 API，返回标题、链接和摘要；
asyncio.to_thread 把同步 SDK 调用放到线程池，避免阻塞 FastAPI 的事件循环。

约定：未配置密钥、依赖缺失或搜索失败时一律返回空列表，不阻断基于课件的回答。
"""

import asyncio
import logging
from typing import Any, Optional

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def available() -> bool:
    """是否配置了 Tavily 密钥（只看是否为空，不校验格式）。"""
    return bool(get_settings().tavily_api_key.strip())


def _build_client() -> Optional[Any]:
    api_key = get_settings().tavily_api_key.strip()
    if not api_key:
        return None
    try:
        from tavily import TavilyClient
    except ImportError:
        logger.warning("tavily-python 未安装，跳过联网搜索")
        return None
    return TavilyClient(api_key=api_key)


async def search(query: str, max_results: int = 3) -> list[dict[str, str]]:
    """搜索网页并返回 {title, url, snippet} 列表；任何异常都降级为空列表。"""
    text = (query or "").strip()
    if not text or not available():
        return []

    def _call() -> list[dict[str, Any]]:
        client = _build_client()
        if client is None:
            return []
        response = client.search(
            query=text,
            search_depth="basic",
            max_results=max_results,
            include_answer=False,
        )
        return list((response or {}).get("results") or [])

    try:
        raw = await asyncio.to_thread(_call)
    except Exception as exc:
        logger.warning("联网搜索失败（%s）", type(exc).__name__)
        return []

    items: list[dict[str, str]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "网页来源").strip()[:120]
        url = str(row.get("url") or "").strip()
        snippet = str(row.get("content") or row.get("snippet") or "").strip()[:600]
        if not title and not url:
            continue
        items.append({"title": title or "网页来源", "url": url, "snippet": snippet})
    return items[:max_results]
