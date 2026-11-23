"""本地启动入口：在 server 目录执行 `python main.py` 即可启动服务。"""

import uvicorn

from app.core.config import get_settings
from app.main import app

__all__ = ["app"]


if __name__ == "__main__":
    settings = get_settings()
    # 会话/检查点/任务表均为进程内存，必须保持单 worker；生产请用 start_prod.ps1 / .sh
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="info",
    )
