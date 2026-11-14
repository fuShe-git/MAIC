"""对话接口：前端提交一条消息，后端启动 LangGraph 图并返回结果。"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.agent.graph import run_classroom

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
        # 用户的输入即 SimpleState.userIssue，图跑完后返回最终状态
        state = await run_classroom(payload.message)
    except Exception as exc:  # 模型/网络异常统一转成 502，避免前端收到 500 无提示
        logger.exception("课堂图执行失败")
        raise HTTPException(status_code=502, detail=f"模型调用失败：{exc}") from exc

    # 闲聊：图在 content_analysis 后就结束，reply 是「你好，我是 MAIC 助手…」这类回复
    if not state.get("needLecture"):
        return ChatResponse(reply=state.get("reply") or "你好，我是 MAIC 助手，有什么可以帮你的？")

    # 需要讲解：后续节点（大纲 / 音频 / 页面）还没实现，先回显识别出的主题
    topic = state.get("topic") or payload.message
    return ChatResponse(reply=f"已识别讲解主题：{topic}（大纲与课件生成开发中）")
