"""应用配置：支持从环境变量或 .env 文件读取。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置项。字段名即环境变量名（不区分大小写）。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "MAIC API"
    debug: bool = False

    # 所有业务接口统一前缀，便于前端统一代理
    api_prefix: str = "/api"

    # 允许跨域的来源，多个用英文逗号分隔
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        """把逗号分隔的字符串拆成列表，并去掉空白项。"""
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    """缓存配置实例，避免每次请求都重新解析 .env。"""
    return Settings()
