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

from app.agent.agent import get_llm, get_output_lang
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

# 课件产物根目录统一由 app.core.paths 管理（可用 MAIC_OUTPUT_DIR 覆盖）
from app.core.paths import OUTPUT_DIR  # noqa: E402

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
{"needLecture": false, "topic": "", "reply": "你好，我是MAIC助手，未检测到讲解内容，请重新描述您想了解的内容。", "intent": "chat", "reviseInds": []}"""

TOPIC_ROUTING_PROMPT = """你是 MAIC 的主题分类助手，负责判断一个讲解主题是否属于桌面卡牌游戏「三国杀」。

判定规则：
1. 主题指向三国杀本身，或它的玩法（身份场、2v2、国战、斗地主等）、武将、卡牌、装备、锦囊、规则术语 → 属于；
2. 主题是三国历史、其他卡牌游戏或桌游、与三国杀无关的内容 → 不属于；
3. 主题里只要出现「三国杀」三个字，一律属于。

输出要求：只输出 JSON，不要解释文字，不要代码块围栏。
属于：{"isSanguosha": true}
不属于：{"isSanguosha": false}"""

PAGE_TOPIC_CHECK_PROMPT = """你是 MAIC 的页面主题校验助手，负责判断一页课件是否值得生成。

判定依据：整套课件主题、本页主题、本页简介与讲稿。
判定规则：
1. 本页主题为空，或只有「新增页面」「待补充」这类占位内容，且简介与讲稿也没有实质内容 → 不通过；
2. 本页主题/讲稿与整套课件主题无关（换成别的话题，或只沾边却没有围绕本页主题讲解） → 不通过；
3. 本页主题紧扣整套课件主题，哪怕是其中一个具体知识点 → 通过；
4. 只判断主题是否相关、内容是否为空，不评判措辞、篇幅或资料是否充分。

输出要求：只输出 JSON，不要解释文字，不要代码块围栏。
通过：{"relevant": true}
不通过：{"relevant": false, "reason": "一句话原因"}"""

NEW_MODULE_NARRATION_PROMPT = """你是 MAIC 的课件讲稿撰写助手，负责给用户新增的一页主题补写口播讲解稿和页面简介。

输入：整套课件主题、本页主题、用户填写的页面简介（可能为空）。
要求：
1. 讲解稿必须紧扣整套课件主题，围绕本页主题展开；用户填写的简介是要讲清的重点，必须自然融入，不要照搬原句；
2. context 只给 1 条 130~200 字的口播稿，语言跟随本次语言要求，连贯口语讲述，说清是什么、为什么、怎么做，并用一个小例子或常见误区收尾，不要 markdown 符号、括号注释、编号前缀、项目符号，不要提问式互动；
3. summary 用 25~45 字说明本页要讲清什么、从什么角度讲，写在卡片简介里，不要照搬讲稿原句；
4. 不要输出 HTML 或解释文字。

输出要求：只输出 JSON，不要代码块围栏。
示例：{"context": ["……"], "summary": "……"}"""

QUERY_REWRITE_PROMPT = """你是 MAIC 的检索查询改写助手，负责把一个讲解主题改写成 5~6 个不同角度的检索查询，用来从知识库里召回更全面的资料。

要求：
1. 角度包括但不限于：定义与概念、规则与流程、分类与举例、具体数值与例外、技巧与策略、常见误区；
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

