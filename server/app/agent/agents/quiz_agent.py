"""课后测评子 agent（新增能力 3）：按大纲生成练习题。

- 常规流程：按 catalogList 生成 3~5 道单选题写入 quizList，组装时附加在课件末尾；
- quiz_only 意图：追加快照里的历史课件，或由 assemble_agent 生成独立测验页；
- 非法输出跳过不阻断（quizList 为空则课件不带练习）。
"""

import logging

from app.agent.nodes import QUIZ_PROMPT, _invoke_text, _parse_json

logger = logging.getLogger(__name__)


def _normalize_quiz(data: dict) -> list[dict]:
    """校验并规整练习题：answer 统一成选项下标数组，长度 1 单选、>1 多选。"""
    quiz_list = []
    for item in data.get("quizList") or []:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        options = [str(opt).strip() for opt in (item.get("options") or []) if str(opt).strip()]
        raw = item.get("answer")
        candidates = raw if isinstance(raw, (list, tuple)) else [raw]
        answers: list[int] = []
        for value in candidates:
            try:
                idx = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(options) and idx not in answers:
                answers.append(idx)
        if not question or len(options) < 2 or not answers:
            continue
        quiz_list.append(
            {
                "question": question,
                "options": options,
                "answer": answers,
                "analysis": str(item.get("analysis") or "").strip()[:100],
            }
        )
    return quiz_list[:5]


async def quiz_agent(state: dict) -> dict:
    """按大纲生成课后练习题；产出 quizList 与 stageDone.quiz。"""
    from app.agent.nodes import _emit

    _emit("quiz", "start", message="正在生成课后练习…")
    catalog = [m for m in (state.get("catalogList") or []) if isinstance(m, dict)]
    if catalog:
        outline_text = "\n".join(
            f"模块 {module.get('ind')}：{'、'.join(module.get('title') or [])}"
            for module in catalog
        )
        subject = f"课件大纲：\n{outline_text}"
    else:
        # quiz_only 且无历史课件：直接按主题出题
        topic = (state.get("topic") or state.get("userIssue") or "").strip()
        if not topic:
            return {"quizList": [], "stageDone": {"quiz": True}}
        subject = f"讲解主题：{topic}"
    try:
        raw = await _invoke_text(QUIZ_PROMPT, subject, temperature=0.4)
        quiz_list = _normalize_quiz(_parse_json(raw))
    except Exception as exc:  # 出题失败跳过，不阻断课件交付
        logger.warning("课后练习生成失败，本次课件不带练习：%s", exc)
        quiz_list = []

    _emit("quiz", "done", total=len(quiz_list), message="课后练习生成完成")
    return {"quizList": quiz_list, "stageDone": {"quiz": True}}
