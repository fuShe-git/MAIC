"""把 nodes.py 里的节点串联成一张 LangGraph 图。

拓扑：

    START → content_analysis ─┬─ needLecture = True  → text_organization → audio → END
                              └─ needLecture = False → END（直接返回闲聊回复）

- `add_edge`：普通边，A 执行完必去 B；
- `add_conditional_edges`：条件边，由路由函数的返回值决定下一站，这里用来做「闲聊就直接结束」的短路。
"""

from langgraph.graph import END, START, StateGraph

from app.agent.nodes import (
    SimpleState,
    audio_node,
    content_analysis_node,
    text_organization_node,
)


def route_after_content_analysis(state: SimpleState) -> str:
    """条件边路由：只有确认需要讲解时才往下走，否则直接结束。"""
    return "text_organization" if state.get("needLecture") else END


def build_graph():
    """装配并编译图。

    编译后的对象才能 invoke / ainvoke；这里没有 interrupt（中断）节点，
    所以不需要 checkpointer（检查点存储）。
    """
    graph = StateGraph(SimpleState)

    # 1. 注册节点：节点名 → 执行函数
    graph.add_node("content_analysis", content_analysis_node)
    graph.add_node("text_organization", text_organization_node)
    graph.add_node("audio", audio_node)

    # 2. 连边
    graph.add_edge(START, "content_analysis")
    graph.add_conditional_edges(
        "content_analysis",
        route_after_content_analysis,
        {
            "text_organization": "text_organization",
            END: END,
        },
    )
    graph.add_edge("text_organization", "audio")
    graph.add_edge("audio", END)

    return graph.compile()


# 全局单例：图编译一次即可复用，不要每个请求都重新构建
classroom_graph = build_graph()


async def run_classroom(user_issue: str) -> dict:
    """跑一遍完整的图，userIssue 就是用户的输入内容。

    返回最终状态：闲聊时看 `reply`，需要讲解时看 `topic` 及后续节点产出的字段。
    """
    return await classroom_graph.ainvoke({"userIssue": user_issue})
