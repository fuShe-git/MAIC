import json
import logging
import re
from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.agent import get_llm

logger = logging.getLogger(__name__)

class CatalogModule(TypedDict):
    """大纲里的一个模块，对应一个即将讲解的页面。

    title 与 context 一一对应（第 i 个 title 的解释就是第 i 个 context），
    因此二者长度必须相等。
    """

    ind: int            # 序号
    title: list[str]    # 该页面内各知识点的标题
    context: list[str]  # 与 title 一一对应的讲解内容


class AudioModule(TypedDict):
    """一个模块对应的音频数据。

    ind 与 CatalogModule.ind 对应，用来把音频和它所属的页面配上对。
    """

    ind: int      # 序号，与 catalogList 中对应模块的 ind 一致
    context: str  # 台词


class PageModule(TypedDict):
    """一个模块对应的页面数据。

    ind 与 catalogList / audioList 中同序号的模块对应，
    context 复用对应音频的台词，page 为渲染用 HTML。
    """

    ind: int      # 序号，与前面对应
    context: str  # 台词，与 audioList 中相同 ind 的台词一致
    page: str     # 生成的页面，HTML 代码


class SimpleState(TypedDict):
    userIssue:str           # 用户问题
    needLecture:bool        # 是否需要讲解；False 时图直接结束
    reply:str               # 不需要讲解时返回给用户的简单回复
    topic:str               # 从用户问题里归纳出的讲解主题，供后续节点使用
    catalogList:list[CatalogModule]  # 大纲，每个模块对应的页面
    audioList:list[AudioModule]      # 台词，每个模块对应的音频
    pageList:list[PageModule]        # 页面，每个模块对应的页面

DEFAULT_GREETING = "你好，我是 MAIC 助手，可以为你讲解知识并生成配套音频与页面，想听点什么？"

CONTENT_ANALYSIS_PROMPT = """你是 MAIC（多智能体互动课堂）的内容分析助手，负责判断用户输入是否属于「需要讲解的内容」。

判断规则：
1. 打招呼、闲聊、询问软件功能、无意义字符 → 不需要讲解；
2. 提出一个可讲授的主题，例如「三国杀的玩法」「讲一下三国杀的玩法」「三国杀」「牛顿第二定律是什么」→ 需要讲解；
3. 需要讲解时，把用户的话归纳成一个简短、名词性的主题，去掉「讲一下」「介绍一下」这类动词，例如「三国杀的玩法」。

输出要求：只输出 JSON，不要输出解释文字或代码块围栏。

需要讲解时：
{"needLecture": true, "topic": "三国杀的玩法", "reply": ""}

不需要讲解时（topic 留空，reply 填一句简短的自我介绍式回复）：
{"needLecture": false, "topic": "", "reply": "你好，我是 MAIC 助手，可以为你讲解知识并生成配套音频与页面，想听点什么？"}"""

## 节点
async def _invoke_text(system_prompt: str, user_prompt: str, temperature: float = 0.3) -> str:
    """调用 agent.py 里的模型，返回纯文本（system + user 两段提示词）。"""
    result = await get_llm().ainvoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
    )
    content = result.content
    if isinstance(content, str):
        return content.strip()
    return str(content).strip()  # 部分模型返回分段列表，统一转字符串


def _parse_json(raw: str) -> dict:
    """从模型输出中提取 JSON 对象；解析失败返回空字典，由调用方兜底。"""
    text = raw.strip()
    if text.startswith("```"):  # 去掉 ```json ... ``` 围栏
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```\s*$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        text = text[start : end + 1]

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("模型输出不是合法 JSON：%s", raw)
        return {}
    return data if isinstance(data, dict) else {}


async def content_analysis_node(state: SimpleState) -> dict:
    '''分析内容，判断是否为需要讲解'''
    print('来到这里',state)
    user_issue = (state.get("userIssue") or "").strip()

    # 空输入不必走模型，直接给引导语
    if not user_issue:
        return {"needLecture": False, "reply": DEFAULT_GREETING, "topic": ""}

    # 分类任务用温度 0，保证同样的输入得到同样的判定
    raw = await _invoke_text(CONTENT_ANALYSIS_PROMPT, f"用户输入：{user_issue}", temperature=0)
    data = _parse_json(raw)

    need_lecture = data.get("needLecture")
    if not isinstance(need_lecture, bool):
        # 模型没给出可用结论时按「需要讲解」兜底，避免用户的请求被静默吞掉
        logger.warning("内容分析未返回布尔型 needLecture，按需要讲解处理：%s", raw)
        need_lecture = True
    print('节点一输出内容',need_lecture,data)
    if not need_lecture:
        reply = str(data.get("reply", "")).strip() or DEFAULT_GREETING
        return {"needLecture": False, "reply": reply, "topic": ""}

    
    # 需要讲解：产出规范化主题，交给 text_organization_node
    return {
        "needLecture": True,
        "topic": str(data.get("topic", "")).strip() or user_issue,
    }

def text_organization_node(state:SimpleState):
    '''根据主题查找资料，生成大纲,并生成对应页面（需要自体校验）'''
    # 此处先用模型判断是否为三国杀，是的话调用rag
    # 是相关的调用mcp
    # 返回大纲，进入audio_node

def audio_node(state:SimpleState):
    '''根据大纲的每个模块生成台词，并将台词转换为音频'''
    # 