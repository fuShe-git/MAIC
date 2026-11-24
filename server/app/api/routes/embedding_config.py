"""Embedding 配置接口：全局唯一配置的读取、保存与连通性测试。

- GET  /embedding-config        读取配置（密钥脱敏；未保存时返回平台默认值）
- PUT  /embedding-config        新增 / 覆盖唯一配置
- POST /embedding-config/test   用当前表单参数测试 OpenAI 兼容向量接口

保存成功后会清掉 embedding 客户端缓存，后续检索立即使用新配置。
"""

import asyncio
import logging
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.storage import embedding_db
from app.tools import milvus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/embedding-config", tags=["embedding-config"])

_MODEL_MAX = 120
_DIMENSION_MIN = 1
_DIMENSION_MAX = 4096
_TIMEOUT_MAX = 3600


class EmbeddingConfigPayload(BaseModel):
    """字段名与前端 camelCase 保持一致；apiKey 留空表示保留原密钥。"""

    baseUrl: str = ""
    model: str = ""
    dimensions: int = embedding_db.DEFAULT_DIMENSIONS
    timeout: int = embedding_db.DEFAULT_TIMEOUT
    apiKey: str = ""


def _clean_payload(payload: EmbeddingConfigPayload) -> dict:
    """校验请求体；不合法直接 400，把可读原因返回前端。"""
    base_url = (payload.baseUrl or "").strip().rstrip("/")
    parsed = urlparse(base_url)
    if not base_url or parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(
            status_code=400,
            detail="服务地址需为合法地址，例如 https://api.openai.com/v1",
        )

    model = (payload.model or "").strip()[:_MODEL_MAX]
    if not model:
        raise HTTPException(status_code=400, detail="向量模型名称不能为空")

    dimensions = int(payload.dimensions or 0)
    if not _DIMENSION_MIN <= dimensions <= _DIMENSION_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"向量维度需为 {_DIMENSION_MIN}-{_DIMENSION_MAX} 之间的整数",
        )

    timeout = int(payload.timeout or 0)
    if timeout <= 0 or timeout > _TIMEOUT_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"请求超时需为 1-{_TIMEOUT_MAX} 秒之间的整数",
        )

    return {
        "base_url": base_url,
        "model": model,
        "dimensions": dimensions,
        "timeout": timeout,
        "api_key": (payload.apiKey or "").strip(),
    }


@router.get("")
async def get_config():
    """读取配置；密钥脱敏。"""
    return await asyncio.to_thread(embedding_db.get_config)


@router.put("")
async def save_config(payload: EmbeddingConfigPayload):
    """新增 / 覆盖唯一配置，并让 embedding 客户端重新初始化。"""
    data = _clean_payload(payload)
    saved = await asyncio.to_thread(
        embedding_db.save_config,
        data["base_url"],
        data["model"],
        data["dimensions"],
        data["timeout"],
        data["api_key"],
    )
    if not saved:
        raise HTTPException(status_code=500, detail="向量化配置保存失败")
    milvus.get_embeddings.cache_clear()
    return saved


@router.post("/test")
async def test_connection(payload: EmbeddingConfigPayload):
    """按表单参数请求一次 /embeddings，验证地址、模型名、维度与密钥。"""
    data = _clean_payload(payload)
    api_key = data["api_key"]
    if not api_key or api_key.startswith(embedding_db.MASK_PREFIX):
        runtime = await asyncio.to_thread(embedding_db.get_runtime_config)
        api_key = runtime["api_key"]
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="请填写 API Key，或在 server/.env 中配置 VECTOR_API_KEY",
        )

    url = f"{data['base_url']}/embeddings"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    timeout = httpx.Timeout(float(data["timeout"]), connect=min(10.0, float(data["timeout"])))
    body = {
        "model": data["model"],
        "input": "MAIC embedding connection test",
        "dimensions": data["dimensions"],
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=headers, json=body)
            # 部分兼容服务不接受 dimensions，报错时去掉该参数再试一次
            if response.status_code >= 400 and "dimension" in response.text.lower():
                response = await client.post(
                    url,
                    headers=headers,
                    json={"model": data["model"], "input": body["input"]},
                )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=400, detail=f"连接失败：{exc}") from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=400,
            detail=f"连接失败（HTTP {response.status_code}）：{response.text[:200]}",
        )

    try:
        result = response.json()
        vector = ((result.get("data") or [{}])[0].get("embedding")) or []
    except (ValueError, AttributeError, IndexError, TypeError):
        raise HTTPException(status_code=400, detail="连接失败：服务返回内容格式不正确")

    dimensions = len(vector)
    message = "连接成功"
    if dimensions and dimensions != data["dimensions"]:
        message = f"连接成功，但服务返回 {dimensions} 维向量，与填写的 {data['dimensions']} 维不一致"
    return {"ok": True, "dimensions": dimensions, "message": message}
