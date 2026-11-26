"""页面生成子 agent（拆自旧 text_organization_node 的逐页生成段）。

- 保留逐页 asyncio.gather 并发与逐页进度上报（x/n）；
- 支持只重生成指定 ind（pageTargets 非空时，其余页复用已有产物）；
- 单页失败回退兜底模板并上报失败清单 failedPages（{ind, stage, error}）。
"""

import asyncio
import logging
from typing import Optional

from app.agent.nodes import (
    _build_page,
    _is_page_topic_relevant,
    _module_outline,
    _emit,
)

logger = logging.getLogger(__name__)


async def page_agent(state: dict) -> dict:
    """按大纲为每个模块生成 HTML 页面；产出 pageList 与失败清单。"""
    topic = (state.get("topic") or state.get("userIssue") or "").strip()
    theme = state.get("selectedTheme") or {}
    catalog = list(state.get("catalogList") or [])
    if not catalog:
        return {"pageList": [], "stageDone": {"pages": True}}

    existing = {
        int(page.get("ind") or 0): page
        for page in (state.get("pageList") or [])
        if isinstance(page, dict)
    }
    # 主管指定的目标 ind（重试/定向改页）；空表示补齐所有缺失页面
    targets = {int(ind) for ind in (state.get("pageTargets") or [])}

    todo = [
        module
        for module in catalog
        if int(module.get("ind") or 0) not in existing
        or (targets and int(module.get("ind") or 0) in targets)
    ]
    if not todo:
        return {"stageDone": {"pages": True}}

    total = len(todo)
    _emit("pages", "start", total=total, message="正在生成页面…")

    failures: list[dict] = []
    pages_done = 0

    async def _build_one(module: dict) -> tuple[int, Optional[str]]:
        nonlocal pages_done
        ind = int(module.get("ind") or 0)

        # 主题校验不通过（空内容/与课件主题无关）时不生成该页，也不记为生成失败
        # 新增页面在主管阶段已校验过并缓存结果，避免重复调用模型
        relevant = module.get("topicRelevant")
        if not isinstance(relevant, bool):
            relevant = await _is_page_topic_relevant(topic, module)
        if not relevant:
            logger.info("第 %s 页主题校验未通过，跳过生成", ind)
            _emit(
                "pages",
                "skipped",
                ind=ind,
                message=f"第 {ind} 页 · 主题不相关或内容为空，已跳过",
            )
            pages_done += 1
            _emit("pages", "progress", current=pages_done, total=total)
            return ind, None

        def _on_error(exc: Exception) -> None:
            failures.append({"ind": ind, "stage": "pages", "error": str(exc)[:200]})
            _emit("pages", "failed", ind=ind, message=f"第 {ind} 页 · 页面生成失败")

        page = await _build_page(topic, module, len(catalog), _on_error, theme)
        pages_done += 1
        _emit("pages", "progress", current=pages_done, total=total)
        return ind, page

    results = await asyncio.gather(*(_build_one(module) for module in todo))
    new_pages = {ind: html for ind, html in results if html is not None}
    skipped_inds = {ind for ind, html in results if html is None}

    # 汇总成 [{ind, context: 本部分大纲, htmlpage: HTML}]，顺序与 catalog 一致
    page_list = []
    for module in catalog:
        ind = int(module.get("ind") or 0)
        if ind in skipped_inds:
            continue
        html = new_pages.get(ind) or (existing.get(ind) or {}).get("htmlpage") or ""
        page_list.append(
            {"ind": ind, "context": _module_outline(module), "htmlpage": html}
        )

    return {
        "pageList": page_list,
        "failedPages": failures,
        "stageDone": {"pages": True},
    }
