"""
结构化审计日志模块

提供审计日志功能：
- 结构化事件记录
- 多种事件类型支持
- 日志查询和分析
"""

from enum import Enum
from typing import Dict, List, Optional, Any
from datetime import datetime
from dataclasses import dataclass, asdict
import json
import hashlib

from common.enterprise_base import EnterpriseBase
from audit.immutable_store import ImmutableStore


class EventType(Enum):
    """事件类型"""
    # 认证事件
    USER_LOGIN = "user_login"
    USER_LOGOUT = "user_logout"
    AUTH_FAILURE = "auth_failure"
    TOKEN_REFRESH = "token_refresh"

    # 数据访问事件
    DATA_READ = "data_read"
    DATA_WRITE = "data_write"
    DATA_DELETE = "data_delete"
    DATA_EXPORT = "data_export"

    # 分析事件
    ANALYSIS_START = "analysis_start"
    ANALYSIS_COMPLETE = "analysis_complete"
    ANALYSIS_FAILED = "analysis_failed"

    # 报告事件
    REPORT_GENERATE = "report_generate"
    REPORT_APPROVE = "report_approve"
    REPORT_REJECT = "report_reject"
    REPORT_PUBLISH = "report_publish"

    # 合规事件
    COMPLIANCE_CHECK = "compliance_check"
    COMPLIANCE_VIOLATION = "compliance_violation"
    CONTENT_FILTER = "content_filter"

    # 系统事件
    SYSTEM_START = "system_start"
    SYSTEM_STOP = "system_stop"
    SYSTEM_ERROR = "system_error"


