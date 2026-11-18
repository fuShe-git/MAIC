import asyncio
import json
import logging
import re
from html import escape
from pathlib import Path
from typing import TypedDict
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.agent import get_llm
from app.tools.audioCreate import tts_save
from app.tools.courseAssemble import build_course_html
from app.tools.milvus import embed_text, get_client, hybrid_search
from app.tools.videoExport import (
    estimate_duration,
    export_mp4,
    read_duration,
    render_page_frames,
)

logger = logging.getLogger(__name__)

class CatalogModule(TypedDict):
    """大纲里的一个模块，对应一个即将讲解的页面。

    title 与 context 一一对应（第 i 个 title 的讲稿就是第 i 个 context），
    因此二者长度必须相等。context 是能直接朗读的口播讲稿，不是页面文案；
    页面文字由它二次提炼而来。
    """

    ind: int            # 序号
    title: list[str]    # 该页面内各知识点的标题
    context: list[str]  # 与 title 一一对应的口播讲稿（90~160 字，口语连贯）


class AudioModule(TypedDict):
    """一个模块对应的音频数据。

    ind 与 CatalogModule.ind 对应，用来把音频和它所属的页面配上对。
    """

    ind: int        # 序号，与 catalogList 中对应模块的 ind 一致
    context: str    # 清洗后的口播台词（多个知识点讲稿按顺序拼接）
    audio_path: str  # mp3 落盘绝对路径；空串表示该页没有旁白
    duration: float  # 音频时长（秒），决定该页在视频里的停留时长


class PageModule(TypedDict):
    """一个模块对应的页面数据，即「大纲 + 页面」的组合。

    ind 与 catalogList / audioList 中同序号的模块对应，
    context 为该模块的大纲文本，htmlpage 为渲染用 HTML。
    """

    ind: int       # 序号，与前面对应
    context: str   # 本部分大纲，即标题 + 口播讲稿
    htmlpage: str  # 本部分对应的页面，HTML 代码


# state
class SimpleState(TypedDict):
    userIssue:str           # 用户问题
    needLecture:bool        # 是否需要讲解；False 时图直接结束
    reply:str               # 不需要讲解时返回给用户的简单回复
    topic:str               # 从用户问题里归纳出的讲解主题，供后续节点使用
    catalogList:list[CatalogModule]  # 大纲，每个模块对应的页面
    audioList:list[AudioModule]      # 台词，每个模块对应的音频
    pageList:list[PageModule]        # 页面，每个模块对应的页面
    courseHtml:str          # 组装后的自包含课件 HTML 落盘路径（节点4产出）
    videoUrl:str            # 导出的 MP4 落盘路径（节点4产出；失败为空串）

DEFAULT_GREETING = "你好，我是 MAIC 助手，可以为你讲解知识并生成配套音频与页面，想听点什么？"

# 课件产物根目录：nodes.py 位于 server/app/agent/ 下，向上两级即 server/ 目录
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "outputs"

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

TOPIC_ROUTING_PROMPT = """你是 MAIC 的主题分类助手，负责判断一个讲解主题是否属于桌面卡牌游戏「三国杀」。

判定规则：
1. 主题指向三国杀本身，或它的玩法（身份场、2v2、国战、斗地主等）、武将、卡牌、装备、锦囊、规则术语 → 属于；
2. 主题是三国历史、其他卡牌游戏或桌游、与三国杀无关的内容 → 不属于；
3. 主题里只要出现「三国杀」三个字，一律属于。

输出要求：只输出 JSON，不要解释文字，不要代码块围栏。
属于：{"isSanguosha": true}
不属于：{"isSanguosha": false}"""

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
6. 页面文字是对讲稿的「提炼」，不是讲稿的搬运（讲稿是给人听的，页面是给人看的）：
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

# 节点1
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


async def _build_page(topic: str, module: CatalogModule, total: int) -> str:
    """根据一个模块的大纲（口播讲稿）生成对应 HTML 页面，失败时回退到兜底模板。"""
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
        return _fallback_page(topic, module)

    page = _clean_html(raw)
    if "<html" in page.lower():
        return page
    if page.startswith("<"):  # 模型只给了片段，补成完整文档
        return _finish_html(page)
    logger.warning("第 %s 页输出不像 HTML，使用兜底模板：%s", module["ind"], raw[:200])
    return _fallback_page(topic, module)

