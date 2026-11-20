"""主题判定 + RAG 检索子 agent（拆自旧 text_organization_node 前半）。

流程：先判断主题是否属于三国杀知识库范畴，属于才逐查询检索（to_thread 包裹，
多查询合并去重）；检索失败降级纯模型知识。完成后写 stageDone.research（docs 可能
为空列表，用阶段标记判定完成）。
"""

import logging

from app.agent.nodes import (
    _is_sanguosha_topic,
    _search_sanguosha_docs,
)

logger = logging.getLogger(__name__)


async def research_agent(state: dict) -> dict:
    """主题判定 + 多查询 RAG 检索，产出 docs（资料原文列表）。"""
    topic = (state.get("topic") or state.get("userIssue") or "").strip()
    if not topic:
        return {"docs": [], "stageDone": {"research": True}}

    queries = list(state.get("queries") or [])
    if not queries:
        queries = [topic]

    docs: list[str] = []
    if await _is_sanguosha_topic(topic):
        # 多查询逐个检索，结果统一去重（_merge_unique reducer 兜底再保一层）
        for query in queries:
            hits = await _search_sanguosha_docs(query)
            for doc in hits:
                if doc not in docs:
                    docs.append(doc)
        logger.info("RAG 检索完成：%s 个查询，命中 %s 条资料", len(queries), len(docs))

    return {"docs": docs, "stageDone": {"research": True}}
