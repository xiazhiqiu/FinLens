"""
FinScope Enterprise Security Module

提供企业级安全功能：
- JWT/SSO 认证
- RBAC 角色权限控制
- AES-256 数据加密
- 输入防护（SQL注入、XSS、Prompt注入）
"""

from .auth import JWTAuth, TokenManager
from .rbac import RBACManager, Role, Permission
from .encryption import AES256Encryption
from .input_guard import InputGuard

__all__ = [
    "JWTAuth",
    "TokenManager",
    "RBACManager",
    "Role",
    "Permission",
    "AES256Encryption",
    "InputGuard",
]
