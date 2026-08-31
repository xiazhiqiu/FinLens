"""
企业级基类模块

提供企业级基类和混入类：
- EnterpriseBase: 企业级基类
- ConfigurableMixin: 可配置混入
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from datetime import datetime
import logging


class EnterpriseBase(ABC):
    """企业级基类"""

    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.logger = logging.getLogger(self.__class__.__name__)
        self.created_at = datetime.now()
        self._initialized = False

    @abstractmethod
    def initialize(self) -> bool:
        """初始化"""
        self._initialized = True
        return True

    def is_initialized(self) -> bool:
        """检查是否已初始化"""
        return self._initialized

    def get_config(self, key: str = None, default: Any = None) -> Any:
        """获取配置"""
        if key is None:
            return self.config
        return self.config.get(key, default)

    def set_config(self, key: str, value: Any):
        """设置配置"""
        self.config[key] = value

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "class": self.__class__.__name__,
            "created_at": self.created_at.isoformat(),
            "initialized": self._initialized,
            "config": self.config,
        }

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(initialized={self._initialized})>"


class ConfigurableMixin:
    """可配置混入类"""

    def load_config(self, config_path: str) -> bool:
        """加载配置文件"""
        import json
        import os

        if not os.path.exists(config_path):
            self.logger.warning(f"配置文件不存在: {config_path}")
            return False

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            self.config.update(config)
            return True
        except Exception as e:
            self.logger.error(f"加载配置失败: {e}")
            return False

    def save_config(self, config_path: str) -> bool:
        """保存配置文件"""
        import json
        import os

        try:
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            self.logger.error(f"保存配置失败: {e}")
            return False

    def validate_config(self, required_keys: list) -> bool:
        """验证配置完整性"""
        missing_keys = [key for key in required_keys if key not in self.config]
        if missing_keys:
            self.logger.warning(f"缺少必要配置: {missing_keys}")
            return False
        return True


class AuditableMixin:
    """可审计混入类"""

    def __init__(self):
        self.audit_logger = None

    def set_audit_logger(self, logger):
        """设置审计日志记录器"""
        self.audit_logger = logger

    def audit_log(self, event_type: str, user_id: str, description: str, details: Dict = None):
        """记录审计日志"""
        if self.audit_logger:
            from audit.audit_logger import EventType, AuditLogger
            self.audit_logger.log_event(
                event_type=EventType.DATA_READ,
                user_id=user_id,
                description=description,
                details=details or {},
            )
