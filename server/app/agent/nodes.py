"""公共层：Prompt（提示词）、辅助函数、产物目录、进度上报。

v4 拆分后本模块不再包含图节点：
- 节点逻辑迁往 agents/ 下的子 agent（intent / query_rewrite / research / outline /
  page / audio / fact_check / quiz / safety / assemble）；
- 本模块保留全部 Prompt 与生成辅助函数，供各子 agent 原样复用；
- re-export state 类型（SimpleState / CatalogModule / AudioModule / PageModule），
  保持既有 `from app.agent.nodes import ...` 导入零破坏。
"""

import asyncio
import json
import logging
import re
from contextvars import ContextVar
from html import escape
from pathlib import Path
from typing import Callable, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.agent import get_llm
from app.agent.state import (
    AudioModule,
    CatalogModule,
    PageModule,
    SimpleState,
)
from app.tools.milvus import embed_text, get_client, hybrid_search

# ── langchain 版本兼容垫片 ─────────────────────────────────────────────────
# langchain-core 0.3.x 的 globals.py 会读取根包 langchain 的 debug / verbose /
# llm_cache 透传属性（0.3 时代保留的废弃兼容层）；langchain 1.x 已删除该透传，
# 二者在同一环境共存时读取即抛 "module 'langchain' has no attribute 'debug'"。
# 本项目不直接依赖根包 langchain，这里启动时按需补齐默认属性，任意版本组合均可运行。
try:
    import langchain as _langchain_root

    for _attr, _default in (("debug", False), ("verbose", False), ("llm_cache", None)):
        if not hasattr(_langchain_root, _attr):
            setattr(_langchain_root, _attr, _default)
except ImportError:
    # 未安装根包时 langchain-core 会自动跳过该分支，无需处理
    pass

logger = logging.getLogger(__name__)

__all__ = [
    "SimpleState",
    "CatalogModule",
    "AudioModule",
    "PageModule",
    "DEFAULT_GREETING",
    "OUTPUT_DIR",
    "set_progress_emitter",
    "_emit",
]

# 模块类型与图状态（SimpleState / CatalogModule / AudioModule / PageModule）
# 已迁往 state.py，并在上方 re-export，兼容既有 `from app.agent.nodes import ...`。

DEFAULT_GREETING = "你好，我是 MAIC 助手，可以为你讲解知识并生成配套音频与页面，想听点什么？"

# 课件产物根目录：nodes.py 位于 server/app/agent/ 下，向上两级即 server/ 目录
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "outputs"

# ── 生成进度上报（供 SSE 流式接口 /api/chat/stream 使用）────────────────────
# 用 ContextVar（上下文变量）保存回调：asyncio.gather 派出的并发子任务会继承
# 同一上下文，因此逐页/逐音频的完成事件也能安全读到同一个回调。
# 未设置时 _emit 为空操作——原 POST /chat 老接口不设回调，行为完全不变。
ProgressEmitter = Callable[[dict], None]

_PROGRESS_EMITTER: ContextVar[Optional[ProgressEmitter]] = ContextVar(
    "progress_emitter", default=None
)


def set_progress_emitter(emitter: Optional[ProgressEmitter]) -> None:
    """由流式接口在跑图前注册回调，跑完后传 None 复位。"""
    _PROGRESS_EMITTER.set(emitter)


def _emit(step: str, status: str, **fields) -> None:
    """向当前注册的回调推送一条进度事件；回调异常绝不阻断生成主流程。

    事件结构：{step, status, current, total, message, data}，
    step 取值 check | outline | pages | audio | assemble | done | reply | error。
    """
    emitter = _PROGRESS_EMITTER.get()
    if emitter is None:
        return
    event: dict = {"step": step, "status": status}
    event.update({key: value for key, value in fields.items() if value is not None})
    try:
        emitter(event)
    except Exception:  # 前端断开连接等异常直接吞掉，只留调试日志
        logger.debug("进度事件推送失败：%s", event, exc_info=True)

