"""对话接口：前端提交一条消息，后端启动 LangGraph 图并返回结果。

- POST /chat：一次性接口，等图全部跑完再返回（老接口，保留兜底）。
- POST /chat/stream：SSE 流式接口，跑图过程中逐步推送进度事件，最后推完整结果。
"""

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent.graph import run_classroom
from app.agent.nodes import set_progress_emitter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    """前端请求体。"""

    message: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="用户输入的内容",
    )


class CatalogItem(BaseModel):
    """大纲里的一个模块，对应一次即将进行的讲解。"""

    ind: int = Field(..., description="模块序号，从 1 开始")
    title: list[str] = Field(default_factory=list, description="该模块内各知识点的标题")
    context: list[str] = Field(
        default_factory=list,
        description=(
            "与 title 一一对应的口播讲稿，可直接朗读（第 i 个 context 讲的就是第 i 个 title），"
            "页面文字由讲稿提炼而来，二者不重复"
        ),
    )


class PageItem(BaseModel):
    """一个模块对应的页面，即「本部分大纲 + 渲染用 HTML」的组合。"""

    ind: int = Field(..., description="模块序号，与 catalogList 中同序号模块的 ind 一致")
    context: str = Field("", description="本部分的大纲文本，即标题 + 口播讲稿")
    htmlpage: str = Field("", description="本部分对应的页面，HTML 代码")


class ChatResponse(BaseModel):
    """返回给前端的结构。"""

    reply: str = Field(..., description="大模型回复的文本")
    needLecture: bool = Field(False, description="是否需要讲解；False 时下面是空的")
    topic: str = Field("", description="识别出的讲解主题")
    catalogList: list[CatalogItem] = Field(
        default_factory=list,
        description="讲解大纲，已按由浅入深的顺序排好",
    )
    pageList: list[PageItem] = Field(
        default_factory=list,
        description="讲解页面，与 catalogList 按 ind 一一对应",
    )
    courseHtml: str = Field(
        "",
        description="组装后的自包含课件 HTML 落盘路径（节点4产出）；未生成时为空串",
    )
    videoUrl: str = Field(
        "",
        description="导出的 MP4 落盘路径（节点4产出）；未生成或导出失败时为空串",
    )


@router.post("/chat", response_model=ChatResponse, summary="与大模型对话")
async def chat_with_model(payload: ChatRequest) -> ChatResponse:
    try:
        # 用户的输入即 SimpleState.userIssue，图跑完后返回最终状态
        state = await run_classroom(payload.message)
    except Exception as exc:  # 模型/网络异常统一转成 502，避免前端收到 500 无提示
        logger.exception("课堂图执行失败")
        raise HTTPException(status_code=502, detail=f"模型调用失败：{exc}") from exc

    # 闲聊：图在 content_analysis 后就结束，reply 是「你好，我是 MAIC 助手…」这类回复
    if not state.get("needLecture"):
        return ChatResponse(
            reply=state.get("reply") or "你好，我是 MAIC 助手，有什么可以帮你的？",
            needLecture=False,
        )

    # 需要讲解：主题 + 大纲由 text_organization 产出
    topic = state.get("topic") or payload.message
    catalog_list = [
        CatalogItem(
            ind=int(item.get("ind") or index),
            title=list(item.get("title") or []),
            context=list(item.get("context") or []),
        )
        for index, item in enumerate(state.get("catalogList") or [], start=1)
    ]

    # 页面由 text_organization 产出：每个模块一段大纲 + 一段 HTML
    page_list = [
        PageItem(
            ind=int(page.get("ind") or index),
            context=str(page.get("context") or ""),
            htmlpage=str(page.get("htmlpage") or ""),
        )
        for index, page in enumerate(state.get("pageList") or [], start=1)
    ]

    return ChatResponse(
        reply=f"已识别讲解主题：{topic}，共 {len(catalog_list)} 个模块",
        needLecture=True,
        topic=topic,
        catalogList=catalog_list,
        pageList=page_list,
        courseHtml=str(state.get("courseHtml") or ""),
        videoUrl=str(state.get("videoUrl") or ""),
    )


