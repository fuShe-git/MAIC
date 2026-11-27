"""主管（supervisor）：图的唯一决策节点。

职责：
1. validate_state：开始前做最小状态校验（cascade 清空残留 / 不合法字段剔除）；
2. LLM 智能路由：读状态摘要与 registry 职责，自主选择派发哪个子 agent；
   选择非法/为空 → 确定性兜底路由（按意图与阶段完成度推导）；
3. dispatch loop 保护：同一 stage 对同一 agent 派发超过 2 次判走不通 → fail 短路；
4. 事实冲突 / 输出违规 → 改派 outline_agent 重生成（级联清空下游产物），
   次数同样计入 dispatchCounts；
5. 人工确认分支：大纲产出后 autoConfirm=False 时 interrupt() 暂停，
   恢复执行时接受用户编辑后的 catalogList，只重走 page/audio。
fail 覆盖：RAG 持续不可用、事实冲突超限、输出违规且超限、派发超限。

图拓扑（星形）：START → safety_input → supervisor ⇄ 各子 agent → END，
页/音频并行由 supervisor 一次返回两个 Send 实现，全部回收后唤醒主管。
"""

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import interrupt

from app.agent.agent import get_llm
from app.agent.agents import AGENT_REGISTRY
from app.agent.nodes import (
    DEFAULT_GREETING,
    _emit,
    _ensure_new_module_content,
    _parse_json,
    _split_oversized_modules,
    localized,
)
from app.agent.state import CLEAR_MARKER

logger = logging.getLogger(__name__)

# 同一 stage 对同一 agent 的派发上限：超过即判「这条路走不通」
MAX_DISPATCH = 2

# supervisor 自身发起重派的计数键后缀（stage:supervisor）
_SUPERVISOR_KEY = "supervisor"

SUPERVISOR_PROMPT = """你是 MAIC 多智能体课堂的主管，负责决定下一步派发哪个子 agent。

可选子 agent（名字 → 职责）：
{registry}

当前状态摘要：
{summary}

决策规则：
1. 每轮只输出下一步要派发的 1~2 个子 agent；可以并行的一起派（页面生成与音频合成可并行）；
2. 只能从可选子 agent 里选，名字必须与列表完全一致；
3. 按流水线顺序推进：意图分析 → 查询改写 → 检索 → 大纲 → （页面 ∥ 音频）→ 事实核查 → 练习 → 组装 → 输出审核；
4. 已完成的阶段不要重派，除非状态摘要里说明要重生成；
5. 没有可派发的 agent 时输出空数组，表示可以收尾。

输出要求：只输出 JSON，不要解释文字，不要代码块围栏。
示例：{{"next": ["page_agent", "audio_agent"]}}"""


# ── 状态校验 ────────────────────────────────────────────────────────────────
def _validate_state(state: dict) -> dict:
    """最小状态校验：剔除不合法字段，返回需要修补的更新（无修补返回空）。"""
    updates: dict = {}

    catalog = state.get("catalogList") or []
    if not isinstance(catalog, list):
        updates["catalogList"] = []

    for key in ("pageList", "audioList"):
        value = state.get(key)
        if value is not None and not isinstance(value, list):
            updates[key] = []

    intent = str(state.get("intent") or "")
    if intent and intent not in {
        "new_lecture", "continue", "revise_page", "quiz_only", "chat",
    }:
        updates["intent"] = "new_lecture"

    return updates


# ── 确定性兜底路由 ──────────────────────────────────────────────────────────
def _pipeline_order(state: dict) -> list[tuple[str, str]]:
    """按 intent 推导剩余阶段 [(stage, agent_name), ...]（pages/audio 并列同组）。"""
    intent = str(state.get("intent") or "new_lecture")
    if intent == "quiz_only":
        return [("quiz", "quiz_agent"), ("assemble", "assemble_agent")]
    if intent == "revise_page":
        return [
            ("pages", "page_agent"),
            ("audio", "audio_agent"),
            ("assemble", "assemble_agent"),
        ]
    # new_lecture / continue：全流水线
    return [
        ("query", "query_rewrite_agent"),
        ("research", "research_agent"),
        ("outline", "outline_agent"),
        ("pages", "page_agent"),
        ("audio", "audio_agent"),
        ("factcheck", "fact_check_agent"),
        ("quiz", "quiz_agent"),
        ("assemble", "assemble_agent"),
        ("safety_output", "safety_output"),
    ]


