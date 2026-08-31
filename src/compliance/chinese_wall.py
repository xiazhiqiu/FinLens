"""
信息隔离墙模块（Chinese Wall）

提供信息隔离功能：
- 研究部门与交易部门隔离
- 敏感信息访问控制
- 利益冲突检测
"""

from enum import Enum
from typing import Dict, List, Set, Optional, Any
from datetime import datetime, timedelta
from dataclasses import dataclass

from common.enterprise_base import EnterpriseBase
from security.rbac import RBACManager, Role, Permission


class Department(Enum):
    """部门类型"""
    RESEARCH = "research"           # 研究部门
    TRADING = "trading"             # 交易部门
    INVESTMENT_BANKING = "ib"       # 投行部门
    ASSET_MANAGEMENT = "am"         # 资产管理
    RISK_MANAGEMENT = "risk"        # 风险管理
    COMPLIANCE = "compliance"       # 合规部门
    ADMINISTRATION = "admin"        # 行政部门


class InformationClassification(Enum):
    """信息密级"""
    PUBLIC = "public"               # 公开信息
    INTERNAL = "internal"           # 内部信息
    CONFIDENTIAL = "confidential"   # 机密信息
    RESTRICTED = "restricted"       # 绝密信息


@dataclass
class InformationBarrier:
    """信息隔离墙"""
    barrier_id: str
    name: str
    department_a: Department
    department_b: Department
    information_types: List[InformationClassification]
    exceptions: List[str]  # 例外情况
    enabled: bool = True

    def to_dict(self) -> Dict:
        return {
            "barrier_id": self.barrier_id,
            "name": self.name,
            "department_a": self.department_a.value,
            "department_b": self.department_b.value,
            "information_types": [t.value for t in self.information_types],
            "exceptions": self.exceptions,
            "enabled": self.enabled,
        }


@dataclass
class AccessRequest:
    """访问请求"""
    user_id: str
    department: Department
    target_information: str
    information_classification: InformationClassification
    reason: str
    timestamp: datetime

    def to_dict(self) -> Dict:
        return {
            "user_id": self.user_id,
            "department": self.department.value,
            "target_information": self.target_information,
            "classification": self.information_classification.value,
            "reason": self.reason,
            "timestamp": self.timestamp.isoformat(),
        }


