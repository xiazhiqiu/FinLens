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
"""

import os
import logging
from typing import Literal

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

from .state import FinancialAnalysisState, create_initial_state

from agents.supervisor import supervisor_node
from agents.report_extractor import report_extractor_node
from agents.data_retriever import data_retriever_node
from agents.financial_analyst import financial_analyst_node
from agents.report_writer import report_writer_node

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 15


class FinancialAnalysisGraph:
    """金融研报分析 StateGraph"""

    def __init__(self, sqlite_path: str = "./data/sqlite/agent_state.db"):
        self.sqlite_path = sqlite_path
        os.makedirs(os.path.dirname(sqlite_path) if os.path.dirname(sqlite_path) else ".", exist_ok=True)
        self._compiled_graph = None

    @staticmethod
    def _route_from_supervisor(
        state: FinancialAnalysisState,
    ) -> Literal["report_extractor", "data_retriever", "financial_analyst", "report_writer", "__end__"]:
        """Supervisor 条件路由"""
        next_agent = state.get("next_agent", "FINISH")

        if next_agent == "FINISH":
            return "__end__"

        if state.get("iteration_count", 0) >= MAX_ITERATIONS:
            return "__end__"

        valid_nodes = {"report_extractor", "data_retriever", "financial_analyst", "report_writer"}
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
                "report_writer": "report_writer",
                "__end__": END,
            },
        )

        # 子Agent -> Supervisor
        workflow.add_edge("report_extractor", "supervisor")
        workflow.add_edge("data_retriever", "supervisor")
        workflow.add_edge("financial_analyst", "supervisor")
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
    ) -> FinancialAnalysisState:
        """同步执行金融分析"""
        compiled = self.compile()
        initial_state = create_initial_state(user_query, report_type, pdf_path)
        result = compiled.invoke(initial_state, {"configurable": {"thread_id": thread_id}})
        return result

    def stream(
        self,
        user_query: str,
        report_type: str = "company",
        pdf_path: str = "",
        thread_id: str = "default",
    ):
        """流式执行金融分析（yield 每个节点的状态更新）"""
        compiled = self.compile()
        initial_state = create_initial_state(user_query, report_type, pdf_path)
        for chunk in compiled.stream(
            initial_state,
            {"configurable": {"thread_id": thread_id}},
            stream_mode="updates",
        ):
            yield chunk
