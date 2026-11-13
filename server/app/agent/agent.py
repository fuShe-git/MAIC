"""大模型调用封装：把原先「导入即执行」的 demo 改造成接口层可复用的函数。

改造要点：
1. 模型客户端改为懒加载（get_llm），模块被 import 时不会真正建立连接；
2. 提供异步函数 chat()，供 FastAPI 的 async 路由直接 await，不阻塞事件循环；
3. 保留 `python agent.py` 的直接运行入口，方便脱离服务单独调试。
"""

import asyncio
import os
from functools import lru_cache

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

load_dotenv()


@lru_cache
def get_llm() -> ChatOpenAI:
    """懒加载并缓存模型客户端，避免每次请求都重新创建。"""
    return ChatOpenAI(
        model=os.getenv("MODEL_NAME", ""),
        api_key=os.getenv("API_KEY"),
        base_url=os.getenv("API_BASE_URL"),
        # 大模型首包可能较慢，超时从 10 秒放宽到 60 秒
        timeout=60.0,
    )


async def chat(prompt: str) -> str:
    """调用大模型，返回纯文本回复。"""
    result = await get_llm().ainvoke([HumanMessage(content=prompt)])
    content = result.content
    if isinstance(content, str):
        return content
    # 部分模型返回内容可能是分段列表，统一转成字符串兜底
    return str(content)


if __name__ == "__main__":
    print(asyncio.run(chat("你好")))
