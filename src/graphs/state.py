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

    # ========== [P1-P4] 多级上下文压缩 ==========
    pdf_l1: Dict[str, Any]  # L1 结构化无损层 {sections, tables, facts}（确定性产物，落 parse_cache）
    pdf_l2: Dict[str, Any]  # L2 章节语义压缩 {section_id: {thesis, key_arguments}}（P3 惰性构建）
    pdf_l3: Dict[str, Any]  # L3 全局摘要（P3）
    pdf_context: str  # 装配产物（预算驱动），Agent 唯一消费入口（新链路）

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

    # ========== [企业级] 数据血缘 ==========
    lineage_node_id: str

    # ========== [企业级] 复核审查 ==========
    review_result: str  # pass / revise
    review_feedback: str
    review_revision_count: int
    defect_locus: str  # 缺陷归属: analysis / report / both（revise 时由 Reviewer 判定）
    prev_analysis_result: str  # revise 时备份的上一版分析（供 Analyst 增量修订）
    prev_final_report: str  # revise 时备份的上一版报告（供 Writer 增量修订）

    # ========== [企业级] 合规（确定性规则结果，Reviewer 预检产出） ==========
    compliance_violations: List[Dict[str, Any]]

    # ========== [P5] 上下文准备 + 领域架构 ==========
    chapter_map: Dict[str, int]             # E1: section_id -> 十节章节号（0 前置/99 尾注）
    domain_contexts: Dict[str, str]         # E1/E2: 领域 -> 预算装配上下文（覆盖率不足/flag关为空 dict）
    derived_metrics: List[Dict[str, Any]]   # E3: 确定性算子层产物
    cross_source_checks: List[Dict[str, Any]]  # E5: facts↔MD&A 散文对账结果
    domain_analyses: Dict[str, str]         # E2: 领域 -> 领域 agent 产出（修订精准回炉缓存）
    defect_domain: str                      # E2: Reviewer 判定的缺陷领域（'' 未定位）


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
        pdf_l1={},
        pdf_l2={},
        pdf_l3={},
        pdf_context="",
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
        lineage_node_id="",
        review_result="",
        review_feedback="",
        review_revision_count=0,
        defect_locus="",
        prev_analysis_result="",
        prev_final_report="",
        compliance_violations=[],
        chapter_map={},
        domain_contexts={},
        derived_metrics=[],
        cross_source_checks=[],
        domain_analyses={},
        defect_domain="",
    )
