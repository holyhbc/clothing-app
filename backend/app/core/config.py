"""应用配置：只从环境变量读，**不硬编码任何密钥**（docs/11-部署运维与发布规范.md §2）。

规则：
    - 键名与 ``.env.example`` 一一对应
    - 缺密钥时**启动即失败**，绝不静默使用弱默认值（宁可起不来，也不要用假密钥）
    - ``JWT_SECRET`` 缺失时给出可直接照做的中文提示，而不是 pydantic 的原始报错
"""

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["local", "test", "production"]


class Settings(BaseSettings):
    """应用配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- 应用 ----
    app_env: AppEnv = Field(default="local", description="运行环境：local / test / production")
    app_version: str = Field(default="0.1.0", description="应用版本（镜像 tag）")

    # ---- 数据库（docs/04 §6.2.1：应用账号只有 SELECT/INSERT/UPDATE）----
    database_url: SecretStr = Field(
        default=SecretStr(""),
        description="应用连接串（erp_app）；留空表示本进程不连库（如仅跑 /healthz）",
    )
    database_url_migration: SecretStr = Field(
        default=SecretStr(""),
        description="迁移连接串（owner / erp_ddl）；只在执行 alembic 时使用",
    )
    db_pool_size: int = Field(default=10, ge=1, le=60, description="连接池常驻连接数")
    db_max_overflow: int = Field(default=10, ge=0, le=60, description="连接池溢出上限")
    db_echo: bool = Field(default=False, description="是否打印 SQL；生产禁止开启")

    # ---- Redis ----
    redis_url: SecretStr = Field(default=SecretStr(""), description="Redis 连接串")

    # ---- 认证 ----
    jwt_secret: SecretStr = Field(
        default=SecretStr(""),
        description="JWT 签名密钥；生产必填，生成：openssl rand -hex 32",
    )
    jwt_algorithm: str = Field(default="HS256", description="JWT 算法（docs/07 §1.1 固定 HS256）")
    jwt_expire_minutes: int = Field(
        default=15, ge=1, le=1440, description="access token 有效期（分钟）"
    )
    jwt_refresh_expire_days: int = Field(
        default=7, ge=1, le=90, description="refresh token 有效期（天），PC 端 7 天"
    )

    # ---- 运维 ----
    log_level: str = Field(default="INFO", description="日志级别：DEBUG/INFO/WARNING/ERROR")
    log_json: bool = Field(default=True, description="是否输出结构化 JSON 日志（docs/11 §8.1）")

    @model_validator(mode="after")
    def _check_production_secrets(self) -> Self:
        """生产环境缺密钥直接失败 —— 宁可起不来，也不许用假密钥签发 token。

        非生产环境允许为空：仅便于跑 ``/healthz`` 与纯单元测试。
        真正需要签发 token 时 ``app/core/security.py`` 会再校验一次，避免误签。
        """
        if self.app_env != "production":
            return self
        missing: list[str] = []
        if not self.jwt_secret.get_secret_value():
            missing.append("JWT_SECRET")
        if not self.database_url.get_secret_value():
            missing.append("DATABASE_URL")
        if missing:
            raise ValueError(
                f"生产环境缺少必需密钥：{', '.join(missing)}。"
                "请执行 cp .env.example .env 并填写；JWT_SECRET 生成：openssl rand -hex 32"
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程级单例。测试里用 ``get_settings.cache_clear()`` 重置。"""
    return Settings()


def require_database_url(settings: Settings | None = None) -> str:
    """取应用连接串；缺失时抛业务异常而不是返回空串。"""
    resolved = settings or get_settings()
    url = resolved.database_url.get_secret_value()
    if not url:
        from app.core.errors import BusinessError, ErrorCode

        raise BusinessError(
            ErrorCode.INTERNAL,
            "数据库未配置：请在 .env 设置 DATABASE_URL（应用账号 erp_app）",
        )
    return url
