"""聚合所有子路由，供 main.py 统一挂载。"""

from fastapi import APIRouter

from app.api.routes import chat, health

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(chat.router)
