# MAIC 后端服务

基于 **FastAPI** 的 MAIC（多智能体互动课堂）后端，提供 HTTP 接口、配置管理与接口文档。

## 目录结构

```
server/
├── main.py                     # 启动入口（python main.py 直接运行）
├── requirements.txt            # Python 依赖清单
├── .env.example                # 环境变量模板（复制为 .env 使用）
├── .gitignore                  # Git 忽略规则（缓存、虚拟环境、.env）
├── README.md                   # 本说明文档
└── app/
    ├── __init__.py             # 包声明，定义 __version__ 版本号
    ├── main.py                 # 应用装配：create_app() 创建应用并注册中间件、路由
    ├── core/
    │   ├── __init__.py         # core 包声明
    │   └── config.py           # 配置中心：Settings 从环境变量 / .env 读取配置
    ├── agent/
    │   ├── __init__.py         # agent 包声明
    │   └── agent.py            # 大模型调用封装（懒加载客户端 + 异步 chat 函数）
    └── api/
        ├── __init__.py         # api 包声明
        ├── router.py           # 路由聚合：把各业务路由汇总为一个 api_router
        └── routes/
            ├── __init__.py     # routes 包声明
            ├── health.py       # 健康检查接口 GET /api/health
            └── chat.py         # 对话接口 POST /api/chat
```

## 各文件作用

| 文件 | 作用 |
| --- | --- |
| `main.py` | 项目启动入口。导入 `app.main` 中的应用实例，并在 `if __name__ == "__main__"` 块内调用 `uvicorn.run()`，执行 `python main.py` 即可启动服务（默认 `127.0.0.1:8001`）。 |
| `app/main.py` | 应用装配层。`create_app()` 负责创建 FastAPI 实例、挂载 CORS 中间件、以 `API_PREFIX` 为前缀注册所有路由，并暴露根路径 `GET /`。 |
| `app/core/config.py` | 配置中心。`Settings` 基于 `pydantic-settings`，从环境变量或 `.env` 读取 `APP_NAME`、`DEBUG`、`API_PREFIX`、`CORS_ORIGINS`；`get_settings()` 用 `lru_cache` 缓存实例。 |
| `app/api/router.py` | 路由聚合。创建 `api_router` 并 `include_router` 各业务子路由，新增业务模块时在此登记一行即可。 |
| `app/api/routes/health.py` | 健康检查接口，返回服务状态与版本号，常用于部署探活。 |
| `requirements.txt` | 声明运行依赖：`fastapi`、`uvicorn[standard]`、`pydantic-settings`。 |
| `.env.example` | 环境变量示例，复制为 `.env` 后按需修改，`.env` 已被 Git 忽略。 |
| `.gitignore` | 忽略 `__pycache__/`、`.venv/`、`.env` 等本地文件，避免污染仓库。 |
| `app/**/__init__.py` | 将目录标记为 Python 包，使 `app.core`、`app.api` 等可被导入。 |

## 环境要求

- Python 3.10 及以上（代码使用了 `list[str]`、`dict[str, str]` 等新语法）

## 安装与启动

```powershell
cd d:\renfu-mycode\product\MAIC\server

# 1. 创建并激活虚拟环境
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. 安装依赖
pip install -r requirements.txt

# 3. 准备环境变量（可选）
Copy-Item .env.example .env

# 4. 启动服务
python main.py
```

若 PowerShell 禁止执行 `Activate.ps1`（执行策略限制），可跳过激活，直接使用虚拟环境中的解释器：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

开发时若需要修改代码自动重启，可改用 uvicorn 命令（需在 `server` 目录下执行）：

```powershell
python -m uvicorn app.main:app --reload --port 8001
```

## 接口一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 服务信息（名称、版本、文档地址） |
| GET | `/api/health` | 健康检查，返回 `{"status": "ok", "version": "0.1.0"}` |
| POST | `/api/chat` | 对话接口，请求体 `{"message": "你好"}`，返回 `{"reply": "..."}` |
| GET | `/docs` | Swagger UI 交互式接口文档 |
| GET | `/redoc` | ReDoc 风格的接口文档 |
| GET | `/openapi.json` | OpenAPI 规范文件 |

## 环境变量

| 变量名 | 默认值 | 说明 |
| --- | --- | --- |
| `APP_NAME` | `MAIC API` | 服务名称，显示在接口文档标题处 |
| `DEBUG` | `false` | 调试模式开关，开启后返回详细错误堆栈 |
| `API_PREFIX` | `/api` | 所有业务接口的统一路径前缀 |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | 允许跨域的前端来源，多个用英文逗号分隔 |
| `MODEL_NAME` | 无 | 大模型名称，由 `app/agent/agent.py` 读取 |
| `API_KEY` | 无 | 大模型服务商密钥 |
| `API_BASE_URL` | 无 | 兼容 OpenAI 协议的服务地址 |

## 新增一个接口

1. 在 `app/api/routes/` 下新建模块（如 `chat.py`），定义 `router = APIRouter(tags=["chat"])` 并编写路由函数。
2. 在 `app/api/router.py` 中 `import` 该模块，并调用 `api_router.include_router(chat.router)`。
3. 重启服务，接口会出现在 `/docs` 中。
