"""大模型调用封装：把原先「导入即执行」的 demo 改造成接口层可复用的函数。

改造要点：
1. 模型客户端改为懒加载（get_llm），模块被 import 时不会真正建立连接；
2. 提供异步函数 chat()，供 FastAPI 的 async 路由直接 await，不阻塞事件循环；
3. 保留 `python agent.py` 的直接运行入口，方便脱离服务单独调试；
4. 通过 ContextVar 保存「本次请求选中的模型配置」，让 get_llm 按请求切换，
   同时不把 modelId 逐层塞进图状态和各 Agent 的函数签名。

专业名词解释：ContextVar（上下文变量）是 asyncio 下的「请求级全局槽位」，
每个协程 / 任务各有一份副本；asyncio.create_task 创建子任务时会复制当前上下文，
因此并发请求之间互不影响。
"""

import asyncio
import logging
import os
import re
from contextvars import ContextVar, Token
from functools import lru_cache
from typing import Optional

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from app.storage import llm_config_db

load_dotenv()

logger = logging.getLogger(__name__)

# 当前请求选中的模型配置；None 表示使用 .env 里的系统默认模型
_LLM_CONFIG: ContextVar[Optional[dict]] = ContextVar("maic_llm_config", default=None)

# 前端「系统默认」选项的哨兵值，与 storage 层保持一致
_PLATFORM_DEFAULT_ID = llm_config_db.PLATFORM_DEFAULT_ID

# 本次请求的输出语言（zh / en），与各接口的用户输入绑定
_OUTPUT_LANG: ContextVar[str] = ContextVar("maic_output_lang", default="zh")

_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def detect_output_lang(text: str) -> str:
    """按用户输入识别输出语言：含汉字判中文，否则出现英文字母判英文，默认中文。"""
    sample = str(text or "")[:2000]
    if _CJK_RE.search(sample):
        return "zh"
    if _LATIN_RE.search(sample):
        return "en"
    return "zh"


def set_output_lang(lang: str) -> Token:
    """在接口层设置本次请求的输出语言，返回用于复原的 Token。"""
    return _OUTPUT_LANG.set("en" if lang == "en" else "zh")


def reset_output_lang(token: Optional[Token]) -> None:
    """复原输出语言上下文；跨任务 reset 会抛 ValueError，直接忽略。"""
    if token is None:
        return
    try:
        _OUTPUT_LANG.reset(token)
    except ValueError:
        logger.debug("输出语言上下文已不存在，忽略复原", exc_info=True)


def get_output_lang() -> str:
    """当前请求的目标输出语言。"""
    return _OUTPUT_LANG.get()


def set_llm_config(config: Optional[dict]) -> Token:
    """在接口层设置本次请求的模型配置，返回用于复原的 Token。"""
    return _LLM_CONFIG.set(config)


def reset_llm_config(token: Optional[Token]) -> None:
    """复原上下文；跨任务 reset 会抛 ValueError，这里直接忽略。"""
    if token is None:
        return
    try:
        _LLM_CONFIG.reset(token)
    except ValueError:
        logger.debug("模型配置上下文已不存在，忽略复原", exc_info=True)


def get_llm_config() -> Optional[dict]:
    """当前上下文里的模型配置（调试 / 日志用）。"""
    return _LLM_CONFIG.get()


async def resolve_llm_config(model_id: Optional[str]) -> Optional[dict]:
    """按前端传来的「配置 id:模型 id」解析运行参数；未命中回退系统默认。

    SQLite 是同步 IO，这里用 to_thread 包一层，避免阻塞事件循环。
    """
    raw = (model_id or "").strip()
    if not raw or raw == _PLATFORM_DEFAULT_ID:
        return None
    config = await asyncio.to_thread(llm_config_db.get_runtime_model, raw)
    if config is None:
        # 配置可能已被删除：只记 id，不打印密钥，生成流程继续走系统默认
        logger.warning("未找到所选模型配置，回退系统默认：%s", raw)
    return config


@lru_cache(maxsize=32)
def _build_llm(model: str, api_key: str, base_url: str, timeout: float) -> ChatOpenAI:
    """按「模型 + 接口地址 + 密钥 + 超时」缓存客户端，避免每轮生成重复建连。

    缓存键包含全部连接参数：同一配置复用客户端，不同配置互不串用。
    """
    return ChatOpenAI(
        model=model,
        api_key=api_key or None,
        base_url=base_url or None,
        timeout=timeout,
    )


def get_llm() -> ChatOpenAI:
    """优先使用当前请求选中的模型；未选择时回退 .env 的系统默认模型。"""
    config = _LLM_CONFIG.get()
    if config:
        return _build_llm(
            str(config.get("model") or ""),
            str(config.get("api_key") or ""),
            str(config.get("base_url") or ""),
            float(config.get("timeout") or 60.0),
        )
    return _build_llm(
        os.getenv("MODEL_NAME", ""),
        os.getenv("API_KEY") or "",
        os.getenv("API_BASE_URL") or "",
        # 大模型首包可能较慢，超时从 10 秒放宽到 60 秒
        60.0,
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