CONTENT_ANALYSIS_PROMPT = """你是 MAIC（多智能体互动课堂）的内容分析助手，负责判断用户输入是否属于「需要讲解的内容」，并识别用户意图。

判断规则：
1. 打招呼、闲聊、询问软件功能、无意义字符 → 不需要讲解；
2. 提出一个可讲授的主题，例如「三国杀的玩法」「讲一下三国杀的玩法」「三国杀」「牛顿第二定律是什么」→ 需要讲解；
3. 需要讲解时，把用户的话归纳成一个简短、名词性的主题，去掉「讲一下」「介绍一下」这类动词，例如「三国杀的玩法」。

意图识别规则（intent 字段，多轮增量）：
1. 提出全新主题，或对话历史里没有已生成的课件 → "new_lecture"；
2. 要求把已生成的课件继续讲下去、补充后续内容（例如「继续」「接着讲」）→ "continue"；
3. 要求修改已生成课件的某一页/某一部分（例如「把第二页改一下」），reviseInds 填要改的页序号数组 → "revise_page"；
4. 只想要课后练习/测验（例如「给我出几道题」「来个测验」）→ "quiz_only"；
5. 其他不需要生成课件的对话（闲聊、追问、提问）→ "chat"。

输出要求：只输出 JSON，不要输出解释文字或代码块围栏。

需要讲解时：
{"needLecture": true, "topic": "三国杀的玩法", "reply": "", "intent": "new_lecture", "reviseInds": []}

不需要讲解时（topic 留空，reply 填一句简短的自我介绍式回复）：
{"needLecture": false, "topic": "", "reply": "你好，我是 MAIC 助手，可以为你讲解知识并生成配套音频与页面，想听点什么？", "intent": "chat", "reviseInds": []}"""

TOPIC_ROUTING_PROMPT = """你是 MAIC 的主题分类助手，负责判断一个讲解主题是否属于桌面卡牌游戏「三国杀」。

判定规则：
1. 主题指向三国杀本身，或它的玩法（身份场、2v2、国战、斗地主等）、武将、卡牌、装备、锦囊、规则术语 → 属于；
2. 主题是三国历史、其他卡牌游戏或桌游、与三国杀无关的内容 → 不属于；
3. 主题里只要出现「三国杀」三个字，一律属于。

输出要求：只输出 JSON，不要解释文字，不要代码块围栏。
属于：{"isSanguosha": true}
不属于：{"isSanguosha": false}"""

QUERY_REWRITE_PROMPT = """你是 MAIC 的检索查询改写助手，负责把一个讲解主题改写成 3~5 个不同角度的检索查询，用来从知识库里召回更全面的资料。

要求：
1. 角度包括但不限于：定义与概念、规则与流程、分类与举例、技巧与策略、常见误区；
2. 每个查询是一句简短的检索词（10~20 字），彼此不重复；
3. 只围绕主题本身，不要引入无关话题。

输出要求：只输出 JSON，不要解释文字，不要代码块围栏。
示例：{"queries": ["三国杀是什么游戏", "三国杀身份场规则流程", "三国杀武将技能怎么用"]}"""

FACT_CHECK_PROMPT = """你是 MAIC 的事实核查助手，负责对照参考资料核查课件大纲里是否有与资料矛盾的事实性错误。

要求：
1. 只核查事实（规则、数字、名称、流程、机制），不评判表述风格与详略；
2. 资料是权威依据；大纲与资料矛盾时判定为冲突，资料没提到的内容不算冲突；
3. 每条冲突给出模块 ind 与一句简短原因（20 字以内）。

输出要求：只输出 JSON，不要解释文字，不要代码块围栏。
有冲突：{"conflicts": [{"ind": 2, "reason": "摸牌数量与资料不符"}]}
无冲突：{"conflicts": []}"""

QUIZ_PROMPT = """你是 MAIC 的出题助手，负责根据课件大纲生成课后练习题。

要求：
1. 出 3~5 道题，覆盖大纲各模块的核心知识点，由浅入深；题型混合单选与多选（多选约占三分之一）；
2. 每题 4 个选项（options）；answer 是正确答案下标数组（下标从 0 开始）：单选题 1 个下标，多选题 2 个及以上下标；
3. analysis 是一句答案解析（30 字以内）；
4. 题目语言口语化、无歧义，不要出超纲题。

输出要求：只输出 JSON，不要解释文字，不要代码块围栏。
示例：{"quizList": [{"question": "三国杀中主公的胜利条件是什么？", "options": ["清掉反贼", "清掉反贼和内奸", "击杀主公", "活到最后"], "answer": [1], "analysis": "主公与忠臣需清掉反贼和内奸才能获胜。"}, {"question": "以下哪些属于主公的胜利条件？", "options": ["反贼全部阵亡", "内奸独活", "主公存活", "忠臣全部阵亡"], "answer": [0, 2], "analysis": "反贼全灭且主公存活即获胜。"}]}"""