def _sse(event: dict) -> str:
    """把一条进度事件序列化成 SSE 帧（data: {json}\\n\\n）。"""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _build_response(state: dict, message: str) -> dict:
    """把图的最终状态转成 ChatResponse 结构（与 POST /chat 的组装逻辑一致）。"""
    if not state.get("needLecture"):
        return ChatResponse(
            reply=state.get("reply") or "你好，我是 MAIC 助手，有什么可以帮你的？",
            needLecture=False,
        ).model_dump()

    topic = state.get("topic") or message
    catalog_list = [
        CatalogItem(
            ind=int(item.get("ind") or index),
            title=list(item.get("title") or []),
            context=list(item.get("context") or []),
        )
        for index, item in enumerate(state.get("catalogList") or [], start=1)
    ]
    page_list = [
        PageItem(
            ind=int(page.get("ind") or index),
            context=str(page.get("context") or ""),
            htmlpage=str(page.get("htmlpage") or ""),
        )
        for index, page in enumerate(state.get("pageList") or [], start=1)
    ]
    return ChatResponse(
        reply=f"已识别讲解主题：{topic}，共 {len(catalog_list)} 个模块",
        needLecture=True,
        topic=topic,
        catalogList=catalog_list,
        pageList=page_list,
        courseHtml=str(state.get("courseHtml") or ""),
        videoUrl=str(state.get("videoUrl") or ""),
    ).model_dump()


@router.post("/chat/stream", summary="流式生成课件（SSE 进度推送）")
async def chat_with_model_stream(payload: ChatRequest) -> StreamingResponse:
    """跑同一套课堂图，边跑边按 SSE 格式推送进度，最后推完整结果。

    事件类型：check | outline | pages | audio | assemble（过程进度），
    reply（闲聊完成）| done（课件完成，data 为 ChatResponse）| error（retrying/failed）。
    内置重试 1 次：第一次失败推 retrying 并重跑，第二次失败推 failed 后结束流。
    """

    async def event_generator():
        # 节点进度先进队列，生成器边跑边吐，实现「实时」推送
        queue: asyncio.Queue[str] = asyncio.Queue()
        set_progress_emitter(lambda event: queue.put_nowait(_sse(event)))
        try:
            state = None
            error_frame: str | None = None

            for attempt in (1, 2):
                task = asyncio.create_task(run_classroom(payload.message))
                # 图在后台跑，这里循环把队列里的进度帧吐给前端
                while not task.done():
                    if queue.empty():
                        await asyncio.sleep(0.15)  # 轮询让出事件循环，进度延迟 ≤150ms
                        continue
                    yield queue.get_nowait()
                # 任务结束后把剩余进度帧排空
                while not queue.empty():
                    yield queue.get_nowait()

                try:
                    state = task.result()
                    error_frame = None
                    break
                except Exception as exc:
                    logger.exception("课堂图执行失败（第 %s 次）", attempt)
                    if attempt == 1:
                        yield _sse(
                            {
                                "step": "error",
                                "status": "retrying",
                                "message": f"生成出错：{exc}，重试中…",
                            }
                        )
                    else:
                        error_frame = _sse(
                            {
                                "step": "error",
                                "status": "failed",
                                "message": f"生成出错：{exc}",
                            }
                        )
                        break

            if error_frame is not None:  # 两次都失败：推最终错误后结束流，前端停留等待页
                yield error_frame
                return
            if state is None:  # 理论上到不了，兜底直接结束
                return

            # 终态事件：闲聊推 reply，需要讲解推 done（带完整 ChatResponse）
            if not state.get("needLecture"):
                reply = state.get("reply") or "你好，我是 MAIC 助手，有什么可以帮你的？"
                yield _sse({"step": "reply", "status": "done", "message": reply})
            else:
                yield _sse(
                    {
                        "step": "done",
                        "status": "done",
                        "data": _build_response(state, payload.message),
                    }
                )
        finally:
            # 复位回调：老接口 /chat 不设回调，行为不受影响；前端断开也在这里兜底
            set_progress_emitter(None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 反向代理（nginx 等）默认会缓冲响应，禁用后进度才能实时透传
            "X-Accel-Buffering": "no",
        },
    )
