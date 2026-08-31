"""
FinScope 金融分析状态定义

FinancialAnalysisState 是所有 Agent 节点共享的状态对象，
通过 LangGraph StateGraph 的 TypedDict 机制自动完成节点间的状态传递与合并。

设计原则:
1. messages 字段使用 add_messages reducer，自动追加而非覆盖
2. 每个金融专有字段有明确的读写 Agent 约定
3. 流程控制字段（iteration_count, agent_status）用于防死循环和监控

[企业级] 新增字段:
- user_id: 用户身份标识
- compliance_warnings: 合规警告
"""

from typing import Annotated, TypedDict, List, Dict, Any
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class FinancialAnalysisState(TypedDict):
    """
    金融研报分析系统共享状态

    所有 Agent 节点函数接收此 State，返回部分更新的 State 字典。
    LangGraph 根据每个字段的 Annotated reducer 自动合并更新。

    字段分组:
    - 基础消息流: messages
    - 用户输入: user_query, report_type, user_id
    - Agent 输出: extracted_entities, financial_data, analysis_result, final_report
    - 检索上下文: retrieved_docs, tool_call_history
    - 流程控制: iteration_count, next_agent, agent_status, error_log
    - [企业级] 合规: compliance_warnings
    """

    # ========== 基础消息流 ==========
    messages: Annotated[List[BaseMessage], add_messages]

    # ========== 用户输入 ==========
    user_query: str
    report_type: str  # company / industry / macro / strategy
    user_id: str  # [企业级] 用户身份标识

    # ========== 各 Agent 输出 ==========
    extracted_entities: List[Dict[str, Any]]
    financial_data: Dict[str, Any]
    analysis_result: str
    final_report: str

    # ========== 检索上下文 ==========
    retrieved_docs: List[Dict[str, Any]]
    tool_call_history: List[Dict[str, Any]]

    # ========== 流程控制 ==========
    iteration_count: int
    next_agent: str
    agent_status: Dict[str, str]
    error_log: List[str]

    # ========== 调度控制 ==========
    pdf_path: str
    agent_call_history: List[str]

    # ========== [企业级] 合规 ==========
    compliance_warnings: List[Dict[str, Any]]


def create_initial_state(
    user_query: str,
    report_type: str = "company",
    pdf_path: str = "",
    user_id: str = "anonymous",
) -> FinancialAnalysisState:
    """
    创建初始化的 FinancialAnalysisState

    Args:
        user_query: 用户原始查询
        report_type: 研报类型（默认 company）
        pdf_path: PDF 文件路径（可选）
        user_id: 用户身份标识（默认 anonymous）

    Returns:
        初始化的 FinancialAnalysisState 字典
    """
    return FinancialAnalysisState(
        messages=[],
        user_query=user_query,
        report_type=report_type,
        user_id=user_id,
        extracted_entities=[],
        financial_data={},
        analysis_result="",
        final_report="",
        retrieved_docs=[],
        tool_call_history=[],
        iteration_count=0,
        next_agent="",
        agent_status={
            "supervisor": "pending",
            "report_extractor": "pending",
            "data_retriever": "pending",
            "financial_analyst": "pending",
            "report_writer": "pending",
        },
        error_log=[],
        pdf_path=pdf_path,
        agent_call_history=[],
        compliance_warnings=[],
    )
