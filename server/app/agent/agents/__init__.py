"""子 agent 注册表：名称 → 职责 / 就绪条件 / 执行函数。

- 供主管（supervisor）LLM 路由 Prompt 展示可选 agent 与职责；
- 供 graph.py 统一注册节点函数。
stage 是流水线阶段键，与 dispatchCounts 的 "stage:agent" 计数键对齐。
safety_input 是固定入口（START 之后第一个节点），不进 registry 计数。
"""

from app.agent.agents.assemble_agent import assemble_agent
from app.agent.agents.audio_agent import audio_agent
from app.agent.agents.fact_check_agent import fact_check_agent
from app.agent.agents.intent_agent import intent_agent
from app.agent.agents.outline_agent import outline_agent
from app.agent.agents.page_agent import page_agent
from app.agent.agents.query_rewrite_agent import query_rewrite_agent
from app.agent.agents.quiz_agent import quiz_agent
from app.agent.agents.research_agent import research_agent
from app.agent.agents.safety_agent import safety_input_node, safety_output_node

# 名称 → 职责描述 / 就绪条件 / 阶段键 / 执行函数
AGENT_REGISTRY: dict[str, dict] = {
    "intent_agent": {
        "stage": "intent",
        "desc": "意图分析：判断是否需要讲解、归纳主题、识别多轮意图",
        "ready": "总在最前执行",
        "func": intent_agent,
    },
    "query_rewrite_agent": {
        "stage": "query",
        "desc": "查询改写：主题改写为 3~5 个角度的检索查询",
        "ready": "intent 完成且需要讲解",
        "func": query_rewrite_agent,
    },
    "research_agent": {
        "stage": "research",
        "desc": "主题判定 + RAG 检索：召回知识库资料",
        "ready": "query 完成",
        "func": research_agent,
    },
    "outline_agent": {
        "stage": "outline",
        "desc": "大纲生成：产出讲解大纲（支持续作与定向改写）",
        "ready": "research 完成",
        "func": outline_agent,
    },
    "page_agent": {
        "stage": "pages",
        "desc": "页面生成：按大纲逐页生成 HTML 页面",
        "ready": "outline 完成（可与 audio_agent 并行）",
        "func": page_agent,
    },
    "audio_agent": {
        "stage": "audio",
        "desc": "音频合成：口播讲稿合成 TTS 旁白",
        "ready": "outline 完成（可与 page_agent 并行）",
        "func": audio_agent,
    },
    "fact_check_agent": {
        "stage": "factcheck",
        "desc": "事实校验：对照资料核查大纲事实冲突",
        "ready": "research 与 outline 完成",
        "func": fact_check_agent,
    },
    "quiz_agent": {
        "stage": "quiz",
        "desc": "课后测评：按大纲生成练习题",
        "ready": "outline 完成",
        "func": quiz_agent,
    },
    "assemble_agent": {
        "stage": "assemble",
        "desc": "课件组装：页面 + 音频 + 练习组装并写资产快照",
        "ready": "pages 与 audio 完成",
        "func": assemble_agent,
    },
    "safety_output": {
        "stage": "safety_output",
        "desc": "输出校验：组装后审核课件文本",
        "ready": "assemble 完成",
        "func": safety_output_node,
    },
}

__all__ = [
    "AGENT_REGISTRY",
    "safety_input_node",
    "safety_output_node",
    "intent_agent",
    "query_rewrite_agent",
    "research_agent",
    "outline_agent",
    "page_agent",
    "audio_agent",
    "fact_check_agent",
    "quiz_agent",
    "assemble_agent",
]
