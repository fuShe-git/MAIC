"""FastAPI 应用入口：创建应用、挂载中间件与路由。"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.router import api_router
from app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        debug=settings.debug,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # 允许前端开发服务器跨域访问
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix=settings.api_prefix)

    @app.get("/", tags=["meta"], summary="服务信息")
    async def root() -> dict[str, str]:
        return {
            "name": settings.app_name,
            "version": __version__,
            "docs": "/docs",
            "health": f"{settings.api_prefix}/health",
        }

    return app


app = create_app()
