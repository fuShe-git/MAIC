"""大模型配置接口：服务商 + 模型列表的持久化 CRUD。

- GET    /llm-configs        列出全部配置（密钥脱敏）；
- POST   /llm-configs        新建配置；
- PUT    /llm-configs/{id}   更新配置（apiKey 留空表示保留原密钥）；
- DELETE /llm-configs/{id}   删除配置及其模型。

SQLite 同步 IO 全部用 to_thread 包裹，异常由存储层兜底，接口不抛 500。
"""

import asyncio
import logging
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.storage import llm_config_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/llm-configs", tags=["llm-configs"])

# 与服务商 / 模型输入框的合理长度上限对齐，避免异常超长内容落库
_NAME_MAX = 60
_MODEL_NAME_MAX = 120
_TIMEOUT_MAX = 3600


class LlmModelItem(BaseModel):
    """单个模型：id 由前端生成，name 是调用接口时真正使用的模型名。"""

    id: str = ""
    name: str = ""


class LlmConfigPayload(BaseModel):
    """服务商配置请求体；字段名与前端 camelCase 保持一致。"""

    name: str = ""
    timeout: int = llm_config_db.DEFAULT_TIMEOUT
    baseUrl: str = ""
    apiKey: str = ""
    models: list[LlmModelItem] = []


def _clean_payload(payload: LlmConfigPayload) -> dict:
    """校验并清洗请求体；不合法直接 400，把可读原因返回给前端。"""
    name = (payload.name or "").strip()[:_NAME_MAX]
    if not name:
        raise HTTPException(status_code=400, detail="请填写供应商名")

    base_url = (payload.baseUrl or "").strip()
    parsed = urlparse(base_url)
    if not base_url or parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(
            status_code=400,
            detail="Base URL 需为合法地址，例如 https://api.openai.com/v1",
        )

    timeout = int(payload.timeout or 0)
    if timeout <= 0 or timeout > _TIMEOUT_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"请求超时需为 1-{_TIMEOUT_MAX} 秒之间的整数",
        )

    models = [
        {
            "id": (item.id or "").strip(),
            "name": (item.name or "").strip()[:_MODEL_NAME_MAX],
        }
        for item in payload.models
        if (item.name or "").strip()
    ]
    if not models:
        raise HTTPException(status_code=400, detail="请至少填写一个模型名称")

    return {
        "name": name,
        "base_url": base_url,
        "api_key": (payload.apiKey or "").strip(),
        "timeout": timeout,
        "models": models,
    }


@router.get("")
async def list_configs():
    """列出全部服务商配置（新建的在前，密钥脱敏）。"""
    return await asyncio.to_thread(llm_config_db.list_configs)


@router.post("")
async def create_config(payload: LlmConfigPayload):
    """新建服务商配置；首次创建必须提供 API Key。"""
    data = _clean_payload(payload)
    if not data["api_key"]:
        raise HTTPException(status_code=400, detail="请填写 API Key")
    config = await asyncio.to_thread(
        llm_config_db.create_config,
        data["name"],
        data["base_url"],
        data["api_key"],
        data["timeout"],
        data["models"],
    )
    if not config:
        raise HTTPException(status_code=500, detail="大模型配置创建失败")
    return config


@router.put("/{config_id}")
async def update_config(config_id: str, payload: LlmConfigPayload):
    """更新服务商配置；apiKey 留空或传脱敏占位时保留数据库里的原密钥。"""
    data = _clean_payload(payload)
    config = await asyncio.to_thread(
        llm_config_db.update_config,
        config_id,
        data["name"],
        data["base_url"],
        data["api_key"],
        data["timeout"],
        data["models"],
    )
    if not config:
        raise HTTPException(status_code=404, detail="大模型配置不存在")
    return config


@router.delete("/{config_id}")
async def delete_config(config_id: str):
    """删除服务商配置及其模型（前端选中的模型失效后会自动回退系统默认）。"""
    deleted = await asyncio.to_thread(llm_config_db.delete_config, config_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="大模型配置不存在")
    return {"deleted": True, "id": config_id}
