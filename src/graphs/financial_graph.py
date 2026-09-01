"""
FinScope 金融分析 StateGraph 编排

核心架构: Supervisor + 4 专业子 Agent
- Supervisor: 任务拆解 + Agent 指派
- ReportExtractor: PDF研报关键实体抽取
- DataRetriever: 金融数据检索
- FinancialAnalyst: 深度金融分析
- ReportWriter: 结构化分析报告撰写

安全机制:
- 硬熔断: iteration_count >= 15 强制终止
- 状态持久化: SqliteSaver checkpoint
- 错误隔离: 子 Agent 异常不中断主流程

[企业级] 安全机制:
- 输入验证: Prompt注入检测
- 审计日志: 全链路操作记录
- 合规检查: 内容过滤 + 信息隔离
"""

import os
import logging
import time
from typing import Literal, Dict, Any

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

from .state import FinancialAnalysisState, create_initial_state

from agents.supervisor import supervisor_node
from agents.report_extractor import report_extractor_node
from agents.data_retriever import data_retriever_node
from agents.financial_analyst import financial_analyst_node
from agents.reviewer import reviewer_node
from agents.report_writer import report_writer_node

# 企业级模块（可选导入）
try:
    from security.input_guard import InputGuard
    from compliance.content_filter import ContentFilter
    from compliance.regulation import RegulationEngine
    from audit.audit_logger import AuditLogger, EventType, EventSeverity
    ENTERPRISE_MODE = True
except ImportError:
    ENTERPRISE_MODE = False

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 15


