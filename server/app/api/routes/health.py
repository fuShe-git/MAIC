"""健康检查接口，用于确认服务已启动。"""

from fastapi import APIRouter
from pydantic import BaseModel

from app import __version__

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str


@router.get("/health", response_model=HealthResponse, summary="健康检查")
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)
