"""
FinScope Enterprise Common Module

提供企业级共享工具：
- 基类和接口定义
- 配置管理
- 工具函数
"""

from .enterprise_base import EnterpriseBase, ConfigurableMixin
from .config import EnterpriseConfig

__all__ = [
    "EnterpriseBase",
    "ConfigurableMixin",
    "EnterpriseConfig",
]