class EventSeverity(Enum):
    """事件严重程度"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class AuditEvent:
    """审计事件"""
    event_id: str
    event_type: EventType
    severity: EventSeverity
    user_id: str
    timestamp: datetime
    description: str
    details: Dict[str, Any]
    source_ip: str = ""
    session_id: str = ""
    request_id: str = ""
    duration_ms: float = 0
    success: bool = True
    error_message: str = ""

    def to_dict(self) -> Dict:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "severity": self.severity.value,
            "user_id": self.user_id,
            "timestamp": self.timestamp.isoformat(),
            "description": self.description,
            "details": self.details,
            "source_ip": self.source_ip,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "duration_ms": self.duration_ms,
            "success": self.success,
            "error_message": self.error_message,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)

    def compute_hash(self) -> str:
        """计算事件哈希"""
        data = f"{self.event_id}{self.event_type.value}{self.user_id}{self.timestamp.isoformat()}"
        return hashlib.sha256(data.encode()).hexdigest()


class AuditLogger:
    """审计日志记录器"""

    def __init__(self, enable_console: bool = True, enable_file: bool = True, storage_path: str = None):
        """
        初始化审计日志记录器

        Args:
            enable_console: 是否启用控制台输出
            enable_file: 是否启用文件存储（ImmutableStore 哈希链）
            storage_path: 审计日志存储路径
        """
        self.enable_console = enable_console
        self.enable_file = enable_file
        self.events: List[AuditEvent] = []
        self.event_counter = 0
        self.store = ImmutableStore(storage_path) if enable_file else None

    def _generate_event_id(self) -> str:
        """生成事件ID"""
        self.event_counter += 1
        return f"AE-{datetime.now().strftime('%Y%m%d')}-{self.event_counter:06d}"

    def log_event(
        self,
        event_type: EventType,
        user_id: str,
        description: str,
        details: Dict = None,
        severity: EventSeverity = EventSeverity.INFO,
        source_ip: str = "",
        session_id: str = "",
        request_id: str = "",
        duration_ms: float = 0,
        success: bool = True,
        error_message: str = "",
    ) -> AuditEvent:
        """
        记录审计事件

        Args:
            event_type: 事件类型
            user_id: 用户ID
            description: 事件描述
            details: 事件详情
            severity: 严重程度
            source_ip: 来源IP
            session_id: 会话ID
            request_id: 请求ID
            duration_ms: 执行时长（毫秒）
            success: 是否成功
            error_message: 错误信息

        Returns:
            审计事件对象
        """
        event = AuditEvent(
            event_id=self._generate_event_id(),
            event_type=event_type,
            severity=severity,
            user_id=user_id,
            timestamp=datetime.now(),
            description=description,
            details=details or {},
            source_ip=source_ip,
            session_id=session_id,
            request_id=request_id,
            duration_ms=duration_ms,
            success=success,
            error_message=error_message,
        )

        # 存储事件
        self.events.append(event)

        # 控制台输出
        if self.enable_console:
            self._log_to_console(event)

        # 文件存储（实际应使用 ImmutableStore）
        if self.enable_file:
            self._log_to_file(event)

        return event

    def _log_to_console(self, event: AuditEvent):
        """输出到控制台"""
        severity_colors = {
            EventSeverity.INFO: "\033[32m",  # 绿色
            EventSeverity.WARNING: "\033[33m",  # 黄色
            EventSeverity.ERROR: "\033[31m",  # 红色
            EventSeverity.CRITICAL: "\033[35m",  # 紫色
        }
        reset_color = "\033[0m"

        color = severity_colors.get(event.severity, "")
        print(f"{color}[AUDIT] {event.timestamp.isoformat()} | {event.event_type.value} | {event.user_id} | {event.description}{reset_color}")

    def _log_to_file(self, event: AuditEvent):
        """输出到文件（ImmutableStore 哈希链防篡改）"""
        if self.store:
            self.store.append(event.to_dict())

    def log_user_action(
        self,
        user_id: str,
        action: str,
        resource: str,
        details: Dict = None,
        success: bool = True,
    ) -> AuditEvent:
        """记录用户操作"""
        return self.log_event(
            event_type=EventType.DATA_READ,
            user_id=user_id,
            description=f"用户操作: {action} on {resource}",
            details=details or {"resource": resource},
            success=success,
        )

    def log_analysis_event(
        self,
        user_id: str,
        analysis_type: str,
        status: str,
        details: Dict = None,
        duration_ms: float = 0,
    ) -> AuditEvent:
        """记录分析事件"""
        event_type = {
            "start": EventType.ANALYSIS_START,
            "complete": EventType.ANALYSIS_COMPLETE,
            "failed": EventType.ANALYSIS_FAILED,
        }.get(status, EventType.ANALYSIS_START)

        return self.log_event(
            event_type=event_type,
            user_id=user_id,
            description=f"分析{status}: {analysis_type}",
            details=details or {"analysis_type": analysis_type},
            duration_ms=duration_ms,
            success=(status != "failed"),
        )

    def log_compliance_event(
        self,
        user_id: str,
        check_type: str,
        result: str,
        violations: List = None,
    ) -> AuditEvent:
        """记录合规事件"""
        event_type = EventType.COMPLIANCE_VIOLATION if violations else EventType.COMPLIANCE_CHECK

        return self.log_event(
            event_type=event_type,
            user_id=user_id,
            description=f"合规检查: {check_type} - {result}",
            details={
                "check_type": check_type,
                "result": result,
                "violations_count": len(violations) if violations else 0,
            },
            severity=EventSeverity.WARNING if violations else EventSeverity.INFO,
        )

    def query_events(
        self,
        event_type: EventType = None,
        user_id: str = None,
        start_time: datetime = None,
        end_time: datetime = None,
        severity: EventSeverity = None,
        limit: int = 100,
    ) -> List[AuditEvent]:
        """查询审计事件"""
        results = self.events

        if event_type:
            results = [e for e in results if e.event_type == event_type]

        if user_id:
            results = [e for e in results if e.user_id == user_id]

        if start_time:
            results = [e for e in results if e.timestamp >= start_time]

        if end_time:
            results = [e for e in results if e.timestamp <= end_time]

        if severity:
            results = [e for e in results if e.severity == severity]

        return results[-limit:]

    def get_statistics(self, days: int = 30) -> Dict[str, Any]:
        """获取统计信息"""
        from datetime import timedelta

        cutoff_time = datetime.now() - timedelta(days=days)
        recent_events = [e for e in self.events if e.timestamp >= cutoff_time]

        # 按类型统计
        type_counts = {}
        for event in recent_events:
            type_name = event.event_type.value
            type_counts[type_name] = type_counts.get(type_name, 0) + 1

        # 按用户统计
        user_counts = {}
        for event in recent_events:
            user_counts[event.user_id] = user_counts.get(event.user_id, 0) + 1

        # 按严重程度统计
        severity_counts = {}
        for event in recent_events:
            severity_name = event.severity.value
            severity_counts[severity_name] = severity_counts.get(severity_name, 0) + 1

        # 失败事件
        failed_events = [e for e in recent_events if not e.success]

        return {
            "period_days": days,
            "total_events": len(recent_events),
            "events_by_type": type_counts,
            "events_by_user": user_counts,
            "events_by_severity": severity_counts,
            "failed_events": len(failed_events),
            "failure_rate": len(failed_events) / len(recent_events) if recent_events else 0,
        }