# 节点2
async def text_organization_node(state:SimpleState):
    '''根据主题查找资料，生成大纲,并生成对应页面（需要自体校验）'''
    topic = (state.get("topic") or state.get("userIssue") or "").strip()
    print('节点二输入主题', topic)

    # 兜底：没有主题就无从讲解，直接交回引导语
    if not topic:
        return {"needLecture": False, "reply": DEFAULT_GREETING, "catalogList": [], "pageList": []}

    # 1. 先问模型这是不是三国杀，确认相关才去检索，避免无关主题白白触发向量库
    docs: list[str] = []
    if await _is_sanguosha_topic(topic):
        docs = await _search_sanguosha_docs(topic)
        print(f'节点二命中三国杀知识库，召回 {len(docs)} 条资料')

    # 2. 带着检索结果回到模型：融合自身知识与 RAG 内容，按由浅入深产出大纲
    catalog = await _build_catalog(topic, docs)

    # 3. 每个模块生成一页 HTML：并发请求，gather 保证返回顺序与 catalog 一致
    pages_html = await asyncio.gather(
        *(_build_page(topic, module, len(catalog)) for module in catalog)
    )

    # 4. 汇总成 [{ind, context: 本部分大纲, htmlpage: 本部分对应的 HTML}]
    page_list: list[PageModule] = [
        {"ind": module["ind"], "context": _module_outline(module), "htmlpage": page}
        for module, page in zip(catalog, pages_html)
    ]
    print('节点二产出页面数', len(page_list))
    return {"catalogList": catalog, "pageList": page_list}

# 节点3
async def audio_node(state: SimpleState):
    '''把每个模块的口播讲稿清洗后合成 TTS 音频，并读回时长。

    台词直接复用节点2大纲里的 context（本就是可朗读的口播讲稿），不调模型二次生成；
    各模块并发合成，单页失败只记日志、该页走时长估算兜底，不阻断整体。
    '''
    catalog: list[CatalogModule] = state.get("catalogList") or []
    if not catalog:
        return {"audioList": []}

    audio_dir = OUTPUT_DIR / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    async def _synthesize(module: CatalogModule) -> AudioModule:
        ind = module["ind"]
        script = _clean_script("\n".join(module["context"]))
        audio_path = ""
        duration = estimate_duration(script)

        if script:
            target = audio_dir / f"{uuid4().hex}.mp3"
            try:
                await tts_save(script, target)
                audio_path = str(target)
                try:
                    duration = read_duration(target)
                except Exception as exc:
                    logger.warning("第 %s 页音频时长读取失败，按字数估算：%s", ind, exc)
            except Exception as exc:
                logger.warning("第 %s 页 TTS 合成失败，该页将无旁白：%s", ind, exc)

        return {
            "ind": ind,
            "context": script,
            "audio_path": audio_path,
            "duration": duration,
        }

    audio_list = await asyncio.gather(*(_synthesize(module) for module in catalog))
    print("节点三产出音频数", len(audio_list))
    return {"audioList": list(audio_list)}

# 节点4
async def html_audio_assemble(state: SimpleState):
    '''整理页面 + 音频，组装成自包含课件 HTML 并导出 MP4。

    组装与导出都只依赖前面节点的结构化产出，不调模型；
    导出失败只记日志、videoUrl 置空，不阻断大纲/页面/讲稿这些已成功的产物。
    '''
    topic = (state.get("topic") or state.get("userIssue") or "课件").strip()
    pages: list[PageModule] = state.get("pageList") or []
    audios: list[AudioModule] = state.get("audioList") or []

    if not pages:
        logger.warning("没有页面，跳过组装与导出")
        return {"courseHtml": "", "videoUrl": ""}

    video_dir = OUTPUT_DIR / "videos"
    video_dir.mkdir(parents=True, exist_ok=True)
    uid = uuid4().hex

    # 1. 组装课件 HTML（每页 base64 音频 + 固定播放 JS），落盘可直接双击播放
    course_html = build_course_html(topic, pages, audios)
    course_path = video_dir / f"{uid}.html"
    course_path.write_text(course_html, encoding="utf-8")

    # 2. 截帧 + FFmpeg 合成 MP4；失败仅告警，不阻断主链路
    audio_by_ind = {int(a.get("ind") or 0): a for a in audios}
    audio_paths: list[str] = []
    durations: list[float] = []
    for page in pages:
        audio = audio_by_ind.get(int(page.get("ind") or 0), {})
        audio_paths.append(str(audio.get("audio_path") or ""))
        duration = audio.get("duration")
        if not duration:
            duration = estimate_duration(str(page.get("context") or ""))
        durations.append(float(duration))

    video_path = ""
    try:
        frames = await render_page_frames([page["htmlpage"] for page in pages])
        video_path = str(
            await asyncio.to_thread(export_mp4, frames, audio_paths, durations, video_dir)
        )
    except Exception as exc:
        logger.warning("视频导出失败，本次仅交付课件 HTML：%s", exc)

    print("节点四产出课件", course_path.name, "视频", video_path or "无")
    return {"courseHtml": str(course_path), "videoUrl": video_path}