"""查询改写子 agent（新增能力 1）：主题 → 3~5 个角度的检索查询。

产出写入 state.queries（去重合并 reducer）；模型输出非法时降级为 [topic]。
"""

import logging

from app.agent.nodes import (
    QUERY_REWRITE_PROMPT,
    _as_str_list,
    _invoke_text,
    _parse_json,
)

logger = logging.getLogger(__name__)


async def query_rewrite_agent(state: dict) -> dict:
    """把讲解主题改写成多个检索查询，供 research_agent 逐个检索后合并去重。"""
    topic = (state.get("topic") or state.get("userIssue") or "").strip()
    if not topic:
        return {"queries": [], "stageDone": {"query": True}}

    raw = await _invoke_text(
        QUERY_REWRITE_PROMPT, f"讲解主题：{topic}", temperature=0.3
    )
    data = _parse_json(raw)
    queries = [q for q in _as_str_list(data.get("queries")) if q][:5]

    if not queries:  # 非法输出降级：直接用主题检索
        logger.warning("查询改写无合法输出，降级为 [topic]：%s", raw)
        queries = [topic]

    # 主题本身始终参与检索，保证覆盖
    if topic not in queries:
        queries.append(topic)
    return {"queries": queries, "stageDone": {"query": True}}
