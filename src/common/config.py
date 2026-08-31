"""
企业级配置模块

提供企业级配置管理：
- 多环境配置（开发、测试、生产）
- 配置加密
- 配置验证
"""

import os
import json
from typing import Dict, Any, Optional
from enum import Enum
from dataclasses import dataclass

from .enterprise_base import EnterpriseBase, ConfigurableMixin


class Environment(Enum):
    """环境类型"""
    DEVELOPMENT = "development"
    TESTING = "testing"
    STAGING = "staging"
    PRODUCTION = "production"


@dataclass
class SecurityConfig:
    """安全配置"""
    jwt_secret: str = ""
    jwt_expire_minutes: int = 60
    aes_encryption_key: str = ""
    api_key_enabled: bool = False
    max_login_attempts: int = 5
    lockout_duration_minutes: int = 30

    def to_dict(self) -> Dict:
        return {
            "jwt_secret": "***" if self.jwt_secret else "",
            "jwt_expire_minutes": self.jwt_expire_minutes,
            "aes_encryption_key": "***" if self.aes_encryption_key else "",
            "api_key_enabled": self.api_key_enabled,
            "max_login_attempts": self.max_login_attempts,
            "lockout_duration_minutes": self.lockout_duration_minutes,
        }


@dataclass
class ComplianceConfig:
    """合规配置"""
    strict_mode: bool = False
    enable_content_filter: bool = True
    enable_chinese_wall: bool = True
    report_approval_required: bool = True
    audit_retention_days: int = 2555  # 7年

    def to_dict(self) -> Dict:
        return {
            "strict_mode": self.strict_mode,
            "enable_content_filter": self.enable_content_filter,
            "enable_chinese_wall": self.enable_chinese_wall,
            "report_approval_required": self.report_approval_required,
            "audit_retention_days": self.audit_retention_days,
        }


@dataclass
class AuditConfig:
    """审计配置"""
    enable_console: bool = True
    enable_file: bool = True
    enable_database: bool = False
    log_level: str = "INFO"
    retention_days: int = 2555  # 7年
    enable_immutable_store: bool = True

    def to_dict(self) -> Dict:
        return {
            "enable_console": self.enable_console,
            "enable_file": self.enable_file,
            "enable_database": self.enable_database,
            "log_level": self.log_level,
            "retention_days": self.retention_days,
            "enable_immutable_store": self.enable_immutable_store,
        }


class EnterpriseConfig(EnterpriseBase, ConfigurableMixin):
    """企业级配置管理器"""

    def __init__(self, environment: Environment = None):
        super().__init__()
        self.environment = environment or Environment.DEVELOPMENT
        self.security = SecurityConfig()
        self.compliance = ComplianceConfig()
        self.audit = AuditConfig()
        self.data_sources: Dict[str, Any] = {}
        self.llm_config: Dict[str, Any] = {}

    def initialize(self) -> bool:
        """初始化配置"""
        # 从环境变量加载
        self._load_from_env()

        # 加载配置文件
        config_file = f"config/{self.environment.value}.json"
        if os.path.exists(config_file):
            self.load_config(config_file)

        self._initialized = True
        return True

    def _load_from_env(self):
        """从环境变量加载配置"""
        # 安全配置
        self.security.jwt_secret = os.getenv("JWT_SECRET", self.security.jwt_secret)
        self.security.aes_encryption_key = os.getenv("AES_ENCRYPTION_KEY", self.security.aes_encryption_key)

        # 合规配置
        self.compliance.strict_mode = os.getenv("COMPLIANCE_STRICT_MODE", "false").lower() == "true"

        # 审计配置
        self.audit.enable_console = os.getenv("AUDIT_ENABLE_CONSOLE", "true").lower() == "true"
        self.audit.enable_file = os.getenv("AUDIT_ENABLE_FILE", "true").lower() == "true"

    def get_security_config(self) -> SecurityConfig:
        """获取安全配置"""
        return self.security

    def get_compliance_config(self) -> ComplianceConfig:
        """获取合规配置"""
        return self.compliance

    def get_audit_config(self) -> AuditConfig:
        """获取审计配置"""
        return self.audit

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "environment": self.environment.value,
            "security": self.security.to_dict(),
            "compliance": self.compliance.to_dict(),
            "audit": self.audit.to_dict(),
            "data_sources": self.data_sources,
            "llm_config": self.llm_config,
        }

    def validate(self) -> Dict[str, Any]:
        """验证配置"""
        issues = []

        # 验证安全配置
        if not self.security.jwt_secret:
            issues.append("JWT Secret 未配置")

        if not self.security.aes_encryption_key:
            issues.append("AES 加密密钥未配置")

        # 验证生产环境配置
        if self.environment == Environment.PRODUCTION:
            if self.security.jwt_secret == "finscope-enterprise-secret-key-change-in-production":
                issues.append("生产环境使用了默认 JWT Secret")

            if self.security.aes_encryption_key == "finscope-default-key-change-in-production-32b":
                issues.append("生产环境使用了默认 AES 密钥")

        return {
            "is_valid": len(issues) == 0,
            "issues": issues,
            "environment": self.environment.value,
        }