class ChineseWall:
    """信息隔离墙管理器"""

    def __init__(self, rbac_manager: RBACManager = None):
        self.rbac = rbac_manager or RBACManager()
        self.barriers: Dict[str, InformationBarrier] = {}
        self.access_log: List[Dict] = []
        self.user_departments: Dict[str, Department] = {}

        # 注册默认隔离墙
        self._register_default_barriers()

    def _register_default_barriers(self):
        """注册默认隔离墙"""

        # 研究与交易隔离
        self.register_barrier(InformationBarrier(
            barrier_id="BARRIER-001",
            name="研究-交易隔离墙",
            department_a=Department.RESEARCH,
            department_b=Department.TRADING,
            information_types=[
                InformationClassification.CONFIDENTIAL,
                InformationClassification.RESTRICTED,
            ],
            exceptions=["合规审查", "监管要求"],
        ))

        # 投行与研究隔离
        self.register_barrier(InformationBarrier(
            barrier_id="BARRIER-002",
            name="投行-研究隔离墙",
            department_a=Department.INVESTMENT_BANKING,
            department_b=Department.RESEARCH,
            information_types=[
                InformationClassification.CONFIDENTIAL,
                InformationClassification.RESTRICTED,
            ],
            exceptions=["合规审查"],
        ))

        # 资管与研究隔离
        self.register_barrier(InformationBarrier(
            barrier_id="BARRIER-003",
            name="资管-研究隔离墙",
            department_a=Department.ASSET_MANAGEMENT,
            department_b=Department.RESEARCH,
            information_types=[
                InformationClassification.CONFIDENTIAL,
            ],
            exceptions=["合规审查"],
        ))

    def register_barrier(self, barrier: InformationBarrier) -> bool:
        """注册隔离墙"""
        self.barriers[barrier.barrier_id] = barrier
        return True

    def unregister_barrier(self, barrier_id: str) -> bool:
        """注销隔离墙"""
        if barrier_id in self.barriers:
            del self.barriers[barrier_id]
            return True
        return False

    def assign_user_department(self, user_id: str, department: Department) -> bool:
        """分配用户部门"""
        self.user_departments[user_id] = department
        return True

    def get_user_department(self, user_id: str) -> Optional[Department]:
        """获取用户部门"""
        return self.user_departments.get(user_id)

    def check_access(
        self,
        user_id: str,
        target_information: str,
        information_classification: InformationClassification,
        reason: str = "",
    ) -> Dict[str, Any]:
        """
        检查访问权限

        Returns:
            访问检查结果
        """
        result = {
            "allowed": True,
            "barriers_checked": [],
            "violations": [],
            "warnings": [],
        }

        user_dept = self.get_user_department(user_id)
        if not user_dept:
            result["warnings"].append("用户未分配部门")
            return result

        # 检查所有相关隔离墙
        for barrier_id, barrier in self.barriers.items():
            if not barrier.enabled:
                continue

            # 检查是否涉及该隔离墙
            involves_barrier = (
                (user_dept == barrier.department_a and self._is_department_involved(barrier.department_b, target_information)) or
                (user_dept == barrier.department_b and self._is_department_involved(barrier.department_a, target_information))
            )

            if involves_barrier:
                result["barriers_checked"].append(barrier.barrier_id)

                # 检查信息密级
                if information_classification in barrier.information_types:
                    # 检查是否在例外情况中
                    if reason and any(exc in reason for exc in barrier.exceptions):
                        result["warnings"].append(
                            f"通过例外情况访问: {barrier.name} (原因: {reason})"
                        )
                    else:
                        result["allowed"] = False
                        result["violations"].append({
                            "barrier_id": barrier_id,
                            "barrier_name": barrier.name,
                            "violation_type": "information_barrier",
                            "message": f"违反 {barrier.name}: {user_dept.value} 不能访问 {information_classification.value} 级别信息",
                        })

        # 记录访问日志
        self._log_access(user_id, user_dept, target_information, information_classification, result)

        return result

    def _is_department_involved(self, department: Department, information: str) -> bool:
        """检查部门是否涉及特定信息"""
        # 简化实现：检查关键词
        keywords = {
            Department.RESEARCH: ["研报", "分析", "评级", "研究"],
            Department.TRADING: ["交易", "买卖", "持仓", "委托"],
            Department.INVESTMENT_BANKING: ["投行", "IPO", "并购", "承销"],
            Department.ASSET_MANAGEMENT: ["资管", "基金", "投资组合"],
        }

        dept_keywords = keywords.get(department, [])
        return any(kw in information for kw in dept_keywords)

    def _log_access(
        self,
        user_id: str,
        department: Department,
        information: str,
        classification: InformationClassification,
        result: Dict,
    ):
        """记录访问日志"""
        log_entry = {
            "user_id": user_id,
            "department": department.value,
            "information": information[:100],  # 截断敏感信息
            "classification": classification.value,
            "allowed": result["allowed"],
            "violations_count": len(result["violations"]),
            "timestamp": datetime.now().isoformat(),
        }
        self.access_log.append(log_entry)

        # 保持日志数量在合理范围
        if len(self.access_log) > 10000:
            self.access_log = self.access_log[-5000:]

    def get_access_logs(
        self,
        user_id: str = None,
        department: Department = None,
        start_time: datetime = None,
        end_time: datetime = None,
        limit: int = 100,
    ) -> List[Dict]:
        """获取访问日志"""
        logs = self.access_log

        if user_id:
            logs = [l for l in logs if l["user_id"] == user_id]

        if department:
            logs = [l for l in logs if l["department"] == department.value]

        if start_time:
            logs = [l for l in logs if datetime.fromisoformat(l["timestamp"]) >= start_time]

        if end_time:
            logs = [l for l in logs if datetime.fromisoformat(l["timestamp"]) <= end_time]

        return logs[-limit:]

    def get_violation_summary(self, days: int = 30) -> Dict[str, Any]:
        """获取违规摘要"""
        cutoff_time = datetime.now() - timedelta(days=days)
        recent_logs = [
            l for l in self.access_log
            if datetime.fromisoformat(l["timestamp"]) >= cutoff_time
        ]

        violations = [l for l in recent_logs if not l["allowed"]]

        return {
            "period_days": days,
            "total_access": len(recent_logs),
            "violations": len(violations),
            "violation_rate": len(violations) / len(recent_logs) if recent_logs else 0,
            "top_violators": self._get_top_violators(violations),
        }

    def _get_top_violators(self, violations: List[Dict], limit: int = 10) -> List[Dict]:
        """获取主要违规者"""
        from collections import Counter

        user_violations = Counter(v["user_id"] for v in violations)
        dept_violations = Counter(v["department"] for v in violations)

        return {
            "by_user": user_violations.most_common(limit),
            "by_department": dept_violations.most_common(limit),
        }
