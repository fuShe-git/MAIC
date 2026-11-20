"""意图分析子 agent（迁自旧 content_analysis_node，增多轮意图分支）。

产出：needLecture / topic / reply / intent / reviseInds。
intent 枚举：new_lecture（新课）| continue（续作）| revise_page（定向改页）|
quiz_only（只要测验）| chat（闲聊，不需要课件）。
多轮增量：会话历史进入 user_prompt，供模型判断 continue / revise_page / quiz_only。
"""

import logging

from app.agent.nodes import (
    CONTENT_ANALYSIS_PROMPT,
    DEFAULT_GREETING,
    _emit,
    _invoke_text,
    _parse_json,
)

logger = logging.getLogger(__name__)

# 合法意图枚举；模型给出非法值时按 new_lecture 兜底
VALID_INTENTS = {"new_lecture", "continue", "revise_page", "quiz_only", "chat"}


def _format_history(history: list[dict]) -> str:
    """把会话历史拼成「用户：… / 助手：…」的文本段，没有历史返回空串。"""
    lines = []
    for item in history or []:
        role = "用户" if item.get("role") == "user" else "助手"
        content = str(item.get("content") or "").strip()
        if content:
            lines.append(f"{role}：{content[:200]}")
    return "\n".join(lines)


async def intent_agent(state: dict) -> dict:
    """分析用户输入：是否需要讲解、讲解主题、多轮意图。"""
    _emit("check", "start", message="开始检查是否可以讲解")
    user_issue = (state.get("userIssue") or "").strip()

    # 空输入不必走模型，直接给引导语
    if not user_issue:
        _emit("check", "done")
        return {
            "needLecture": False,
            "reply": DEFAULT_GREETING,
            "topic": "",
            "intent": "chat",
            "stageDone": {"intent": True},
        }

    history_text = _format_history(state.get("history") or [])
    user_prompt = f"用户输入：{user_issue}"
    if history_text:
        user_prompt = f"对话历史（供判断意图）：\n{history_text}\n\n{user_prompt}"

    # 分类任务用温度 0，保证同样的输入得到同样的判定
    raw = await _invoke_text(CONTENT_ANALYSIS_PROMPT, user_prompt, temperature=0)
    data = _parse_json(raw)

    need_lecture = data.get("needLecture")
    if not isinstance(need_lecture, bool):
        # 模型没给出可用结论时按「需要讲解」兜底，避免用户的请求被静默吞掉
        logger.warning("内容分析未返回布尔型 needLecture，按需要讲解处理：%s", raw)
        need_lecture = True

    intent = str(data.get("intent") or "").strip()
    if intent not in VALID_INTENTS:
        # 非法/缺失意图兜底：需要讲解视为新课，否则视为闲聊
        intent = "new_lecture" if need_lecture else "chat"

    revise_inds = [
        int(ind)
        for ind in (data.get("reviseInds") or [])
        if str(ind).strip().lstrip("-").isdigit() and int(ind) > 0
    ]

    _emit("check", "done")
    if not need_lecture:
        reply = str(data.get("reply", "")).strip() or DEFAULT_GREETING
        # 闲聊：图在此短路结束，reply 事件由流式接口按最终状态统一推送
        return {
            "needLecture": False,
            "reply": reply,
            "topic": "",
            "intent": "chat",
            "stageDone": {"intent": True},
        }

    # 需要讲解：产出规范化主题
    topic = str(data.get("topic", "")).strip() or user_issue
    # 插一步「讲解主题」：等待页展示「讲解主题：xxx」；闲聊分支不发，直接结束
    _emit("topic", "start", topic=topic, message=f"讲解主题：{topic}")
    return {
        "needLecture": True,
        "topic": topic,
        "intent": intent,
        "reviseInds": revise_inds,
        "stageDone": {"intent": True},
    }