def _default_next(state: dict) -> list[str]:
    """确定性兜底：返回当前应该派发的 agent 名（可能两个并行）。"""
    done = state.get("stageDone") or {}
    intent = str(state.get("intent") or "new_lecture")

    if intent == "chat" or state.get("failMessage") or not state.get("needLecture", True):
        return []

    order = _pipeline_order(state)
    next_pairs = [(stage, agent) for stage, agent in order if not done.get(stage)]
    if not next_pairs:
        return []

    stage, agent = next_pairs[0]
    # pages 与 audio 同组并行（都未完成时一起派）
    if stage == "pages" and not done.get("audio"):
        return ["page_agent", "audio_agent"]
    return [agent]


# ── LLM 智能路由（确定性兜底之上的优化层）────────────────────────────────
def _state_summary(state: dict) -> str:
    """给路由 LLM 看的状态摘要。"""
    done = state.get("stageDone") or {}
    counts = state.get("dispatchCounts") or {}
    failed = state.get("failedPages") or []
    conflicts = state.get("conflicts") or []
    catalog = state.get("catalogList") or []
    lines = [
        f"主题：{state.get('topic') or '（无）'}",
        f"意图：{state.get('intent') or 'new_lecture'}",
        f"已完成阶段：{', '.join(sorted(done)) or '无'}",
        f"大纲模块数：{len(catalog)}",
        f"失败清单：{len(failed)} 条",
        f"事实冲突：{len(conflicts)} 条",
        f"派发计数：{json.dumps(counts, ensure_ascii=False) if counts else '无'}",
    ]
    return "\n".join(lines)


async def _llm_choose(state: dict, candidates: list[str]) -> list[str]:
    """LLM 路由：返回候选内的合法选择；任何异常/非法输出返回空（走确定性兜底）。"""
    if not candidates:
        return []
    registry_text = "\n".join(
        f"- {name}：{AGENT_REGISTRY[name]['desc']}"
        for name in candidates
        if name in AGENT_REGISTRY
    )
    prompt = SUPERVISOR_PROMPT.format(
        registry=registry_text or "（无可派发 agent）",
        summary=_state_summary(state),
    )
    try:
        llm = get_llm().bind(temperature=0)
        result = await llm.ainvoke(
            [SystemMessage(content="你是 MAIC 的调度主管。"), HumanMessage(content=prompt)]
        )
        content = result.content
        text = content if isinstance(content, str) else str(content)
        data = _parse_json(text)
        chosen = [
            name for name in (data.get("next") or [])
            if isinstance(name, str) and name in candidates
        ]
        return chosen[:2]
    except Exception as exc:
        logger.warning("主管 LLM 路由失败，走确定性兜底：%s", exc)
        return []


# ── 循环保护与失败短路 ──────────────────────────────────────────────────────
def _over_limit(state: dict, keys: list[str]) -> bool:
    """任一计数键达到派发上限即判走不通。"""
    counts = state.get("dispatchCounts") or {}
    return any(int(counts.get(key, 0)) >= MAX_DISPATCH for key in keys)


def _fail(message: str) -> dict:
    """失败短路：推 error 事件并返回空派发（路由 → END）。"""
    logger.error("主管判定生成失败：%s", message)
    _emit("error", "done", message=message)
    return {"dispatch": [], "failMessage": message}


def _count_updates(state: dict, dispatch: list[str], stage_of: dict) -> dict:
    """为本轮派发累加计数（stage:agent）。"""
    counts = dict(state.get("dispatchCounts") or {})
    updates = {}
    for name in dispatch:
        key = f"{stage_of.get(name, name)}:{name}"
        value = int(counts.get(key, 0)) + 1
        counts[key] = value
        updates[key] = value
    return updates


