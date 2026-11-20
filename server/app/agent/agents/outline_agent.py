"""大纲生成子 agent（拆自旧 text_organization_node 后半）。

三种模式：
- new_lecture：全量生成大纲（_build_catalog 原样复用）；
- continue：在既有大纲后续写 1~3 个模块（增量补丁 Prompt）；
- revise_page：只重写 reviseInds 指定的模块，其余模块原样保留。
非法 JSON 降级单模块，保证链路不断。
"""

import logging

from app.agent.nodes import (
    _build_catalog,
    _invoke_text,
    _normalize_catalog,
    _parse_json,
)

logger = logging.getLogger(__name__)

CONTINUE_OUTLINE_PROMPT = """你是 MAIC 的课件策划，负责为一份已有的讲解大纲续写后续模块。

要求：
1. 续写 1~3 个模块，由浅入深接着已有内容往后讲，不要重复已有模块的内容；
2. 每个模块含 1~3 个知识点：title 是知识点标题（8 字以内）；
3. context 是口播讲稿（90~160 字），口语连贯，第 1 条要用一句话接住已有大纲的最后一个模块；
4. title 与 context 必须等长且一一对应；
5. 只用口语和中文标点，不要 markdown 符号。

输出要求：只输出 JSON，不要解释文字，不要代码块围栏。
示例：{"catalogList": [{"title": ["进阶技巧"], "context": ["前面的基础都清楚了，我们接着看进阶技巧。……"]}]}"""

REVISE_MODULE_PROMPT = """你是 MAIC 的课件策划，负责重写讲解大纲中的指定模块。

要求：
1. 根据修改意见重写模块内容，保持与其他模块的衔接；没有修改意见就提升内容质量；
2. 模块含 1~3 个知识点：title 是知识点标题（8 字以内）；
3. context 是口播讲稿（90~160 字），口语连贯，只用口语和中文标点；
4. title 与 context 必须等长且一一对应；
5. 输出一个模块。

输出要求：只输出 JSON，不要解释文字，不要代码块围栏。
示例：{"catalogList": [{"title": ["新标题"], "context": ["新的口播讲稿。……"]}]}"""


async def _continue_catalog(topic: str, docs: list[str], existing: list) -> list:
    """续作：生成 1~3 个新模块追加到既有大纲之后。失败时返回既有大纲（不新增）。"""
    material = "\n\n".join(f"[资料{i}] {doc}" for i, doc in enumerate(docs[:6], 1))
    tail = existing[-1] if existing else None
    last_hint = ""
    if tail:
        last_hint = "已有大纲最后一个模块：{}。".format("、".join(tail.get("title") or []))
    user_prompt = (
        f"讲解主题：{topic}\n{last_hint}\n"
        f"可参考的资料：\n{material or '（无资料，依赖你的知识）'}\n\n"
        "请为已有大纲续写后续模块。"
    )
    raw = await _invoke_text(CONTINUE_OUTLINE_PROMPT, user_prompt, temperature=0.4)
    new_modules = _normalize_catalog(_parse_json(raw))
    if not new_modules:
        logger.warning("大纲续写解析失败，本次不追加模块：%s", raw)
        return list(existing)

    base = len(existing)
    combined = list(existing) + [
        {"ind": base + i, "title": m["title"], "context": m["context"]}
        for i, m in enumerate(new_modules, 1)
    ]
    return combined


async def _revise_catalog(topic: str, docs: list[str], existing: list, revise_inds: list[int]) -> list:
    """定向改写：只重写 reviseInds 指定 ind 的模块，其余保持不变。"""
    catalog = [dict(module) for module in existing]
    for pos, module in enumerate(catalog):
        if module.get("ind") not in revise_inds:
            continue
        user_prompt = (
            f"讲解主题：{topic}\n"
            f"待重写模块（第 {module.get('ind')} 页）：\n"
            f"标题：{'、'.join(module.get('title') or [])}\n"
            f"讲稿：{' '.join(module.get('context') or [])}\n\n"
            "请重写这个模块。"
        )
        raw = await _invoke_text(REVISE_MODULE_PROMPT, user_prompt, temperature=0.4)
        revised = _normalize_catalog(_parse_json(raw))
        if revised:
            catalog[pos] = {
                "ind": module["ind"],
                "title": revised[0]["title"],
                "context": revised[0]["context"],
            }
        else:
            logger.warning("第 %s 页改写解析失败，保留原文：%s", module.get("ind"), raw)
    return catalog


async def outline_agent(state: dict) -> dict:
    """按 intent 选择全量生成 / 续作 / 定向改写，产出 catalogList。"""
    from app.agent.nodes import _emit

    _emit("outline", "start", message="正在生成大纲…")
    topic = (state.get("topic") or state.get("userIssue") or "").strip()
    docs = list(state.get("docs") or [])
    intent = str(state.get("intent") or "new_lecture")
    existing = list(state.get("catalogList") or [])

    # 兜底：没有主题就无从讲解，直接交回引导语
    if not topic:
        return {
            "needLecture": False,
            "reply": "你好，我是 MAIC 助手，可以为你讲解知识并生成配套音频与页面，想听点什么？",
            "catalogList": [],
            "stageDone": {"outline": True},
        }

    if intent == "continue" and existing:
        catalog = await _continue_catalog(topic, docs, existing)
    elif intent == "revise_page" and existing and state.get("reviseInds"):
        valid = [int(ind) for ind in (state.get("reviseInds") or [])]
        catalog = await _revise_catalog(topic, docs, existing, valid)
    else:
        catalog = await _build_catalog(topic, docs)

    _emit("outline", "done", total=len(catalog))
    return {"catalogList": catalog, "stageDone": {"outline": True}}