class FinancialAnalysisGraph:
    """金融研报分析 StateGraph"""

    def __init__(self, sqlite_path: str = "./data/sqlite/agent_state.db"):
        self.sqlite_path = sqlite_path
        os.makedirs(os.path.dirname(sqlite_path) if os.path.dirname(sqlite_path) else ".", exist_ok=True)
        self._compiled_graph = None

        # [企业级] 初始化企业组件
        self._input_guard = None
        self._content_filter = None
        self._regulation_engine = None
        self._audit_logger = None

        if ENTERPRISE_MODE:
            self._input_guard = InputGuard()
            self._content_filter = ContentFilter()
            self._regulation_engine = RegulationEngine()
            audit_storage_path = os.path.join(os.path.dirname(sqlite_path), "audit_logs")
            self._audit_logger = AuditLogger(
                enable_console=False,
                enable_file=True,
                storage_path=audit_storage_path,
            )

    def validate_input(self, user_query: str) -> Dict[str, Any]:
        """[企业级] 验证用户输入"""
        if not self._input_guard:
            return {"valid": True, "sanitized": user_query}

        result = self._input_guard.check_input(user_query)

        if not result["is_safe"]:
            logger.warning("输入安全检查失败: %s", result["threats"])
            if self._audit_logger:
                self._audit_logger.log_event(
                    event_type=EventType.AUTH_FAILURE,
                    user_id="system",
                    description="输入安全检查失败",
                    details={"threats": result["threats"]},
                    severity=EventSeverity.WARNING,
                )

        return {
            "valid": result["is_safe"],
            "sanitized": result["sanitized_input"],
            "threats": result["threats"],
        }

    def filter_output(self, content: str) -> Dict[str, Any]:
        """[企业级] 过滤输出内容（RegulationEngine 规则拦截 + ContentFilter 脱敏）"""
        if not self._content_filter:
            return {"valid": True, "filtered": content}

        # 第一层：RegulationEngine 确定性规则检查（违规即拦截）
        if self._regulation_engine:
            reg_result = self._regulation_engine.check_compliance(content)
            if not reg_result.passed:
                logger.warning("监管规则违规: %s", reg_result.violations)
                if self._audit_logger:
                    self._audit_logger.log_compliance_event(
                        user_id="system",
                        check_type="regulation_check",
                        result="violation",
                        violations=reg_result.violations,
                    )
                return {
                    "valid": False,
                    "filtered": content,
                    "violations": reg_result.violations,
                    "compliance_warnings": [
                        {"rule_id": v["rule_id"], "message": v["message"]}
                        for v in reg_result.violations
                    ],
                }

        # 第二层：ContentFilter 内容脱敏（替换违规内容）
        result = self._content_filter.filter_content(content)

        if not result.passed:
            logger.warning("内容过滤发现违规: %s", result.violations)
            if self._audit_logger:
                self._audit_logger.log_compliance_event(
                    user_id="system",
                    check_type="content_filter",
                    result="violation",
                    violations=result.violations,
                )

        return {
            "valid": result.passed,
            "filtered": result.filtered_content,
            "violations": result.violations,
        }

    @staticmethod
    def _route_from_supervisor(
        state: FinancialAnalysisState,
    ) -> Literal["report_extractor", "data_retriever", "financial_analyst", "reviewer", "report_writer", "__end__"]:
        """Supervisor 条件路由"""
        next_agent = state.get("next_agent", "FINISH")

        if next_agent == "FINISH":
            return "__end__"

        if state.get("iteration_count", 0) >= MAX_ITERATIONS:
            return "__end__"

        valid_nodes = {"report_extractor", "data_retriever", "financial_analyst", "reviewer", "report_writer"}
        if next_agent not in valid_nodes:
            return "__end__"

        return next_agent  # type: ignore[return-value]

    def _build_graph(self) -> StateGraph:
        """构建 StateGraph 拓扑"""
        workflow = StateGraph(FinancialAnalysisState)

        # 注册节点
        workflow.add_node("supervisor", supervisor_node)
        workflow.add_node("report_extractor", report_extractor_node)
        workflow.add_node("data_retriever", data_retriever_node)
        workflow.add_node("financial_analyst", financial_analyst_node)
        workflow.add_node("reviewer", reviewer_node)
        workflow.add_node("report_writer", report_writer_node)

        # 入口
        workflow.set_entry_point("supervisor")

        # Supervisor -> 子Agent (条件路由)
        workflow.add_conditional_edges(
            "supervisor",
            self._route_from_supervisor,
            {
                "report_extractor": "report_extractor",
                "data_retriever": "data_retriever",
                "financial_analyst": "financial_analyst",
                "reviewer": "reviewer",
                "report_writer": "report_writer",
                "__end__": END,
            },
        )

        # 子Agent -> Supervisor
        workflow.add_edge("report_extractor", "supervisor")
        workflow.add_edge("data_retriever", "supervisor")
        workflow.add_edge("financial_analyst", "supervisor")
        workflow.add_edge("reviewer", "supervisor")
        workflow.add_edge("report_writer", "supervisor")

        return workflow

    def compile(self):
        """编译并返回可执行的 LangGraph App"""
        if self._compiled_graph is not None:
            return self._compiled_graph

        workflow = self._build_graph()

        # 创建 SqliteSaver
        try:
            import sqlite3
            conn = sqlite3.connect(self.sqlite_path, check_same_thread=False)
            saver = SqliteSaver(conn)
            logger.info("SqliteSaver 初始化成功: %s", self.sqlite_path)
        except Exception as e:
            from langgraph.checkpoint.memory import InMemorySaver
            logger.warning("SqliteSaver 初始化失败 (%s)，回退 InMemorySaver", e)
            saver = InMemorySaver()

        self._compiled_graph = workflow.compile(checkpointer=saver)
        logger.info("FinancialAnalysisGraph 编译完成 (checkpointer=%s)", type(saver).__name__)
        return self._compiled_graph

    def invoke(
        self,
        user_query: str,
        report_type: str = "company",
        pdf_path: str = "",
        thread_id: str = "default",
        user_id: str = "anonymous",
    ) -> FinancialAnalysisState:
        """[企业级] 同步执行金融分析"""
        # [企业级] 输入验证
        validation = self.validate_input(user_query)
        if not validation["valid"]:
            logger.warning("输入验证失败，使用清理后的输入")

        # [企业级] 审计日志
        start_time = time.time()
        if self._audit_logger:
            self._audit_logger.log_event(
                event_type=EventType.ANALYSIS_START,
                user_id=user_id,
                description=f"开始分析: {user_query[:50]}...",
                details={"report_type": report_type, "pdf_path": pdf_path},
            )

        compiled = self.compile()
        initial_state = create_initial_state(
            validation["sanitized"],
            report_type,
            pdf_path,
        )
        # 添加用户ID到状态
        initial_state["user_id"] = user_id

        result = compiled.invoke(initial_state, {"configurable": {"thread_id": thread_id}})

        # [企业级] 输出过滤
        final_report = result.get("final_report", "")
        if final_report:
            filter_result = self.filter_output(final_report)
            if not filter_result["valid"]:
                result["final_report"] = filter_result["filtered"]
                result["compliance_warnings"] = filter_result["violations"]

        # [企业级] 审计日志
        duration_ms = (time.time() - start_time) * 1000
        if self._audit_logger:
            self._audit_logger.log_event(
                event_type=EventType.ANALYSIS_COMPLETE,
                user_id=user_id,
                description=f"分析完成: {user_query[:50]}...",
                details={
                    "report_type": report_type,
                    "duration_ms": duration_ms,
                    "iterations": result.get("iteration_count", 0),
                },
                duration_ms=duration_ms,
            )

        return result

    def stream(
        self,
        user_query: str,
        report_type: str = "company",
        pdf_path: str = "",
        thread_id: str = "default",
        user_id: str = "anonymous",
    ):
        """[企业级] 流式执行金融分析"""
        # [企业级] 输入验证
        validation = self.validate_input(user_query)
        if not validation["valid"]:
            logger.warning("输入验证失败，使用清理后的输入")

        # [企业级] 审计日志
        if self._audit_logger:
            self._audit_logger.log_event(
                event_type=EventType.ANALYSIS_START,
                user_id=user_id,
                description=f"开始流式分析: {user_query[:50]}...",
                details={"report_type": report_type, "pdf_path": pdf_path},
            )

        compiled = self.compile()
        initial_state = create_initial_state(
            validation["sanitized"],
            report_type,
            pdf_path,
        )
        initial_state["user_id"] = user_id

        for chunk in compiled.stream(
            initial_state,
            {"configurable": {"thread_id": thread_id}},
            stream_mode="updates",
        ):
            yield chunk
