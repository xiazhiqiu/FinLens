"""
RBAC 角色权限控制模块

提供基于角色的访问控制：
- 角色定义（分析师、审核员、合规官、管理员）
- 权限管理（读、写、审核、管理）
- 资源级权限控制
"""

from enum import Enum
from typing import Dict, List, Set, Optional
from functools import wraps

from common.enterprise_base import EnterpriseBase


class Permission(Enum):
    """权限枚举"""
    # 读权限
    READ_REPORT = "read:report"
    READ_DATA = "read:data"
    READ_ANALYSIS = "read:analysis"

    # 写权限
    WRITE_REPORT = "write:report"
    WRITE_ANALYSIS = "write:analysis"
    UPLOAD_PDF = "upload:pdf"

    # 审核权限
    APPROVE_REPORT = "approve:report"
    REVIEW_CONTENT = "review:content"
    OVERRIDE_COMPLIANCE = "override:compliance"

    # 管理权限
    MANAGE_USERS = "manage:users"
    MANAGE_ROLES = "manage:roles"
    MANAGE_SYSTEM = "manage:system"
    VIEW_AUDIT_LOG = "view:audit_log"


class Role(Enum):
    """角色枚举"""
    ANALYST = "analyst"           # 分析师
    REVIEWER = "reviewer"         # 审核员
    COMPLIANCE = "compliance"     # 合规官
    ADMIN = "admin"               # 管理员
    READONLY = "readonly"         # 只读用户


# 角色-权限映射
ROLE_PERMISSIONS: Dict[Role, Set[Permission]] = {
    Role.ANALYST: {
        Permission.READ_REPORT,
        Permission.READ_DATA,
        Permission.READ_ANALYSIS,
        Permission.WRITE_REPORT,
        Permission.WRITE_ANALYSIS,
        Permission.UPLOAD_PDF,
    },
    Role.REVIEWER: {
        Permission.READ_REPORT,
        Permission.READ_DATA,
        Permission.READ_ANALYSIS,
        Permission.APPROVE_REPORT,
        Permission.REVIEW_CONTENT,
    },
    Role.COMPLIANCE: {
        Permission.READ_REPORT,
        Permission.READ_DATA,
        Permission.READ_ANALYSIS,
        Permission.REVIEW_CONTENT,
        Permission.OVERRIDE_COMPLIANCE,
        Permission.VIEW_AUDIT_LOG,
    },
    Role.ADMIN: {
        perm for perm in Permission  # 所有权限
    },
    Role.READONLY: {
        Permission.READ_REPORT,
        Permission.READ_DATA,
        Permission.READ_ANALYSIS,
    },
}


class RBACManager:
    """RBAC 管理器"""

    def __init__(self):
        self.role_permissions = ROLE_PERMISSIONS.copy()
        self.user_roles: Dict[str, Role] = {}

    def assign_role(self, user_id: str, role: Role) -> bool:
        """分配角色"""
        self.user_roles[user_id] = role
        return True

    def get_user_role(self, user_id: str) -> Optional[Role]:
        """获取用户角色"""
        return self.user_roles.get(user_id)

    def get_user_permissions(self, user_id: str) -> Set[Permission]:
        """获取用户权限"""
        role = self.get_user_role(user_id)
        if not role:
            return set()
        return self.role_permissions.get(role, set())

    def check_permission(self, user_id: str, permission: Permission) -> bool:
        """检查用户是否有指定权限"""
        permissions = self.get_user_permissions(user_id)
        return permission in permissions

    def check_any_permission(self, user_id: str, permissions: List[Permission]) -> bool:
        """检查用户是否有任一指定权限"""
        user_permissions = self.get_user_permissions(user_id)
        return bool(user_permissions.intersection(permissions))

    def check_all_permissions(self, user_id: str, permissions: List[Permission]) -> bool:
        """检查用户是否有所有指定权限"""
        user_permissions = self.get_user_permissions(user_id)
        return all(p in user_permissions for p in permissions)

    def require_permission(self, permission: Permission):
        """权限装饰器"""
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                # 从参数中获取 user_id
                user_id = kwargs.get("user_id") or (args[0] if args else None)
                if not user_id:
                    raise PermissionError("用户身份未提供")

                if not self.check_permission(user_id, permission):
                    raise PermissionError(f"用户 {user_id} 无权限: {permission.value}")

                return func(*args, **kwargs)
            return wrapper
        return decorator

    def require_any_permission(self, permissions: List[Permission]):
        """任一权限装饰器"""
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                user_id = kwargs.get("user_id") or (args[0] if args else None)
                if not user_id:
                    raise PermissionError("用户身份未提供")

                if not self.check_any_permission(user_id, permissions):
                    perm_names = [p.value for p in permissions]
                    raise PermissionError(f"用户 {user_id} 无任一权限: {perm_names}")

                return func(*args, **kwargs)
            return wrapper
        return decorator


class ResourcePermission:
    """资源级权限控制"""

    def __init__(self, rbac_manager: RBACManager):
        self.rbac = rbac_manager
        self.resource_owners: Dict[str, str] = {}  # resource_id -> owner_id

    def register_resource(self, resource_id: str, owner_id: str):
        """注册资源所有者"""
        self.resource_owners[resource_id] = owner_id

    def check_resource_access(self, user_id: str, resource_id: str, permission: Permission) -> bool:
        """检查资源访问权限"""
        # 管理员始终有权限
        if self.rbac.check_permission(user_id, Permission.MANAGE_SYSTEM):
            return True

        # 检查基本权限
        if not self.rbac.check_permission(user_id, permission):
            return False

        # 检查资源所有权（可选）
        owner = self.resource_owners.get(resource_id)
        if owner and owner == user_id:
            return True

        # 检查角色（审核员、合规官可以访问所有资源）
        role = self.rbac.get_user_role(user_id)
        if role in [Role.REVIEWER, Role.COMPLIANCE, Role.ADMIN]:
            return True

        return False