# ── 主管节点 ────────────────────────────────────────────────────────────────
async def supervisor_node(state: dict) -> dict:
    """校验状态 → （可能 interrupt 等待人工确认）→ 决策本轮派发的子 agent。"""
    updates: dict = _validate_state(state)
    topic_text = (state.get("topic") or state.get("userIssue") or "").strip()

    # ── 人工确认分支：大纲产出后暂停，恢复执行时接受用户编辑的大纲 ──
    done = state.get("stageDone") or {}
    intent = str(state.get("intent") or "new_lecture")
    need_lecture = state.get("needLecture", True)
    confirmable = intent in ("new_lecture", "continue", "revise_page")

    if (
        need_lecture
        and confirmable
        and done.get("outline")
        and not state.get("outlineConfirmed")
        and not state.get("autoConfirm", True)
    ):
        catalog = list(updates.get("catalogList") or state.get("catalogList") or [])
        _emit("confirm", "start", catalogList=catalog, threadId=state.get("threadId") or "")
        # interrupt() 首次调用抛出中断、图暂停；用户 resume 后返回 {confirmed, catalogList}
        answer = interrupt({"catalogList": catalog, "threadId": state.get("threadId") or ""})
        if not (isinstance(answer, dict) and answer.get("confirmed")):
            # 用户取消：不视为失败，返回闲聊式收尾
            _emit("confirm", "done", message="已取消本次生成")
            return {
                **updates,
                "dispatch": [],
                "needLecture": False,
                "reply": "已取消本次生成，有需要随时找我。",
                "outlineConfirmed": True,
            }
        # 用户确认（可能编辑过大纲）：采纳编辑后的 catalogList，级联清空下游产物
        edited = answer.get("catalogList")
        updates.update(_cascade_clear())
        if isinstance(edited, list) and edited:
            enriched = await _ensure_new_module_content(topic_text, edited)
            # 用户编辑后内容可能塞得过多；下游已级联清空，可安全拆页并重编号
            updates["catalogList"] = await _split_oversized_modules(enriched or edited)
        # 用户选定的 PPT 样式随确认一起恢复，后续 page_agent 会按它生成每页视觉。
        if isinstance(answer.get("theme"), dict) and answer.get("theme"):
            updates["selectedTheme"] = answer["theme"]
        updates["outlineConfirmed"] = True

        # 确认后直接重走 page/audio（保留既有快照不动）
        stage_of = {name: spec["stage"] for name, spec in AGENT_REGISTRY.items()}
        dispatch = [
            name for name in ("page_agent", "audio_agent")
            if not _over_limit(state, [f"{stage_of[name]}:{name}"])
        ]
        if not dispatch:
            return {**updates, **_fail("页面/音频生成超限")}
        updates.update(_count_updates(state, dispatch, stage_of))
        return {**updates, "dispatch": dispatch}

    # ── 失败短路优先：failMessage 已置 → END ──
    if state.get("failMessage"):
        return {**updates, "dispatch": []}

    # 闲聊 / 不需要讲解：直接收尾（reply 由流式接口按最终状态推送）
    if not need_lecture or intent == "chat":
        if not state.get("reply"):
            updates["reply"] = localized(
                DEFAULT_GREETING, "Hello, I am MAIC. What would you like to learn today?"
            )
        return {**updates, "dispatch": []}

    # ── 冲突处理：事实冲突 → 改派大纲重生成（级联清空下游）──────────────
    conflicts = state.get("conflicts") or []
    if conflicts and done.get("factcheck") and not done.get("quiz"):
        stage_key = f"outline:{_SUPERVISOR_KEY}"
        if _over_limit(state, [stage_key, "outline:outline_agent"]):
            return {**updates, **_fail("事实冲突超限，无法确认课件内容准确性")}
        _emit("outline", "start", message="检测到事实冲突，正在重新生成大纲…")
        updates.update(_cascade_clear())
        updates.update(_count_updates(state, ["outline_agent"], {"outline_agent": "outline"}))
        counts = dict(state.get("dispatchCounts") or {})
        updates[stage_key] = int(counts.get(stage_key, 0)) + 1
        return {**updates, "dispatch": ["outline_agent"]}

    # ── 输出违规处理：改派大纲重生成（级联清空下游）──────────────────────
    if state.get("outputUnsafe") and done.get("assemble"):
        stage_key = f"outline:{_SUPERVISOR_KEY}"
        if _over_limit(state, [stage_key, "outline:outline_agent"]):
            return {**updates, **_fail("课件内容违规且重生成超限")}
        _emit("outline", "start", message="课件内容未过审，正在重新生成…")
        updates.update(_cascade_clear())
        updates["outputUnsafe"] = False
        updates.update(_count_updates(state, ["outline_agent"], {"outline_agent": "outline"}))
        counts = dict(state.get("dispatchCounts") or {})
        updates[stage_key] = int(counts.get(stage_key, 0)) + 1
        return {**updates, "dispatch": ["outline_agent"]}

    # ── 失败页自动重试（受派发上限约束）────────────────────────────────────
    failed = [
        row for row in (state.get("failedPages") or [])
        if isinstance(row, dict) and row.get("ind") is not None
    ]
    pages_ready = done.get("pages") and done.get("audio")
    if failed and pages_ready:
        retry_agents = []
        retry_inds = []
        for row in failed:
            stage = str(row.get("stage") or "pages")
            agent = "audio_agent" if stage == "audio" else "page_agent"
            if not _over_limit(state, [f"{stage}:{agent}"]):
                if agent not in retry_agents:
                    retry_agents.append(agent)
                retry_inds.append(int(row.get("ind")))
        if retry_agents:
            _emit("pages", "start", message="正在重试失败的页面…")
            updates["pageTargets"] = sorted(set(retry_inds))
            updates.update(
                _count_updates(state, retry_agents, {"page_agent": "pages", "audio_agent": "audio"})
            )
            return {**updates, "dispatch": retry_agents}
        # 重试次数用尽：标记失败清单继续组装（终态 partial + failedPages）

    # ── 常规推进：候选 = 确定性推导 ∩ 派发上限内 ───────────────────────────
    # /chat/generate 直接带着用户大纲进入此分支，补写讲稿必须在 page/audio 并发派发前完成
    catalog = updates.get("catalogList") or state.get("catalogList") or []
    enriched = await _ensure_new_module_content(topic_text, catalog)
    if enriched is not None:
        catalog = enriched
        updates["catalogList"] = enriched
    # /chat/generate 直接带用户大纲进入；页面尚未生成时按内容量拆页并重编号，
    # 避免把过多内容挤进一页。已有页面时不拆，防止 ind 与既有产物错位。
    if not (done.get("pages") or (state.get("pageList") or [])):
        split = await _split_oversized_modules(catalog)
        if split != catalog:
            updates["catalogList"] = split

    default_agents = _default_next(state)
    stage_of = {name: spec["stage"] for name, spec in AGENT_REGISTRY.items()}
    candidates = [
        name for name in default_agents
        if not _over_limit(state, [f"{stage_of.get(name, name)}:{name}"])
    ]

    if not candidates:
        # 全部阶段完成（或无可派发）→ 收尾
        if not state.get("courseHtml") and state.get("needLecture", True):
            logger.warning("主管收尾时没有课件产物，可能生成链路不完整")
        return {**updates, "dispatch": []}

    dispatch = await _llm_choose(state, candidates) or candidates

    # 重试/定向改页时给 pageTargets 置目标 ind；常规派发清空
    if "page_agent" in dispatch or "audio_agent" in dispatch:
        if state.get("intent") == "revise_page" and state.get("reviseInds"):
            updates["pageTargets"] = [int(ind) for ind in state["reviseInds"]]
        elif "pageTargets" not in updates:
            updates["pageTargets"] = []

    updates.update(_count_updates(state, dispatch, stage_of))
    return {**updates, "dispatch": dispatch}


def _cascade_clear() -> dict:
    """改派 outline 时级联清空下游过期产物（R4）：同一次更新内显式清空。

    reducer 合并语义下写空列表不会清空旧值，因此用 CLEAR_MARKER 标记重置；
    stageDone 里的 None 值表示删除阶段标记（强制相关阶段重走）。
    """
    return {
        "pageList": [],
        "audioList": [],
        "quizList": [CLEAR_MARKER],
        "failedPages": [{CLEAR_MARKER: True}],
        "conflicts": [CLEAR_MARKER],
        "stageDone": {
            "pages": None,
            "audio": None,
            "factcheck": None,
            "quiz": None,
            "assemble": None,
            "safety_output": None,
        },
        "pageTargets": [],
        "outputUnsafe": False,
        # 停止后恢复旧线程时，不能把上一次的课件产物当成新结果返回
        "courseHtml": "",
        "videoUrl": "",
    }
