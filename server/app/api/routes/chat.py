"""对话接口：前端提交一条消息，后端调用大模型返回回复。"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.agent.agent import chat

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


class ChatResponse(BaseModel):
    """返回给前端的结构。"""

    reply: str = Field(..., description="大模型回复的文本")


@router.post("/chat", response_model=ChatResponse, summary="与大模型对话")
async def chat_with_model(payload: ChatRequest) -> ChatResponse:
    try:
        reply = await chat(payload.message)
    except Exception as exc:  # 模型/网络异常统一转成 502，避免前端收到 500 无提示
        logger.exception("调用模型失败")
        raise HTTPException(status_code=502, detail=f"模型调用失败：{exc}") from exc
    return ChatResponse(reply=reply)
