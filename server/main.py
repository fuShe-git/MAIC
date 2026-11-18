"""本地启动入口：在 server 目录执行 `python main.py` 即可启动服务。"""

import uvicorn

from app.main import app

__all__ = ["app"]


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8001,
        reload=False,
    )