SAFETY_PROMPT = """你是 MAIC 的内容安全审核助手，负责判断一段文本是否包含违规内容。

违规范围：色情低俗、暴力血腥、违法犯罪（赌博/毒品/诈骗等）、自残自杀引导、政治敏感、人身攻击辱骂。
正常的知识讲解（包括游戏规则、历史战争、医学常识）不算违规。

输出要求：只输出 JSON，不要解释文字，不要代码块围栏。
违规：{"safe": false, "reason": "一句话原因"}
安全：{"safe": true, "reason": ""}"""

OUTLINE_PROMPT = """你是 MAIC（多智能体互动课堂）的课件策划，负责把一个主题拆成「由浅入深」的讲解大纲，并写出可以直接念出来的口播讲稿。

要求：
1. 拆成 3~5 个模块，模块之间严格由浅入深：先讲「是什么、怎么玩」，再讲细节与技巧，最后讲进阶策略或常见误区；
2. 每个模块含 1~3 个知识点：title 是知识点标题（简短名词短语，8 字以内）；
3. context 是讲解这个知识点的口播讲稿（90~160 字），必须是连贯的一句话接一句话的口语讲述，不是要点罗列：
   - 开头一句话承接：第 1 个知识点先引入本页话题，之后每个知识点都先接住上一个（例如「座次搞清楚了，再来看入席时的顺序」），不要从半空中重新起头；
   - 中间把要点讲清楚，允许在收尾前补一句适度延伸，用一个小例子、一个类比或一个常见误区帮助理解，点到为止，不要展开成段；
   - 最后一句自然收束，让人能顺着往下听；
   - 每个模块的第 1 条 context 要用一句话接住上一个模块，第 1 个模块负责整堂课的引入；
   - 全部模块合计最多出现一次「注意这里」这类口头提醒；不要出现任何提问式互动（例如「你有没有想过」「大家想一想」）；
   - 只用口语和中文标点：不要 markdown 符号、不要括号注释、不要编号前缀、不要项目符号；
4. title 与 context 必须等长且一一对应（第 i 个 context 讲的就是第 i 个 title）；
5. ind 从 1 开始连续编号；
6. 资料只作为事实依据，不要原文照抄；资料与主题无关时忽略它，用你自己的知识。

输出要求：只输出 JSON，不要解释文字，不要代码块围栏，不要输出 HTML。
示例：
{"catalogList": [{"ind": 1, "title": ["什么是三国杀", "身份与胜负"], "context": ["咱们先从最基础的开始。三国杀是一款以三国为背景的桌面卡牌游戏，玩家各挑一名武将，用手里的卡牌互相攻击、防御和辅助，把这一层想通，后面的规则才好落地。", "明白玩法了，再看输赢怎么算。每名玩家开局都藏着一个身份，主公和忠臣要清掉反贼和内奸，反贼则要击杀主公。注意这里，内奸得先帮着主公清场，最后再单独取胜，这是新手最容易搞混的地方。"]}, {"ind": 2, "title": ["回合流程"], "context": ["身份和胜负心里有数了，我们接着看一个回合怎么走。一个回合依次走判定、摸牌、出牌、弃牌四个阶段，摸牌阶段固定摸两张，出牌阶段可以按手里的牌自由使用。记住这个顺序，你就能判断什么时候该攒牌、什么时候该出手。"]}]}"""