OUTLINE_PLAN_PROMPT = """你是 MAIC（多智能体互动课堂）的课件策划，负责把一个主题拆成「由浅入深」的讲解大纲骨架。

要求：
1. 必须紧扣用户给出的讲解主题：整套课件标题和每个模块都服务这个主题本身，不能擅自缩窄成某个子玩法、具体条件或换成别的主题；若主题是某款游戏的玩法，第 1 个模块必须是该游戏玩法的整体入门，不能直接跳到某个具体规则；
2. 模块数量按内容量决定，不固定：内容多就多给几个模块，宁可把一个大知识点拆成两页讲透，也不要把几页的内容挤成一页；整套课件建议 8~12 页，但以讲透内容为第一优先；
3. 模块之间严格由浅入深，形成一条完整课程线：前 1~2 页讲「是什么、整体怎么玩」，中间逐页拆规则细节与操作技巧，最后 1~2 页讲进阶策略、常见误区与全局总结；相邻模块不能重复讲同一件事；
4. 每个模块给 2~6 个知识点标题（title），是简短名词短语（中文 8 字以内、英文 6 词以内），彼此不重复；同一模块的知识点必须语义紧密相关、围绕同一个子主题，跨度大的内容要拆到不同模块；知识点超过 3 条的模块会由系统按语义自动拆成多页，不用你手动拆；
5. 每个模块还要有 summary，用 25~45 字说明本页要讲清什么、从什么角度讲，写在卡片简介里；不能只是标题的复述；
6. ind 从 1 开始连续编号；
7. 资料只作为事实依据，不要原文照抄；资料里提到的数值、阶段、例外情况要尽量安排进对应模块；资料与主题无关时忽略它，用你自己的知识。

输出要求：只输出 JSON，不要解释文字，不要代码块围栏，不要输出 HTML，不要输出 context（讲稿由后续步骤单独撰写）。
示例（只演示字段格式，实际模块数与知识点数按内容量决定）：
{"catalogList": [{"ind": 1, "summary": "先认识游戏的身份构成与胜负目标", "title": ["什么是三国杀", "身份与胜负"]}, {"ind": 2, "summary": "按判定、摸牌、出牌、弃牌四个阶段理解一个回合怎么走", "title": ["回合流程", "阶段要点"]}]}"""

MODULE_SCRIPT_PROMPT = """你是 MAIC 的课件讲稿撰写助手，负责为讲解大纲里的一页写可以直接念出来的口播讲稿。

输入：整套课件主题、本页简介、本页知识点标题（按顺序）、前后页的标题。
要求：
1. 必须逐字围绕整套课件主题展开，不要缩窄成别的子话题；
2. 为每个知识点各写一条 context，条数与知识点条数完全一致、顺序一一对应，每条 130~200 字；
3. 每条 context 都要讲透一层，不能只罗列结论：
   - 开头一句话承接：本页第 1 条要接住上一页（第 1 页则负责整堂课的引入），之后每条都先接住上一条，不要从半空中重新起头；
   - 中间说清这个知识点是什么、为什么会这样、具体怎么做；
   - 收尾给一个小例子、一个类比或一个常见误区，帮助听众真正听懂，点到为止；
   - 最后一句自然收束，让人能顺着往下听；
4. 相邻两条不要重复同一个信息点，也不要提前把下一页的内容讲掉；
5. 只用口语和自然标点（跟随本次语言要求）：不要 markdown 符号、不要括号注释、不要编号前缀、不要项目符号；不要出现任何提问式互动（例如中文的「你有没有想过」「大家想一想」，英文的 "Have you ever wondered?"）；本页最多出现一次类似的口头提醒；
6. 不要输出 HTML 或解释文字。

输出要求：只输出 JSON，不要代码块围栏。
示例：{"context": ["……", "……"]}"""

DEFAULT_VISUAL_STYLE = """视觉规范（必须遵守，整体是暖色简约风）：
1. 画布：宽度 100%，最大 1040px，aspect-ratio 16 / 9，背景 #FEFAF3，内边距 44px 56px，圆角 12px，
   字体 "PingFang SC", "Microsoft YaHei", system-ui, sans-serif，整体在页面中水平垂直居中；
2. 顶部：主标题（橙色 #E4620B，约 34px 加粗）+ 一行灰色副标题（#8C8C8C，约 17px，用于点明本页定位）；
3. 主体：左右两栏 grid（左 1fr、右 1.25fr，间距 24px）；
   左栏是主视觉卡片：浅米色底 #FAEFE1、圆角 8px、内容垂直居中，放本页最核心的术语或关键词，
   超大加粗橙色文字（约 42px），下方可配一行辅助说明（约 20px，可用拼音、英文或一句解释）；
   右栏是 2~3 个信息条：浅黄底 #FFF9EA、圆角 6px，每条一个橙色小标题（约 18px 加粗）
   + 深灰正文（#4A4A4A，约 15px，行高 1.75），可用「·」分隔或圆点列表；
4. 底部：橙色 #EC6520 通栏横幅，白色居中大字（约 20px，字间距 2px），写一句本页的总结口号；"""

