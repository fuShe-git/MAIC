"""内容安全审核（R7 双节点共用）：关键词规则层 + LLM 兜底。

check_text(text, mode) 供 safety_input（输入审核，固定入口）与
safety_output（输出审核，可派发子 agent）两个节点共用。
mode 取 "input" / "output"，仅用于日志区分。
规则层命中直接判违规；规则层放行后交 LLM 复核，LLM 异常按安全放行（fail-open，
不因审核服务抖动阻断生成）。
"""

import logging
import re

from app.agent.nodes import SAFETY_PROMPT, _invoke_text, _parse_json

logger = logging.getLogger(__name__)

# 规则层：明显的违规关键词（第一道防线，命中即拦截，不调模型）
_BLOCK_KEYWORDS = [
    "色情", "裸聊", "援交", "赌博平台", "博彩网站", "冰毒", "制毒", "贩毒",
    "炸药制作", "枪支买卖", "自杀方法", "自残教程", "杀猪盘", "洗钱教程",
    "迷奸", "强奸", "儿童色情",
]

# 疑似违规、交给 LLM 复核的弱信号词（规则层不确定时走 LLM）
_SUSPICIOUS_KEYWORDS = [
    "怎么炸", "怎么毒", "自杀", "自残", "赌博", "毒品", "枪械", "爆炸物",
    "色情", "裸", "代开发票", "套现",
]


def _rule_check(text: str) -> tuple[bool, str]:
    """关键词规则层：返回 (是否命中强违规, 原因)。"""
    compact = re.sub(r"\s+", "", text or "")
    for word in _BLOCK_KEYWORDS:
        if word in compact:
            return True, f"包含违规关键词「{word}」"
    return False, ""


def _is_suspicious(text: str) -> bool:
    """弱信号检测：命中才需要 LLM 复核，避免每次生成都多一轮模型调用。"""
    compact = re.sub(r"\s+", "", text or "")
    return any(word in compact for word in _SUSPICIOUS_KEYWORDS)


async def check_text(text: str, mode: str = "input") -> tuple[bool, str]:
    """审核一段文本；返回 (是否放行, 拦截原因)。

    流程：强违规关键词直接拦 → 无弱信号直接放行 → LLM 复核（异常放行）。
    """
    text = str(text or "")
    if not text.strip():
        return True, ""

    blocked, reason = _rule_check(text)
    if blocked:
        logger.warning("内容安全[%s]规则层拦截：%s", mode, reason)
        return False, reason

    if not _is_suspicious(text):
        return True, ""

    # LLM 兜底复核：弱信号文本才走这里
    try:
        raw = await _invoke_text(
            SAFETY_PROMPT, f"待审核文本：\n{text[:2000]}", temperature=0
        )
        data = _parse_json(raw)
        safe = data.get("safe")
        if isinstance(safe, bool):
            if not safe:
                reason = str(data.get("reason") or "内容不安全")[:100]
                logger.warning("内容安全[%s]LLM 拦截：%s", mode, reason)
                return False, reason
            return True, ""
        logger.warning("内容安全[%s]LLM 未返回布尔值，按安全放行：%s", mode, raw)
        return True, ""
    except Exception as exc:  # 审核服务异常不阻断生成
        logger.warning("内容安全[%s]LLM 复核失败，按安全放行：%s", mode, exc)
        return True, ""


async def safety_input_node(state: dict) -> dict:
    """输入审核（固定入口，START 之后第一个节点，不进 dispatchCounts 计数）。

    违规输入短路返回：needLecture=False + 拦截话术，图直接走向 END。
    """
    from app.agent.nodes import _emit

    _emit("safety", "start", message="正在审核输入内容…")
    user_issue = (state.get("userIssue") or "").strip()
    print('开始输入审核')
    ok, reason = await check_text(user_issue, mode="input")
    print('输入审核结果', ok, '二参',reason)
    if ok:
        _emit("safety", "done", message="输入审核通过")
        return {}

    _emit("safety", "done", message="输入未通过安全审核")
    return {
        "needLecture": False,
        "topic": "",
        "reply": "抱歉，你的输入包含不合规内容，我不能为你生成课件。换个话题试试吧。",
    }


async def safety_output_node(state: dict) -> dict:
    """输出审核（registry 可派发子 agent）：组装后校验课件文本。

    违规时置 outputUnsafe 与原因，由主管决策改派大纲重生成或失败短路。
    """
    from app.agent.nodes import _emit

    _emit("safety", "start", message="正在审核课件内容…")
    catalog = state.get("catalogList") or []
    parts = [
        f"{' '.join(module.get('title') or [])} {' '.join(module.get('context') or [])}"
        for module in catalog
        if isinstance(module, dict)
    ]
    # 课后练习题文本一并送审（quiz_only 流程只有题目没有大纲）
    for quiz in state.get("quizList") or []:
        if isinstance(quiz, dict):
            parts.append(
                f"{quiz.get('question', '')} {' '.join(quiz.get('options') or [])}"
            )
    ok, reason = await check_text("\n".join(parts), mode="output")
    if ok:
        _emit("safety", "done", message="课件内容审核通过")
        return {"outputUnsafe": False, "stageDone": {"safety_output": True}}

    _emit("safety", "done", message="课件内容未通过安全审核")
    return {
        "outputUnsafe": True,
        "failMessage": f"课件内容未通过安全审核：{reason}",
        "stageDone": {"safety_output": True},
    }
