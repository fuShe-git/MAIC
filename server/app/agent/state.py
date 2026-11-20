"""图状态定义（v4）：既有字段保持 LastValue（覆盖写）不动，新增字段全部声明 reducer。

专业名词解释：
- reducer（合并器）：LangGraph 中定义「多个节点并发更新同一字段时如何合并」的函数；
  不声明时默认 LastValue（后写覆盖先写）。并行分支（Send 扇出）同时写一个字段时，
  裸 list/dict 会互相覆盖丢数据，所以并发可见字段必须声明 reducer。
- 清除标记：reducer 合并语义下「写空列表」不会清空旧值，因此约定
  CLEAR_MARKER（字符串 / 字典两种形态）表示「重置该字段」，供主管级联清空下游过期产物。
"""

from typing import Annotated, Optional, TypedDict

# 清除标记：任何 reducer 看到它就重置已有内容（R4 下游级联失效要用）
CLEAR_MARKER = "__clear__"


# ── 模块类型（与旧 nodes.py 定义一致，nodes.py 会 re-export 兼容旧导入）──────
class CatalogModule(TypedDict):
    """大纲里的一个模块，对应一个即将讲解的页面。

    title 与 context 一一对应（第 i 个 title 的讲稿就是第 i 个 context），
    因此二者长度必须相等。context 是能直接朗读的口播讲稿，不是页面文案。
    """

    ind: int            # 序号
    title: list[str]    # 该页面内各知识点的标题
    context: list[str]  # 与 title 一一对应的口播讲稿（90~160 字，口语连贯）


class AudioModule(TypedDict):
    """一个模块对应的音频数据，ind 与 CatalogModule.ind 对应。"""

    ind: int        # 序号，与 catalogList 中对应模块的 ind 一致
    context: str    # 清洗后的口播台词（多个知识点讲稿按顺序拼接）
    audio_path: str  # mp3 落盘绝对路径；空串表示该页没有旁白
    duration: float  # 音频时长（秒），决定该页在视频里的停留时长


class PageModule(TypedDict):
    """一个模块对应的页面数据，即「大纲 + 页面」的组合。"""

    ind: int       # 序号，与前面对应
    context: str   # 本部分大纲，即标题 + 口播讲稿
    htmlpage: str  # 本部分对应的页面，HTML 代码


# ── reducer（合并器）─────────────────────────────────────────────────────────
def _merge_dict(
    left: Optional[dict] = None, right: Optional[dict] = None
) -> dict:
    """字典合并：right 覆盖 left 的同名键；值为 None 表示删除该键（重置阶段标记）。

    dispatchCounts 与 stageDone 共用：dispatchCounts 由主管唯一写入（写新总数），
    stageDone 支持主管把过期阶段标记删掉（级联失效后强制重走）。
    """
    merged = dict(left or {})
    for key, value in (right or {}).items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


def _merge_unique(
    left: Optional[list] = None, right: Optional[list] = None
) -> list:
    """有序去重合并；出现清除标记则整体重置。queries / docs 用。"""
    result: list = []
    for item in list(left or []) + list(right or []):
        if item == CLEAR_MARKER or (
            isinstance(item, dict) and item.get(CLEAR_MARKER)
        ):
            result = []
            continue
        if item not in result:
            result.append(item)
    return result


def _merge_extend(
    left: Optional[list] = None, right: Optional[list] = None
) -> list:
    """顺序追加合并；出现清除标记则整体重置。conflicts / quizList 用。"""
    result: list = list(left or [])
    for item in list(right or []):
        if item == CLEAR_MARKER or (
            isinstance(item, dict) and item.get(CLEAR_MARKER)
        ):
            result = []
            continue
        result.append(item)
    return result


def _merge_failed(
    left: Optional[list] = None, right: Optional[list] = None
) -> list:
    """失败清单合并：按 (ind, stage) 去重，后报覆盖先报；出现清除标记则整体重置。"""
    result: list = [dict(item) for item in (left or []) if isinstance(item, dict)]
    for item in list(right or []):
        if isinstance(item, dict) and item.get(CLEAR_MARKER):
            result = []
            continue
        if not isinstance(item, dict):
            continue
        key = (item.get("ind"), item.get("stage"))
        result = [
            row for row in result if (row.get("ind"), row.get("stage")) != key
        ]
        result.append(dict(item))
    return result


# ── 图状态 ──────────────────────────────────────────────────────────────────
class SimpleState(TypedDict, total=False):
    # ── 既有字段（LastValue，不动）──
    userIssue: str           # 用户问题
    needLecture: bool        # 是否需要讲解；False 时图直接结束
    reply: str               # 不需要讲解时返回给用户的简单回复
    topic: str               # 从用户问题里归纳出的讲解主题
    catalogList: list[CatalogModule]  # 大纲，单一写入方：outline_agent / 主管
    audioList: list[AudioModule]      # 台词音频，单一写入方：audio_agent
    pageList: list[PageModule]        # 页面，单一写入方：page_agent
    courseHtml: str          # 组装后的自包含课件 HTML 落盘路径
    videoUrl: str            # 导出的 MP4 落盘路径（当前恒为空串）

    # ── v4 新增：并发可见字段全部带 reducer ──
    dispatchCounts: Annotated[dict[str, int], _merge_dict]  # "stage:agent" → 已派发次数
    stageDone: Annotated[dict[str, bool], _merge_dict]      # 阶段名 → 是否完成（None 重置）
    failedPages: Annotated[list[dict], _merge_failed]       # [{ind, stage, error}]
    queries: Annotated[list[str], _merge_unique]            # 改写后的检索查询
    docs: Annotated[list[str], _merge_unique]               # RAG 召回资料（合并去重）
    conflicts: Annotated[list[dict], _merge_extend]         # 事实校验冲突 [{ind, reason}]
    quizList: Annotated[list[dict], _merge_extend]          # 课后练习题

    # ── v4 新增：标量字段（LastValue）──
    history: list[dict]      # 多轮会话历史 [{role, content}]（20 条裁剪）
    sessionId: str           # 会话 id，多轮与快照的键
    intent: str              # new_lecture | continue | revise_page | quiz_only | chat
    reviseInds: list[int]    # revise_page 意图下要定向改写的模块 ind
    courseId: str            # quiz_only 追加的历史课件 id
    outlineConfirmed: bool   # 大纲是否已经过人工确认
    autoConfirm: bool        # True 跳过人工确认（默认，旧调用零破坏）
    failMessage: str         # 非空表示生成失败原因（主管短路 END 前写入）
    threadId: str            # LangGraph thread_id（检查点/恢复执行的键）
    outputUnsafe: bool       # safety_output 判定课件文本违规
    dispatch: list[str]      # 主管本轮决策：要派发的子 agent 名（路由转 Send 用）
    snapshot: dict           # 多轮/重试用的历史产物快照