PAGE_PROMPT_TEMPLATE = """你是 MAIC 的课件前端，负责把一页口播讲稿提炼成要点，再渲染成 HTML 页面。

{visual_style}
5. 只用 HTML + 内联 <style>，禁止 <script>、禁止外链字体与图片，禁止 markdown 代码块围栏；
6. 整页内容必须在一屏内完整装下：不得出现纵向滚动条，不得有任何文字或模块被视口裁切，
   文案多时优先精简文字，其次压缩字号与间距（字号不低于 13px、行高不低于 1.5）；
7. 页面文字是对讲稿的「提炼」，不是讲稿的搬运（讲稿是给人听的，页面是给人看的）：
   - 每条信息条的小标题不超过 8 个字，正文写成 18~26 个字的一句话短语，可用「·」分隔两个关键词；
   - 必须自己改写和压缩，严禁整句照抄讲稿原话；
   - 不要出现「那么」「接下来」「我们说」「注意这里」「大家」（英文如 well / next / now / please note）这类口播连接词，页面不需要过渡句；
   - 讲稿里的例子、类比、易错点只保留结论，不要复述讲述过程；
   - 不要编造与讲稿矛盾的数字或规则；页面文字宜少不宜多，宁可留白。
   示例：讲稿「中餐讲究面门为尊，主位正对门口，主人坐主位，主宾在其右手边，其余人按身份年龄依次排开。」
   页面写成「主位正对门口 · 主宾居右 · 其余按身份年龄排开」，而不是照搬整句。
8. 信息条数量必须与输入的知识点条数完全一致（输入几条就画几条，最多 3 条），不得自行增加、删减或合并知识点；宁可留白，也不要把多条内容挤进同一条里。

输出要求：直接输出完整 HTML 文档（以 <html> 开头、</html> 结尾），不要任何解释文字。"""

PAGE_PROMPT = PAGE_PROMPT_TEMPLATE.format(visual_style=DEFAULT_VISUAL_STYLE)

# RAG 召回的条数：页数变多后每页都要有事实依据，召回量同步放大
RAG_TOP_K = 10

# 页面卡片正文的字数上限：讲稿篇幅远大于页面，兜底渲染时按这个长度压缩，避免溢出
PAGE_CARD_MAX_CHARS = 26

# 单页知识点条数上限（右栏信息条容量）：一个模块内容超过这个数就自动拆成多页，
# 页数由内容量决定，而不是由大纲模块条数决定。
MAX_POINTS_PER_PAGE = 3

# 大纲阶段单个模块最多允许的知识点数：超出部分交给拆页逻辑处理，不再直接截断丢弃。
MAX_POINTS_PER_MODULE = 12

# 语义拆页：把内容过多的模块按知识点相关性分组，相关的放一起，允许分得更细。
SEMANTIC_SPLIT_PROMPT = """你是课件策划助手，负责把一组知识点标题按语义相关性分组，供后续拆成多页。

要求：
1. 语义相近、围绕同一个子主题的知识点放在同一组；跨度大、属于不同子主题的知识点必须分到不同组；
2. 每组 1~{max_points} 条，宁可分细一点，也不要把不相关的知识点硬凑在一起；
3. 每个知识点必须且只能出现在一个组里，不能遗漏、不能重复；
4. 保持原始顺序的相对先后，序号小的组排在前面；
5. 只输出 JSON，不要解释文字，不要代码块围栏。

示例（输入 6 条知识点，按语义拆成 3 组）：
{{"groups": [[0, 1], [2], [3, 4, 5]]}}"""

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


def _theme_css(theme: Optional[dict]) -> str:
    """把用户所选样式转成后置 CSS；颜色由 theme_agent 校验为 6 位十六进制。"""
    if not isinstance(theme, dict) or not theme.get("previewColors"):
        return ""
    colors = theme.get("previewColors") or {}
    background = str(colors.get("background") or "#FFFFFF")
    primary = str(colors.get("primary") or "#2F5BEA")
    accent = str(colors.get("accent") or primary)
    text = str(colors.get("text") or "#1E1F29")
    return f"""
    .slide {{ background: {background}; }}
    .slide h1, .slide .hero .term, .slide .hero .hint, .slide .card h3 {{ color: {primary}; }}
    .slide .subtitle {{ color: {text}; opacity: .66; }}
    .slide .hero {{ background: {background}; border-left: 6px solid {accent}; }}
    .slide .card {{ background: #FFFFFF; border-left: 4px solid {accent}; }}
    .slide .card p {{ color: {text}; }}
    .slide .footer {{ background: {primary}; color: #FFFFFF; }}
"""


