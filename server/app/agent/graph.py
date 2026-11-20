"""LangGraph 图编排（v4 星形拓扑）。

拓扑：START → safety_input → supervisor ⇄ 子 agent（Send 并行）→ END
- supervisor 决策写入 state.dispatch（agent 名列表），路由函数转成 Send 扇出；
- 每个子 agent 执行完都回到 supervisor，由它决定下一步；
- page_agent ∥ audio_agent 一次派两个 Send，分支全部回收后唤醒主管（一次）；
- BoundedMemorySaver 记忆检查点支撑 interrupt（大纲人工确认）/ Command(resume) 恢复。

对外接口：
- run_classroom(user_issue, ...)：跑一整条生成链路，既有调用 run_classroom(msg) 不变；
- resume_classroom(thread_id, answer)：人工确认后恢复执行。
"""

from typing import Optional
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send

from app.agent.agents import AGENT_REGISTRY, safety_input_node
from app.agent.checkpointer import BoundedMemorySaver
from app.agent.nodes import SimpleState
from app.agent.supervisor import supervisor_node


def _route_after_safety(state: SimpleState) -> str:
    """输入审核后：被拦/闲聊短路 → END；正常 → 主管。"""
    if state.get("needLecture") is False:
        return END
    return "supervisor"


def _route_from_supervisor(state: SimpleState) -> list:
    """主管决策 → Send 扇出（可并行 1~2 个子 agent）；空派发 → END。"""
    dispatch = state.get("dispatch") or []
    if not dispatch:
        return END
    return [Send(name, state) for name in dispatch if name in AGENT_REGISTRY]


def _build_graph():
    """装配星形图并编译（挂记忆检查点）。"""
    graph = StateGraph(SimpleState)

    graph.add_node("safety_input", safety_input_node)
    graph.add_node("supervisor", supervisor_node)
    for name, spec in AGENT_REGISTRY.items():
        graph.add_node(name, spec["func"])

    graph.add_edge(START, "safety_input")
    graph.add_conditional_edges(
        "safety_input",
        _route_after_safety,
        ["supervisor", END],
    )
    # Send 扇出不需要 path_map：Send 自带目标节点名
    graph.add_conditional_edges("supervisor", _route_from_supervisor)
    for name in AGENT_REGISTRY:
        graph.add_edge(name, "supervisor")

    return graph.compile(checkpointer=BoundedMemorySaver())


classroom_graph = _build_graph()


def is_interrupted(state: dict) -> bool:
    """判断图是否停在 interrupt（等待人工确认大纲）。"""
    if state.get("__interrupt__"):
        return True
    return False


async def run_classroom(
    user_issue: str,
    *,
    thread_id: Optional[str] = None,
    auto_confirm: bool = True,
    history: Optional[list] = None,
    session_id: str = "",
) -> dict:
    """跑一整条生成链路，返回最终状态（含 courseHtml / videoUrl 等）。

    - thread_id：检查点线程键；不传则自动新生成（旧调用零破坏）；
    - auto_confirm：True 跳过人工确认（默认，既有 POST /chat 行为不变）；
      False 时大纲产出后图停在 interrupt，最终状态带 __interrupt__；
    - history：多轮会话历史 [{role, content}]，供意图识别；
    - session_id：会话 id，产物快照的键。
    """
    tid = (thread_id or "").strip() or uuid4().hex
    config = {"configurable": {"thread_id": tid}}
    initial: SimpleState = {
        "userIssue": user_issue,
        "needLecture": True,
        "reply": "",
        "topic": "",
        "catalogList": [],
        "audioList": [],
        "pageList": [],
        "courseHtml": "",
        "videoUrl": "",
        "history": [dict(item) for item in (history or [])],
        "sessionId": (session_id or "").strip(),
        "intent": "",
        "reviseInds": [],
        "courseId": "",
        "outlineConfirmed": False,
        "autoConfirm": bool(auto_confirm),
        "failMessage": "",
        "threadId": tid,
        "outputUnsafe": False,
        "dispatch": [],
        "pageTargets": [],
    }
    return await classroom_graph.ainvoke(initial, config=config)


async def resume_classroom(thread_id: str, answer: dict) -> dict:
    """人工确认后恢复执行：answer = {confirmed, catalogList}。"""
    config = {"configurable": {"thread_id": (thread_id or "").strip()}}
    return await classroom_graph.ainvoke(Command(resume=answer), config=config)
