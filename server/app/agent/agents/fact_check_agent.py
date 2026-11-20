"""事实核查子 agent（新增能力 2）：对照 RAG 资料核查大纲事实冲突。

产出 conflicts（[{ind, reason}]，追加合并 reducer）供主管决策：
有冲突且派发次数未超限 → 改派 outline_agent 重生成并级联清空下游产物；
无冲突 → 继续后续阶段。没有资料时不核查（无从对照），直接放行。
"""

import logging

from app.agent.nodes import FACT_CHECK_PROMPT, _invoke_text, _parse_json

logger = logging.getLogger(__name__)


async def fact_check_agent(state: dict) -> dict:
    """对照 docs 核查 catalogList 的事实性错误，产出 conflicts。"""
    docs = list(state.get("docs") or [])
    catalog = list(state.get("catalogList") or [])
    if not docs or not catalog:
        return {"conflicts": [], "stageDone": {"factcheck": True}}

    material = "\n\n".join(f"[资料{i}] {doc}" for i, doc in enumerate(docs[:6], 1))
    outline_text = "\n".join(
        f"模块 {module.get('ind')}：{'、'.join(module.get('title') or [])}"
        f"——{' '.join(module.get('context') or [])}"
        for module in catalog
        if isinstance(module, dict)
    )
    user_prompt = f"参考资料：\n{material}\n\n课件大纲：\n{outline_text}"

    try:
        raw = await _invoke_text(FACT_CHECK_PROMPT, user_prompt, temperature=0)
        data = _parse_json(raw)
    except Exception as exc:  # 核查失败不阻断生成
        logger.warning("事实核查调用失败，按无冲突放行：%s", exc)
        return {"conflicts": [], "stageDone": {"factcheck": True}}

    valid_inds = {
        int(module.get("ind") or 0) for module in catalog if isinstance(module, dict)
    }
    conflicts = []
    for item in data.get("conflicts") or []:
        if not isinstance(item, dict):
            continue
        try:
            ind = int(item.get("ind"))
        except (TypeError, ValueError):
            continue
        if ind not in valid_inds:
            continue
        conflicts.append(
            {"ind": ind, "reason": str(item.get("reason") or "与资料矛盾")[:100]}
        )

    if conflicts:
        logger.info("事实核查发现 %s 条冲突：%s", len(conflicts), conflicts)
    return {"conflicts": conflicts, "stageDone": {"factcheck": True}}