def _visual_style(theme: Optional[dict]) -> str:
    """用户选了样式时替换默认视觉规范，否则保持旧流程不变。"""
    if not isinstance(theme, dict):
        return DEFAULT_VISUAL_STYLE
    prompt = str(theme.get("stylePrompt") or "").strip()
    if not prompt:
        return DEFAULT_VISUAL_STYLE
    return "本次课件视觉规范（优先于其他配色建议）：\n" + prompt

# 输出语言要求：统一追加到 system 提示词末尾；JSON 字段名、HTML 标签等结构标识符不翻译
_LANGUAGE_RULES: dict[str, str] = {
    "zh": (
        "\n\n语言要求：用户使用中文交流，请全程使用中文撰写所有面向用户的文字内容"
        "（字段值、标题、简介、讲稿、题目、选项、解析、原因等）。"
        "JSON 字段名、代码标识符与 HTML 标签保持原样，不要翻译。"
    ),
    "en": (
        "\n\nLanguage requirement: The user is communicating in English. Write all "
        "user-facing text (field values, titles, summaries, scripts, questions, options, "
        "explanations, reasons, etc.) in English. Keep JSON keys, code identifiers and "
        "HTML tags unchanged; do not translate them."
    ),
}


def localized(zh_text: str, en_text: str) -> str:
    """按当前请求语言选择用户可见文案；非英文一律用中文，避免兜底文案串语言。"""
    return en_text if get_output_lang() == "en" else zh_text