PAGE_PROMPT = """你是 MAIC 的课件前端，负责把一页口播讲稿提炼成要点，再渲染成一个简约风格的 HTML 页面。

视觉规范（必须遵守，整体是暖色简约风）：
1. 画布：宽度 100%，最大 1040px，aspect-ratio 16 / 9，背景 #FEFAF3，内边距 44px 56px，圆角 12px，
   字体 "PingFang SC", "Microsoft YaHei", system-ui, sans-serif，整体在页面中水平垂直居中；
2. 顶部：主标题（橙色 #E4620B，约 34px 加粗）+ 一行灰色副标题（#8C8C8C，约 17px，用于点明本页定位）；
3. 主体：左右两栏 grid（左 1fr、右 1.25fr，间距 24px）；
   左栏是主视觉卡片：浅米色底 #FAEFE1、圆角 8px、内容垂直居中，放本页最核心的术语或关键词，
   超大加粗橙色文字（约 42px），下方可配一行辅助说明（约 20px，可用拼音、英文或一句解释）；
   右栏是 2~3 个信息条：浅黄底 #FFF9EA、圆角 6px，每条一个橙色小标题（约 18px 加粗）
   + 深灰正文（#4A4A4A，约 15px，行高 1.75），可用「·」分隔或圆点列表；
4. 底部：橙色 #EC6520 通栏横幅，白色居中大字（约 20px，字间距 2px），写一句本页的总结口号；
5. 只用 HTML + 内联 <style>，禁止 <script>、禁止外链字体与图片，禁止 markdown 代码块围栏；
6. 整页内容必须在一屏内完整装下：不得出现纵向滚动条，不得有任何文字或模块被视口裁切，
   文案多时优先精简文字，其次压缩字号与间距（字号不低于 13px、行高不低于 1.5）；
7. 页面文字是对讲稿的「提炼」，不是讲稿的搬运（讲稿是给人听的，页面是给人看的）：
   - 每条信息条的小标题不超过 8 个字，正文写成 18~26 个字的一句话短语，可用「·」分隔两个关键词；
   - 必须自己改写和压缩，严禁整句照抄讲稿原话；
   - 不要出现「那么」「接下来」「我们说」「注意这里」「大家」这类口播连接词，页面不需要过渡句；
   - 讲稿里的例子、类比、易错点只保留结论，不要复述讲述过程；
   - 不要编造与讲稿矛盾的数字或规则；页面文字宜少不宜多，宁可留白。
   示例：讲稿「中餐讲究面门为尊，主位正对门口，主人坐主位，主宾在其右手边，其余人按身份年龄依次排开。」
   页面写成「主位正对门口 · 主宾居右 · 其余按身份年龄排开」，而不是照搬整句。

输出要求：直接输出完整 HTML 文档（以 <html> 开头、</html> 结尾），不要任何解释文字。"""

# RAG 召回的条数
RAG_TOP_K = 6

# 页面卡片正文的字数上限：讲稿篇幅远大于页面，兜底渲染时按这个长度压缩，避免溢出
PAGE_CARD_MAX_CHARS = 26

# 兜底页面与片段包装共用的样式，保证降级时也是同一套简约风格
PAGE_BASE_STYLE = """
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { display: flex; align-items: center; justify-content: center; min-height: 100vh; background: #FFFFFF;
           font-family: "PingFang SC", "Microsoft YaHei", system-ui, sans-serif; }
    .slide { width: 100%; max-width: 1040px; aspect-ratio: 16 / 9; background: #FEFAF3; border-radius: 12px;
             padding: 44px 56px; display: flex; flex-direction: column; }
    .slide h1 { color: #E4620B; font-size: 34px; font-weight: 700; }
    .slide .subtitle { color: #8C8C8C; font-size: 17px; margin-top: 10px; }
    .slide .body { flex: 1; display: grid; grid-template-columns: 1fr 1.25fr; gap: 24px; margin-top: 24px; min-height: 0; }
    .slide .hero { background: #FAEFE1; border-radius: 8px; padding: 24px; text-align: center;
                   display: flex; flex-direction: column; align-items: center; justify-content: center; }
    .slide .hero .term { color: #E4620B; font-size: 42px; font-weight: 700; line-height: 1.35; }
    .slide .hero .hint { color: #E4620B; font-size: 20px; margin-top: 14px; opacity: .85; }
    .slide .cards { display: flex; flex-direction: column; gap: 14px; overflow: hidden; }
    .slide .card { background: #FFF9EA; border-radius: 6px; padding: 14px 20px; }
    .slide .card h3 { color: #E4620B; font-size: 18px; margin-bottom: 8px; }
    .slide .card p { color: #4A4A4A; font-size: 15px; line-height: 1.75; }
    .slide .footer { background: #EC6520; color: #FFFFFF; text-align: center; font-size: 20px; letter-spacing: 2px;
                     padding: 12px; margin-top: 24px; border-radius: 4px; }
"""

