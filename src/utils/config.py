"""
FinScope 全局配置管理

pydantic-settings 实现:
- 自动从 .env 加载环境变量
- 类型校验 + 非法值自动降级
- 线程安全单例模式
"""

import threading
from typing import Optional, Dict

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """FinScope 全局配置类"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        case_sensitive=False,
    )

    # ---- LLM 配置 ----
    LLM_PROVIDER: str = Field(
        default="deepseek",
        description="LLM 提供商: deepseek / openai / ollama",
    )
    DEEPSEEK_API_KEY: str = Field(default="", description="DeepSeek API 密钥")
    DEEPSEEK_BASE_URL: str = Field(
        default="https://api.deepseek.com/v1", description="DeepSeek API 地址"
    )
    DEEPSEEK_MODEL: str = Field(default="deepseek-chat", description="DeepSeek 模型名")
    OPENAI_API_KEY: str = Field(default="", description="OpenAI API 密钥")
    OPENAI_BASE_URL: str = Field(default="https://api.openai.com/v1", description="OpenAI API 地址")
    OPENAI_MODEL: str = Field(default="gpt-4o", description="OpenAI 模型名")
    OLLAMA_BASE_URL: str = Field(
        default="http://localhost:11434", description="Ollama 服务地址"
    )
    OLLAMA_MODEL: str = Field(default="qwen2.5:14b", description="Ollama 模型名")

    # ---- 金融数据源配置 ----
    TUSHARE_TOKEN: str = Field(default="", description="Tushare API token")
    TUSHARE_PRIORITY: bool = Field(default=True, description="Tushare 优先于 AkShare")

    # ---- PDF 解析 ----
    MINERU_API_URL: str = Field(
        default="",
        description="MinerU API 服务地址（如 http://localhost:8000）；留空使用本地 CLI。"
        "200 页研报全文解析耗时数分钟，生产环境建议部署 mineru-api 服务",
    )
    MINERU_TIMEOUT_SECONDS: int = Field(
        default=900, ge=30, le=7200,
        description="MinerU 解析超时（秒）；200 页全文解析建议 ≥600",
    )
    PARSE_CACHE_ENABLED: bool = Field(
        default=True,
        description="PDF 解析结果缓存（按内容 SHA-256 键控，schema 变更自动失效）",
    )
    PARSE_CACHE_DIR: str = Field(
        default="./data/parse_cache", description="解析缓存目录"
    )

    # ---- 数据库与存储 ----
    SQLITE_PATH: str = Field(
        default="./data/sqlite/agent_state.db", description="SQLite 持久化路径"
    )

    # ---- Agent 执行控制 ----
    MAX_AGENT_ITERATIONS: int = Field(default=15, ge=1, le=100, description="硬熔断上限")
    AGENT_TIMEOUT_SECONDS: int = Field(default=120, ge=10, le=600, description="单次调用超时")
    SINGLE_AGENT_MAX_CALLS: int = Field(
        default=3, ge=1, le=10, description="单Agent连续调用上限"
    )

    # ---- 多级上下文压缩（P1-P4）----
    USE_MULTILEVEL_COMPRESSION: bool = Field(
        default=True,
        description="L1 无损结构化 + 预算装配链路（P4 起唯一路径；关闭仅用于诊断降级）",
    )
    CONTEXT_BUDGET_TOKENS: int = Field(
        default=24000, ge=1000, le=128000,
        description="Agent 单次注入上下文预算（装配器硬约束）",
    )
    MAX_TOOL_ROUNDS_PER_AGENT: int = Field(
        default=5, ge=1, le=30,
        description="单 Agent 工具循环轮数上限（每轮可带多条并行调用；"
                    "真实调用总数另有 40 次硬熔断兜底，C4 语义修正）",
    )
    L2_MIN_TEXT_TOKENS: int = Field(
        default=600, ge=100, le=20000,
        description="章节文本超过此 token 量才值得 L2 语义压缩（小的直接原文直注）",
    )

    # ---- P5 架构演进 ----
    USE_DOMAIN_AGENTS: bool = Field(
        default=True,
        description="E2: 5 领域 agent 替代单 Analyst（关闭或十节覆盖率不足时回退全局装配路径）",
    )
    L2_EAGER_MAX_NEW: int = Field(
        default=30, ge=1, le=100,
        description="E1: ContextPreparator 单次 L2 急切构建上限（替代 Analyst 惰性 8/次）",
    )
    DOMAIN_CHAPTER_COVERAGE_MIN: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="E2: 进入领域模式的最小十节覆盖率（非 T3 text token 占比，非标模板自动回退）",
    )
    DOMAIN_MAX_PARALLEL_AGENTS: int = Field(
        default=3, ge=1, le=5,
        description="E2: 领域 agent 并行度（ThreadPoolExecutor workers）",
    )

    # ---- LLM 调用参数 ----
    LLM_TEMPERATURE: float = Field(default=0.2, ge=0.0, le=2.0, description="LLM温度")
    LLM_MAX_TOKENS: int = Field(default=4096, ge=256, le=32768, description="最大token数")

    # ---- 日志 ----
    LOG_LEVEL: str = Field(default="INFO", description="日志级别")

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper().strip()
        if upper not in valid_levels:
            import warnings
            warnings.warn(f"LOG_LEVEL={v} 非法，已降级为 INFO")
            return "INFO"
        return upper

    @field_validator("DEEPSEEK_BASE_URL")
    @classmethod
    def validate_base_url(cls, v: str) -> str:
        v = v.rstrip("/")
        if not v.endswith("/v1"):
            v = v + "/v1"
        return v

    def get_api_key_status(self) -> Dict[str, any]:
        """返回 API Key 配置状态"""
        provider = self.LLM_PROVIDER.lower()

        if provider == "deepseek":
            key = self.DEEPSEEK_API_KEY
        elif provider == "openai":
            key = self.OPENAI_API_KEY
        elif provider == "ollama":
            return {"configured": True, "masked_key": "local", "status": "ready"}
        else:
            key = ""

        if not key:
            return {"configured": False, "masked_key": "", "status": "missing"}
        if "your-" in key.lower() or "placeholder" in key.lower() or key == "sk-":
            return {"configured": False, "masked_key": "sk-****placeholder", "status": "placeholder"}

        if len(key) > 10:
            masked = key[:5] + "****" + key[-4:]
        else:
            masked = "****"
        return {"configured": True, "masked_key": masked, "status": "ready"}

    def is_api_ready(self) -> bool:
        """检查 LLM 是否就绪"""
        return self.get_api_key_status()["status"] == "ready"

    def validate_data_source(self) -> Dict[str, any]:
        """检查数据源配置状态"""
        has_tushare = bool(self.TUSHARE_TOKEN and self.TUSHARE_TOKEN != "your-tushare-token-here")
        has_akshare = True  # AkShare 无需 token，只要网络可达

        if has_tushare:
            return {
                "configured": True,
                "primary": "Tushare",
                "fallback": "AkShare",
                "status": "ready",
                "message": "Tushare + AkShare 双源可用",
            }
        elif has_akshare:
            return {
                "configured": True,
                "primary": "AkShare",
                "fallback": None,
                "status": "degraded",
                "message": "Tushare 未配置，仅 AkShare 可用（部分功能受限）",
            }
        else:
            return {
                "configured": False,
                "primary": None,
                "fallback": None,
                "status": "unavailable",
                "message": "无可用数据源，请配置 TUSHARE_TOKEN",
            }


_settings_instance: Optional[Settings] = None
_lock = threading.Lock()


def get_settings() -> Settings:
    """获取全局 Settings 单例（线程安全）"""
    global _settings_instance
    if _settings_instance is None:
        with _lock:
            if _settings_instance is None:
                try:
                    _settings_instance = Settings()
                except Exception as e:
                    import warnings
                    warnings.warn(f"Settings 初始化异常 ({e})，使用全默认配置运行")
                    _settings_instance = Settings()
    return _settings_instance


settings = get_settings()