## 节点
async def _invoke_text(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
    language: Optional[str] = None,
) -> str:
    """调用 agent.py 里的模型，返回纯文本（system + user 两段提示词）。

    temperature 通过 bind 逐次覆盖：分类、抽 JSON 这类任务用 0 保证稳定，
    生成文案/页面时留一点随机性。
    language 默认跟随本次请求的用户输入语言；内部检索等步骤可显式覆盖。
    """
    lang = language or get_output_lang()
    suffix = _LANGUAGE_RULES.get(lang, "")
    llm = get_llm().bind(temperature=temperature)
    result = await llm.ainvoke(
        [
            SystemMessage(content=system_prompt + suffix),
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
        titles = _as_str_list(item.get("title"))[:MAX_POINTS_PER_MODULE]
        scripts = (_clean_script(content) for content in _as_str_list(item.get("context")))
        contexts = [script for script in scripts if script]
        count = min(len(titles), len(contexts))  # 以短的一侧为准，保证一一对应
        if count == 0:
            continue
        summary = str(item.get("summary") or "").strip()[:120]
        if not summary:
            summary = f"围绕{'、'.join(titles[:count])}展开讲解，循序渐进理解本页重点。"
        catalog.append(
            {
                "ind": len(catalog) + 1,
                "title": titles[:count],
                "context": contexts[:count],
                "summary": summary,
            }
        )
    return catalog


def _normalize_plan(data: dict) -> list[CatalogModule]:
    """校验模型给出的大纲骨架：只取 title 与 summary，context 留空等下一步按页补写。"""
    plan: list[CatalogModule] = []
    for item in data.get("catalogList") or []:
        if not isinstance(item, dict):
            continue
        titles = _as_str_list(item.get("title"))[:MAX_POINTS_PER_MODULE]
        if not titles:
            continue
        summary = str(item.get("summary") or "").strip()[:120]
        if not summary:
            summary = f"围绕{'、'.join(titles)}展开讲解，循序渐进理解本页重点。"
        plan.append(
            {
                "ind": len(plan) + 1,
                "title": titles,
                "context": [],
                "summary": summary,
            }
        )
    return plan


def _mechanical_groups(count: int) -> list[list[int]]:
    """按数量机械切片，仅作为语义分组失败时的兜底。"""
    return [
        list(range(start, min(start + MAX_POINTS_PER_PAGE, count)))
        for start in range(0, count, MAX_POINTS_PER_PAGE)
    ]


async def _group_points_semantically(titles: list[str], summary: str) -> list[list[int]]:
    """调用模型按语义相关性给知识点分组，返回索引分组；失败时回退机械切片。"""
    indexed = "\n".join(f"{i}. {title}" for i, title in enumerate(titles))
    user_prompt = (
        f"模块简介：{summary}\n"
        f"知识点（带序号）：\n{indexed}\n\n"
        "请按语义相关性把上面的知识点分组。"
    )
    try:
        prompt = SEMANTIC_SPLIT_PROMPT.format(max_points=MAX_POINTS_PER_PAGE)
        raw = await _invoke_text(prompt, user_prompt, temperature=0)
        groups = _parse_json(raw).get("groups")
        if not isinstance(groups, list) or not groups:
            raise ValueError("模型未返回可用的分组")
        normalized: list[list[int]] = []
        seen: set[int] = set()
        for group in groups:
            if not isinstance(group, list) or not group:
                continue
            indexes: list[int] = []
            for item in group:
                text = str(item).strip()
                if not text.lstrip("-").isdigit():
                    continue
                index = int(text)
                if 0 <= index < len(titles) and index not in seen:
                    seen.add(index)
                    indexes.append(index)
            if indexes:
                normalized.append(sorted(indexes))
        if len(seen) != len(titles) or not normalized:
            raise ValueError("分组未完整覆盖全部知识点")
        # 模型偶尔会给出超过单页容量的组，再按数量切开，保证每页不挤
        capped: list[list[int]] = []
        for group in normalized:
            for start in range(0, len(group), MAX_POINTS_PER_PAGE):
                capped.append(group[start : start + MAX_POINTS_PER_PAGE])
        capped.sort(key=lambda group: group[0])
        return capped
    except Exception as exc:
        logger.warning("语义拆页失败，回退为按数量切片：%s", exc)
        return _mechanical_groups(len(titles))


async def _split_oversized_modules(modules: list[CatalogModule]) -> list[CatalogModule]:
    """按知识点语义相关性把内容过多的模块拆成多页。

    超过 MAX_POINTS_PER_PAGE 条的模块先由模型按语义聚类，相关的知识点放一起，
    每组 1~MAX_POINTS_PER_PAGE 条；模型失败或输出非法时回退为按数量机械切片。
    title 与 context 按索引同步切分，后续页面 / 音频 / 组装按新 ind 对齐。
    """
    result: list[CatalogModule] = []
    for module in modules or []:
        if not isinstance(module, dict):
            continue
        titles = list(module.get("title") or [])
        contexts = list(module.get("context") or [])
        summary = str(module.get("summary") or "").strip()
        if len(titles) <= MAX_POINTS_PER_PAGE:
            result.append(dict(module))
            continue

        groups = await _group_points_semantically(titles, summary)
        for part, indexes in enumerate(groups):
            if part == 0:
                part_summary = summary
            else:
                part_summary = f"{summary}（续{part}）" if summary else f"本页要点（续{part}）"
            split_module = dict(module)
            split_module.update(
                {
                    "ind": 0,  # 下方统一重排，避免重复编号
                    "title": [titles[i] for i in indexes],
                    "context": [contexts[i] for i in indexes if i < len(contexts)] if contexts else [],
                    "summary": part_summary,
                }
            )
            # 拆出的新分片需要重新判定讲稿是否就绪，不能继承旧模块的完成标记
            split_module.pop("_narrationReady", None)
            result.append(split_module)

    for ind, module in enumerate(result, 1):
        module["ind"] = ind
    return result


def _fallback_script(topic: str, title: str, summary: str) -> str:
    """讲稿生成失败时的兜底口播：用本页简介拼一句能直接朗读的话，保证不空音。"""
    focus = summary.strip().rstrip("。") or f"{title}是这一页要讲清楚的重点"
    return (
        f"接下来讲{title}。{focus}。把它放回「{topic}」这套课里，"
        "先弄清它要解决什么问题，再看具体怎么做，这一页就能听明白了。"
    )


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


async def _write_module_scripts(
    topic: str, plan: list[CatalogModule], index: int
) -> list[str]:
    """给一页的所有知识点补写口播讲稿，返回与 title 顺序一致的讲稿列表（可能少给，由调用方兜底）。"""
    module = plan[index]
    total = len(plan)

    if index == 0:
        prev_hint = "本模块是第 1 页，第 1 条讲稿负责整堂课的引入。"
    else:
        prev = plan[index - 1]
        prev_hint = (
            f"上一页（第 {prev['ind']} 页）讲的是：{'、'.join(prev['title'])}；"
            "本页第 1 条讲稿要用一句话接住它。"
        )
    next_hint = ""
    if index + 1 < total:
        nxt = plan[index + 1]
        next_hint = (
            f"\n下一页（第 {nxt['ind']} 页）会讲：{'、'.join(nxt['title'])}；本页不要提前讲掉。"
        )

    points = "\n".join(
        f"{pos}. {title}" for pos, title in enumerate(module["title"], 1)
    )
    user_prompt = (
        f"整套课件主题（必须逐字围绕）：{topic}\n"
        f"本模块是第 {module['ind']} 页，共 {total} 页。\n"
        f"本页简介：{module['summary']}\n"
        f"本页知识点（按顺序逐条写讲稿）：\n{points}\n"
        f"{prev_hint}{next_hint}\n\n"
        "请为上面每一个知识点各写一条口播讲稿。"
    )
    raw = await _invoke_text(MODULE_SCRIPT_PROMPT, user_prompt, temperature=0.5)
    scripts = [_clean_script(text) for text in _as_str_list(_parse_json(raw).get("context"))]
    return [script for script in scripts if script]


async def _fill_scripts(topic: str, plan: list[CatalogModule]) -> list[CatalogModule]:
    """按页并发补写讲稿：单页失败只影响这一页（用简介兜底），不影响整份大纲。"""
    results = await asyncio.gather(
        *(_write_module_scripts(topic, plan, index) for index in range(len(plan))),
        return_exceptions=True,
    )

    catalog: list[CatalogModule] = []
    for module, result in zip(plan, results):
        if isinstance(result, BaseException):
            logger.warning("第 %s 页讲稿生成失败，使用兜底文案：%s", module["ind"], result)
            result = []
        scripts = list(result or [])
        contexts = [
            scripts[pos] if pos < len(scripts) and scripts[pos]
            else _fallback_script(topic, title, module["summary"])
            for pos, title in enumerate(module["title"])
        ]
        catalog.append(
            {
                "ind": module["ind"],
                "title": list(module["title"]),
                "context": contexts,
                "summary": module["summary"],
            }
        )
    return catalog


async def _build_catalog(topic: str, docs: list[str], hint: str = "") -> list[CatalogModule]:
    """结合模型自身知识与 RAG 资料，按由浅入深的顺序生成大纲。

    分两步走：先规划模块骨架（页数、每页标题与简介），再按页并发补写口播讲稿。
    一次性吐出 8~10 页的完整讲稿容易超出单次输出上限被截断，骨架被截断会导致
    整份 JSON 解析失败、退化成单页课件；拆开之后页数再多也不会因为长度崩掉。
    """
    if docs:
        material = "\n\n".join(f"[资料{i}] {doc}" for i, doc in enumerate(docs, 1))
    else:
        material = "（本次没有检索到资料，请完全依赖你自己的知识。）"

    extra = f"\n\n用户对大纲的补充要求（必须融入新大纲）：\n{hint}" if hint else ""
    user_prompt = (
        f"讲解主题（必须逐字围绕，不可改写成子主题）：{topic}\n"
        f"本堂课标题就是：{topic}\n\n"
        f"可参考的资料：\n{material}"
        f"{extra}"
    )
    raw = await _invoke_text(OUTLINE_PLAN_PROMPT, user_prompt, temperature=0.4)
    # 先按单页容量拆页，再按页补写讲稿：每页知识点不超过 3 条，页面不会挤成一团
    plan = await _split_oversized_modules(_normalize_plan(_parse_json(raw)))
    if plan and len(plan) < 6:  # 页数明显没给够时留痕，便于排查模型偷懒
        logger.warning("大纲只规划出 %s 页，少于预期的 8~10 页：%s", len(plan), raw[:200])

    if not plan:  # 模型没吐出合法 JSON 时降级成单模块，避免整条链路断掉
        logger.warning("大纲解析失败，降级为单模块：%s", raw)
        return [
            {
                "ind": 1,
                "title": [topic],
                "context": [f"围绕「{topic}」的核心概念、基础流程与常见要点展开讲解。"],
                "summary": f"从核心概念出发，理解「{topic}」的基础流程与常见要点。",
            }
        ]

    return await _fill_scripts(topic, plan)


def _finish_html(fragment: str, theme: Optional[dict] = None) -> str:
    """把模型返回的 HTML 片段包成完整文档，主题样式以后置 CSS 覆盖默认样式。"""
    style = PAGE_BASE_STYLE + _theme_css(theme)
    return (
        '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="UTF-8">\n'
        f"<style>{style}</style>\n</head>\n<body>\n{fragment}\n</body>\n</html>"
    )


def _fallback_page(topic: str, module: CatalogModule, theme: Optional[dict] = None) -> str:
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
    return _finish_html(fragment, theme)


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


async def _is_page_topic_relevant(topic: str, module: CatalogModule) -> bool:
    """生成页面前用 LLM 校验本页主题：空内容或与整套课件主题无关时返回 False。

    整堂课主题缺失时无从判断相关性，放行以免误杀全部页面；
    LLM 调用失败、输出非法时一律按不通过处理，保证「主题没问题才生成」。
    """
    if not (topic or "").strip():
        return True

    titles = [
        str(title).strip()
        for title in (module.get("title") or [])
        if str(title).strip()
    ]
    summary = str(module.get("summary") or "").strip()
    points = _module_points(module).strip()
    if not titles and not summary and not points:
        return False

    user_prompt = (
        f"整套课件主题（判定基准）：{topic}\n"
        f"本页主题：{'、'.join(titles) if titles else '（空）'}\n"
        f"本页简介：{summary or '（空）'}\n"
        f"本页讲稿：\n{points or '（空）'}"
    )
    try:
        raw = await _invoke_text(PAGE_TOPIC_CHECK_PROMPT, user_prompt, temperature=0)
        flag = _parse_json(raw).get("relevant")
        if isinstance(flag, bool):
            return flag
        logger.warning(
            "第 %s 页主题校验未返回布尔值，按不通过处理：%s", module["ind"], raw[:200]
        )
    except Exception as exc:
        logger.warning("第 %s 页主题校验失败，按不通过处理：%s", module["ind"], exc)
    return False


async def _generate_new_module_text(
    topic: str, titles: list[str], user_summary: str
) -> tuple[str, str]:
    """为新主题生成一条口播讲稿和简介；模型失败时用兜底文案保证该页仍有旁白。"""
    course = (topic or "").strip() or "本次课程"
    user_prompt = (
        f"整套课件主题：{course}\n"
        f"本页主题：{'、'.join(titles) if titles else '（空）'}\n"
        f"用户填写的页面简介（必须融入）：{user_summary or '（未填写）'}"
    )
    script, summary = "", ""
    try:
        raw = await _invoke_text(NEW_MODULE_NARRATION_PROMPT, user_prompt, temperature=0.5)
        data = _parse_json(raw)
        script = _clean_script(" ".join(_as_str_list(data.get("context"))))[:400]
        summary = str(data.get("summary") or "").strip()[:120]
    except Exception as exc:
        logger.warning("新增页面讲稿生成失败，使用兜底文案：%s", exc)

    focus = "、".join(titles) if titles else "本页主题"
    if not script:
        hint = f"你特别提到要讲清楚：{user_summary}。" if user_summary else ""
        script = (
            f"接下来看{focus}。{hint}在「{course}」这套课里，{focus}是需要单独说清楚的一部分。"
            f"我们先把它放回整体中，看看它解决什么问题，再一步步讲清它的要点和常见用法，"
            "这样你就能把这一页和前后的内容接起来。"
        )
    if not summary:
        summary = user_summary or f"围绕{'、'.join(titles) or topic}展开讲解，理解本页重点。"
    return script, summary


async def _ensure_new_module_content(topic: str, catalog: list) -> Optional[list]:
    """确认页新增的页面若通过主题校验但没有讲稿，补写讲解稿与简介。

    写回 catalog 后 page_agent / audio_agent 并发派发时都能看到同一份数据；
    _narrationReady 与 topicRelevant 是内部标记，避免主管每轮重复调用模型。
    无需处理时返回 None，避免无意义写回 state。
    """
    updated: list = []
    changed = False
    notified = False
    for module in catalog or []:
        if (
            not isinstance(module, dict)
            or not module.get("isNew")
            or module.get("_narrationReady")
        ):
            updated.append(module)
            continue

        if any(str(item).strip() for item in (module.get("context") or [])):
            next_module = dict(module)
            next_module["_narrationReady"] = True
            updated.append(next_module)
            changed = True
            continue

        if not notified:
            _emit("outline", "start", message="正在补充新增页面的讲解与简介…")
            notified = True

        next_module = dict(module)
        relevant = module.get("topicRelevant")
        if not isinstance(relevant, bool):
            relevant = await _is_page_topic_relevant(topic, module)
        next_module["topicRelevant"] = relevant
        if relevant:
            titles = [
                str(title).strip()
                for title in (module.get("title") or [])
                if str(title).strip()
            ]
            user_summary = str(module.get("summary") or "").strip()
            script, summary = await _generate_new_module_text(topic, titles, user_summary)
            next_module["context"] = [script]
            next_module["summary"] = summary
        next_module["_narrationReady"] = True
        updated.append(next_module)
        changed = True

    return updated if changed else None


async def _build_page(
    topic: str,
    module: CatalogModule,
    total: int,
    on_error: Optional[Callable[[Exception], None]] = None,
    theme: Optional[dict] = None,
) -> str:
    """根据一个模块的大纲（口播讲稿）生成对应 HTML 页面，失败时回退到兜底模板。

    出口统一注入自适配脚本：任一路径（模型页 / 片段包装 / 兜底页）产出的
    页面在窄容器里溢出时都会自动缩回一屏内，不再出现滚动条和内容裁切。
    on_error：页面降级到兜底模板时的回调（供 page_agent 上报失败清单），默认不通知。
    theme：用户所选 PPT 样式；空值时沿用默认暖色简约风。"""
    return _inject_fit(await _build_page_raw(topic, module, total, on_error, theme))


async def _build_page_raw(
    topic: str,
    module: CatalogModule,
    total: int,
    on_error: Optional[Callable[[Exception], None]] = None,
    theme: Optional[dict] = None,
) -> str:
    """生成页面本体（不注入脚本），失败时回退到兜底模板。"""
    point_count = len(module.get("title") or [])
    user_prompt = (
        f"整套课件主题（页面顶部课程名必须使用原文，不可改写或缩窄）：{topic}\n"
        f"页面主标题写本页知识点，副标题点明它在整套课件中的位置；课程名始终以「整套课件主题」为准。\n"
        f"本页序号：第 {module['ind']} 页，共 {total} 页\n"
        f"本页口播讲稿（供你理解内容，不是页面文案，页面文字要从中提炼）：\n{_module_points(module)}\n\n"
        f"本页共 {point_count} 个知识点，页面信息条必须一条不差地对应这 {point_count} 条，"
        "不得自行增加、删减或合并；即使所选视觉规范描述了其他条数，也以本页实际知识点数为准。\n\n"
        "请据此生成这一页的 HTML：主标题概括本页核心知识点，副标题点明本页在整套课件中的位置；"
        "卡片和横幅里的文字都要压缩成短语，不要照抄讲稿里的原句。"
    )
    if isinstance(theme, dict) and theme.get("stylePrompt"):
        colors = theme.get("previewColors") or {}
        user_prompt += (
            f"\n本次课件视觉规范：{theme['stylePrompt']}"
            f"\n关键配色：背景 {colors.get('background', '')}、主色 {colors.get('primary', '')}、"
            f"强调色 {colors.get('accent', '')}、正文 {colors.get('text', '')}。"
        )
    visual_style = _visual_style(theme)
    page_prompt = PAGE_PROMPT_TEMPLATE.format(visual_style=visual_style)
    try:
        raw = await _invoke_text(page_prompt, user_prompt, temperature=0.5)
    except Exception as exc:
        logger.warning("第 %s 页生成失败，使用兜底模板：%s", module["ind"], exc)
        if on_error is not None:
            on_error(exc)
        return _fallback_page(topic, module, theme)

    page = _clean_html(raw)
    if "<html" in page.lower():
        return page
    if page.startswith("<"):  # 模型只给了片段，补成完整文档
        return _finish_html(page, theme)
    logger.warning("第 %s 页输出不像 HTML，使用兜底模板：%s", module["ind"], raw[:200])
    if on_error is not None:
        on_error(RuntimeError("模型输出不是可用的 HTML 页面"))
    return _fallback_page(topic, module, theme)

# 旧 text_organization_node 已拆为 agents/query_rewrite_agent.py、agents/research_agent.py、
# agents/outline_agent.py、agents/page_agent.py

# 旧 audio_node 已迁往 agents/audio_agent.py；
# 旧 html_audio_assemble 已迁往 agents/assemble_agent.py（MP4 注释块原样保留）。