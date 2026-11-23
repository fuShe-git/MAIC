"""应用配置：支持从环境变量或 .env 文件读取。"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 固定读取 server/.env，避免从仓库根目录启动时读不到配置
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    """全局配置项。字段名即环境变量名（不区分大小写）。"""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8-sig",
        extra="ignore",
    )

    app_name: str = "MAIC API"
    debug: bool = False

    # 监听地址与端口：默认 0.0.0.0 让容器/远程机器可被访问，本地开发也照常可用
    host: str = "0.0.0.0"
    port: int = 8001

    # 课件数据库与产物根目录；留空时默认 server/outputs
    maic_output_dir: str = ""

    # 所有业务接口统一前缀，便于前端统一代理
    api_prefix: str = "/api"

    # 允许跨域的来源，多个用英文逗号分隔
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # 讨论：课件上下文字符预算（Token 是模型处理文本的基本单位，限制长度可控制费用与延迟）
    discussion_context_chars: int = 16000
    # 讨论：随请求携带的最近对话轮数
    discussion_history_turns: int = 8
    # 讨论：联网搜索返回条数
    discussion_web_results: int = 3
    # 联网搜索密钥（讨论在课件不足时使用）
    tavily_api_key: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        """把逗号分隔的字符串拆成列表，并去掉空白项。"""
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    """缓存配置实例，避免每次请求都重新解析 .env。"""
    return Settings()
