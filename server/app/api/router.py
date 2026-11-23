"""聚合所有子路由，供 main.py 统一挂载。"""

from fastapi import APIRouter

from app.api.routes import chat, courses, discussion, generated, health

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(chat.router)
api_router.include_router(courses.router)
api_router.include_router(discussion.router)
api_router.include_router(generated.router)
