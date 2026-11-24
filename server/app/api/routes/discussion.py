"""课件讨论接口：SSE（Server-Sent Events，服务端单向推流）流式问答。

事件类型：meta（来源状态）/ delta（回答片段）/ sources（引用来源）/ done / error。
讨论历史只在前端本次打开期间维护，后端不落库。
"""

import asyncio
import json
import logging
from typing import Literal, Optional
from uuid import uuid4

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agent.agent import reset_llm_config, resolve_llm_config, set_llm_config
from app.agent.discussion import stream_answer

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/discussion", tags=["discussion"])


class DiscussionPage(BaseModel):
    """首次提问携带的单页课件正文（不含 HTML，避免大 payload）。"""

    ind: int
    context: str


class DiscussionHistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class DiscussionRequest(BaseModel):
    message: str
    courseId: str = ""
    topic: str = ""
    pageNum: str = ""
    firstQuestion: bool = True
    coursePages: Optional[list[DiscussionPage]] = None
    history: list[DiscussionHistoryItem] = []
    modelId: Optional[str] = None  # 所选模型「配置id:模型id」；空 / platform-default 用系统默认


def _sse(event: dict) -> str:
    """按项目既有格式编码 SSE 帧。"""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/stream")
async def discussion_stream(payload: DiscussionRequest):
    """流式讨论：优先课件，必要时联网；失败以 error 事件返回，不抛 500。"""
    request_id = uuid4().hex[:8]

    if not payload.message.strip():
        async def _bad():
            yield _sse({"type": "error", "message": "请输入讨论问题"})

        return StreamingResponse(_bad(), media_type="text/event-stream")

    async def _event_stream():
        # 课件内提问同样使用用户在首页选中的模型
        model_config = await resolve_llm_config(payload.modelId)
        token = set_llm_config(model_config)
        data = payload.model_dump()
        data.pop("modelId", None)
        try:
            async for event in stream_answer(data, request_id):
                yield _sse(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # 日志只记错误类型，不打印问题全文或课件正文
            logger.warning("讨论失败 rid=%s（%s）", request_id, type(exc).__name__)
            yield _sse({"type": "error", "message": "讨论服务异常，请稍后重试"})
        finally:
            reset_llm_config(token)

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