## 节点
async def _invoke_text(system_prompt: str, user_prompt: str, temperature: float = 0.3) -> str:
    """调用 agent.py 里的模型，返回纯文本（system + user 两段提示词）。

    temperature 通过 bind 逐次覆盖：分类、抽 JSON 这类任务用 0 保证稳定，
    生成文案/页面时留一点随机性。
    """
    llm = get_llm().bind(temperature=temperature)
    result = await llm.ainvoke(
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

# 旧 content_analysis_node 已迁往 agents/intent_agent.py

def _as_str_list(value) -> list[str]:
    """把模型输出统一成字符串列表：字符串按换行拆开，列表逐项转字符串，其余视为空。"""
    if isinstance(value, str):
        return [part.strip() for part in value.split("\n") if part.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


# 讲稿里常见的 markdown 噪声：行首项目符号/编号、加粗/斜体/行内代码、标题井号
_SCRIPT_MARKDOWN = re.compile(r"^\s*(?:[-*•·]+|\d+[.、)])\s*", re.MULTILINE)
_SCRIPT_SYMBOLS = re.compile(r"\*\*|__|~~|`|#{1,6}\s*")
_SCRIPT_SPACES = re.compile(r"[ \t\u00a0\u3000]+")

# 页面提炼时该剔除的口播填充词：这些词只服务于「听」，放到页面上是噪音
# 只列明显不影响语义的连接词，避免误删「注意事项」这类实词
_SCRIPT_FILLER = re.compile(
    r"(那么|接下来|我们说|咱们|大家|注意这里|首先|其次|然后|另外|所以说|也就是说)"
)


def _clean_script(text: str) -> str:
    """清洗讲稿：去掉模型残留的 markdown 痕迹与多余空白，保证能被 TTS 直接朗读。

    只动符号与空白，不改写文字本身，避免二次生成带来的偏差。
    """
    cleaned = str(text or "").strip()
    cleaned = _SCRIPT_MARKDOWN.sub("", cleaned)
    cleaned = _SCRIPT_SYMBOLS.sub("", cleaned)
    cleaned = "\n".join(_SCRIPT_SPACES.sub(" ", line).strip() for line in cleaned.splitlines())
    return cleaned.strip()


def _page_snippet(text: str, limit: int = PAGE_CARD_MAX_CHARS) -> str:
    """把口播讲稿压成页面可用的短句：按句号顺序拼整句、剔除口播填充词，超长截断。

    页面只需要结论，不需要过渡；兜底渲染走这里，保证长讲稿不会撑破卡片版式。
    """
    cleaned = _clean_script(text)
    if not cleaned:
        return ""

    snippet = ""
    for sentence in re.split(r"(?<=[。！？；!?;])", cleaned):
        sentence = _SCRIPT_FILLER.sub("", sentence).strip(" ，、,；;。！")
        if not sentence:
            continue
        candidate = f"{snippet}，{sentence}" if snippet else sentence
        if snippet and len(candidate) > limit:  # 拼上会超长就停在上一句
            break
        snippet = candidate
        if len(snippet) >= limit // 2:  # 已经够撑起一条卡片，不必再拼
            break

    snippet = snippet.strip("，、, ")
    if len(snippet) > limit:
        snippet = snippet[:limit].rstrip("，、, ") + "…"
    return snippet


def _normalize_catalog(data: dict) -> list[CatalogModule]:
    """校验并规整模型给出的大纲：清洗讲稿、title 与 context 按短的截齐、ind 重新连续编号。"""
    catalog: list[CatalogModule] = []
    for item in data.get("catalogList") or []:
        if not isinstance(item, dict):
            continue
        titles = _as_str_list(item.get("title"))
        scripts = (_clean_script(content) for content in _as_str_list(item.get("context")))
        contexts = [script for script in scripts if script]
        count = min(len(titles), len(contexts))  # 以短的一侧为准，保证一一对应
        if count == 0:
            continue
        catalog.append(
            {"ind": len(catalog) + 1, "title": titles[:count], "context": contexts[:count]}
        )
    return catalog


def _module_points(module: CatalogModule) -> str:
    """把一个模块拼成「1. 标题：口播讲稿」的编号列表，作为生成页面的原文输入。"""
    return "\n".join(
        f"{i}. {title}：{content}"
        for i, (title, content) in enumerate(zip(module["title"], module["context"]), 1)
    )


def _module_outline(module: CatalogModule) -> str:
    """模块大纲的纯文本形态（标题 + 口播讲稿），写进 pageList 的 context 字段。"""
    return "\n".join(
        f"{title}：{content}" for title, content in zip(module["title"], module["context"])
    )


def _retrieve_docs(topic: str, top_k: int = RAG_TOP_K) -> list[str]:
    """同步检索三国杀知识库（混合检索：语义 + BM25 关键词），返回命中的原文。

    混合检索的两路输入：dense 路要查询向量，sparse 路要查询原文，因此向量化和原文都要传。
    """
    client = get_client()
    hits = hybrid_search(
        client,
        query_vector=embed_text(topic),
        query_text=topic,
        limit=top_k,
    )
    return [
        str(hit.get("page_content") or "").strip()
        for hit in hits
        if str(hit.get("page_content") or "").strip()
    ]


async def _search_sanguosha_docs(topic: str) -> list[str]:
    """在线程里执行检索：pymilvus 与 embedding 都是同步网络 IO，直接调用会阻塞事件循环。"""
    try:
        return await asyncio.to_thread(_retrieve_docs, topic)
    except Exception as exc:  # 知识库不可用不该中断整堂课，降级为纯模型知识
        logger.warning("三国杀资料检索失败，改用模型自身知识：%s", exc)
        return []


async def _is_sanguosha_topic(topic: str) -> bool:
    """判断主题是否属于三国杀；模型没给出布尔值时按关键词兜底。"""
    raw = await _invoke_text(TOPIC_ROUTING_PROMPT, f"讲解主题：{topic}", temperature=0)
    flag = _parse_json(raw).get("isSanguosha")
    if isinstance(flag, bool):
        return flag
    logger.warning("主题判定未返回布尔值，按关键词兜底：%s", raw)
    return "三国杀" in topic


async def _build_catalog(topic: str, docs: list[str]) -> list[CatalogModule]:
    """结合模型自身知识与 RAG 资料，按由浅入深的顺序生成大纲。"""
    if docs:
        material = "\n\n".join(f"[资料{i}] {doc}" for i, doc in enumerate(docs, 1))
    else:
        material = "（本次没有检索到资料，请完全依赖你自己的知识。）"

    user_prompt = f"讲解主题：{topic}\n\n可参考的资料：\n{material}"
    raw = await _invoke_text(OUTLINE_PROMPT, user_prompt, temperature=0.4)
    catalog = _normalize_catalog(_parse_json(raw))

    if not catalog:  # 模型没吐出合法 JSON 时降级成单模块，避免整条链路断掉
        logger.warning("大纲解析失败，降级为单模块：%s", raw)
        catalog = [
            {
                "ind": 1,
                "title": [topic],
                "context": [f"围绕「{topic}」的核心概念、基础流程与常见要点展开讲解。"],
            }
        ]
    return catalog


def _finish_html(fragment: str) -> str:
    """把模型返回的 HTML 片段包成完整文档，样式沿用同一套简约风。"""
    return (
        '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="UTF-8">\n'
        f"<style>{PAGE_BASE_STYLE}</style>\n</head>\n<body>\n{fragment}\n</body>\n</html>"
    )


def _fallback_page(topic: str, module: CatalogModule) -> str:
    """兜底页面：模型没给出可用 HTML 时用固定模板渲染，保证每页都有内容。

    讲稿比页面要点长得多，这里用 _page_snippet 压到卡片容量内，避免降级时文字溢出。
    """
    hero = escape(module["title"][0]) if module["title"] else escape(topic)
    cards = "\n".join(
        f'<div class="card"><h3>{escape(title)}</h3>'
        f"<p>{escape(_page_snippet(content) or title)}</p></div>"
        for title, content in zip(module["title"], module["context"])
    )
    fragment = (
        '<div class="slide">\n'
        f"  <h1>{escape(topic)}</h1>\n"
        f'  <div class="subtitle">第 {module["ind"]} 页 · 要点讲解</div>\n'
        '  <div class="body">\n'
        f'    <div class="hero"><div class="term">{hero}</div>'
        '<div class="hint">本页核心概念</div></div>\n'
        f'    <div class="cards">\n{cards}\n    </div>\n'
        "  </div>\n"
        '  <div class="footer">跟着大纲一步步来，理解比记忆更重要</div>\n'
        "</div>"
    )
    return _finish_html(fragment)


def _clean_html(raw: str) -> str:
    """去掉模型附带的 ```html 围栏，并截取 <html> 到 </html> 之间的内容。"""
    text = (raw or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"```\s*$", "", text).strip()
    lower = text.lower()
    start, end = lower.find("<html"), lower.rfind("</html>")
    if start != -1 and end > start:
        return text[start : end + len("</html>")]
    return text


# 内容超过一屏时按比例整体缩放，消灭滚动条与裁切；容器尺寸变化时重算。
# 只在溢出时触发（缩放比 >= 1 不注入 zoom），导出视频的 1280×720 截图不受影响。
_FIT_SCRIPT = """
<script>
(function () {
  function fit() {
    var body = document.body, html = document.documentElement;
    if (!body || !html || !html.clientHeight) return;
    body.style.zoom = "";
    var ratio = Math.min(
      html.clientHeight / Math.max(html.scrollHeight, 1),
      html.clientWidth / Math.max(html.scrollWidth, 1)
    );
    if (ratio < 0.999) body.style.zoom = String(ratio * 0.995);
  }
  window.addEventListener("load", fit);
  window.addEventListener("resize", fit);
  setTimeout(fit, 0);
})();
</script>
"""


def _inject_fit(html: str) -> str:
    """把自适配脚本注入页面尾部；已经是完整文档才注入，兜底与模型页统一走这里。"""
    lower = html.lower()
    if "</body>" in lower:
        at = lower.rfind("</body>")
    elif "</html>" in lower:
        at = lower.rfind("</html>")
    else:
        return html
    return html[:at] + _FIT_SCRIPT + "\n" + html[at:]


async def _build_page(
    topic: str,
    module: CatalogModule,
    total: int,
    on_error: Optional[Callable[[Exception], None]] = None,
) -> str:
    """根据一个模块的大纲（口播讲稿）生成对应 HTML 页面，失败时回退到兜底模板。

    出口统一注入自适配脚本：任一路径（模型页 / 片段包装 / 兜底页）产出的
    页面在窄容器里溢出时都会自动缩回一屏内，不再出现滚动条和内容裁切。
    on_error：页面降级到兜底模板时的回调（供 page_agent 上报失败清单），默认不通知。"""
    return _inject_fit(await _build_page_raw(topic, module, total, on_error))


async def _build_page_raw(
    topic: str,
    module: CatalogModule,
    total: int,
    on_error: Optional[Callable[[Exception], None]] = None,
) -> str:
    """生成页面本体（不注入脚本），失败时回退到兜底模板。"""
    user_prompt = (
        f"整套课件主题：{topic}\n"
        f"本页序号：第 {module['ind']} 页，共 {total} 页\n"
        f"本页口播讲稿（供你理解内容，不是页面文案，页面文字要从中提炼）：\n{_module_points(module)}\n\n"
        "请据此生成这一页的 HTML：主标题概括本页核心知识点，副标题点明本页在整套课件中的位置；"
        "卡片和横幅里的文字都要压缩成短语，不要照抄讲稿里的原句。"
    )
    try:
        raw = await _invoke_text(PAGE_PROMPT, user_prompt, temperature=0.5)
    except Exception as exc:
        logger.warning("第 %s 页生成失败，使用兜底模板：%s", module["ind"], exc)
        if on_error is not None:
            on_error(exc)
        return _fallback_page(topic, module)

    page = _clean_html(raw)
    if "<html" in page.lower():
        return page
    if page.startswith("<"):  # 模型只给了片段，补成完整文档
        return _finish_html(page)
    logger.warning("第 %s 页输出不像 HTML，使用兜底模板：%s", module["ind"], raw[:200])
    if on_error is not None:
        on_error(RuntimeError("模型输出不是可用的 HTML 页面"))
    return _fallback_page(topic, module)

# 旧 text_organization_node 已拆为 agents/query_rewrite_agent.py、agents/research_agent.py、
# agents/outline_agent.py、agents/page_agent.py

# 旧 audio_node 已迁往 agents/audio_agent.py；
# 旧 html_audio_assemble 已迁往 agents/assemble_agent.py（MP4 注释块原样保留）。