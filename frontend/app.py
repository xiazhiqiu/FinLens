"""
FinScope Streamlit 前端应用

深蓝科技感UI + LangGraph 流式打通:
- 侧边栏: PDF上传、股票代码输入、AgentStepVisualizer
- 主界面: 对话输入 + graph.stream() 流式执行 + Markdown 报告渲染

启动方式:
    streamlit run frontend/app.py
"""

import sys
import os
import re
import time
import uuid
from datetime import datetime

import streamlit as st

# 确保 src/ 在 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.financial_graph import FinancialAnalysisGraph
from graphs.state import create_initial_state
from components.step_progress import AgentStepVisualizer
from utils.config import get_settings
from utils.llm_client import is_llm_ready

# 页面配置
st.set_page_config(
    page_title="FinScope | 金融研报智能分析系统",
    page_icon=" ",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 自定义 CSS
st.markdown("""
<style>
    .stApp { background: linear-gradient(135deg, #0F172A 0%, #1E3A5F 50%, #0F172A 100%); }
    .main-header {
        background: linear-gradient(135deg, #0F172A 0%, #1E3A5F 50%, #2563EB 100%);
        padding: 1.5rem 2rem; border-radius: 12px;
        border: 1px solid rgba(37, 99, 235, 0.3); margin-bottom: 1.5rem;
    }
    .main-header h1 { font-size: 1.8rem; font-weight: 700; color: #F1F5F9; margin: 0; }
    .main-header .subtitle { color: #94A3B8; font-size: 0.9rem; margin-top: 0.4rem; }
    .badge { display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 0.7rem; font-weight: 600; margin-right: 6px; }
    .badge-blue { background: rgba(37, 99, 235, 0.3); color: #93C5FD; }
    .badge-gold { background: rgba(212, 175, 55, 0.2); color: #FCD34D; }
    .badge-green { background: rgba(16, 185, 129, 0.2); color: #6EE7B7; }
    .card {
        background: rgba(30, 41, 59, 0.8); border: 1px solid #334155;
        border-radius: 10px; padding: 1.2rem; margin-bottom: 1rem;
    }
    .card-title { font-size: 0.95rem; font-weight: 600; color: #F1F5F9; margin-bottom: 0.8rem; }
    .analysis-result {
        background: rgba(15, 23, 42, 0.9); border: 1px solid #334155;
        border-radius: 10px; padding: 1.5rem; color: #E2E8F0;
        font-size: 0.9rem; line-height: 1.7;
    }
    .analysis-result h1, .analysis-result h2, .analysis-result h3 {
        color: #93C5FD; border-bottom: 1px solid rgba(51, 65, 85, 0.5);
        padding-bottom: 0.4rem; margin-top: 1.2rem;
    }
    [data-testid="stSidebar"] { background: rgba(15, 23, 42, 0.95); border-right: 1px solid #334155; }
    .stButton > button {
        background: linear-gradient(135deg, #2563EB, #1E3A5F) !important;
        color: #F1F5F9 !important; border: 1px solid rgba(37, 99, 235, 0.4) !important;
        border-radius: 8px !important; font-weight: 600 !important;
    }
    .warning-box {
        background: rgba(245, 158, 11, 0.1); border: 1px solid rgba(245, 158, 11, 0.3);
        border-radius: 8px; padding: 1rem; color: #FCD34D; font-size: 0.85rem; margin-bottom: 1rem;
    }
</style>
""", unsafe_allow_html=True)


def init_session_state():
    """初始化 Streamlit session_state"""
    if "initialized" not in st.session_state:
        st.session_state.initialized = True
        st.session_state.thread_id = str(uuid.uuid4())
        st.session_state.messages = []
        st.session_state.current_report = ""
        st.session_state.execution_steps = []
        st.session_state.is_running = False

        settings = get_settings()
        st.session_state.graph = FinancialAnalysisGraph(sqlite_path=settings.SQLITE_PATH)
        st.session_state.graph.compile()


init_session_state()

# 页面头部
st.markdown("""
<div class="main-header">
    <h1>FinScope</h1>
    <p class="subtitle">基于 LangGraph 多智能体协同的金融研报智能分析系统</p>
    <div style="margin-top:0.6rem;">
        <span class="badge badge-blue">LangGraph</span>
        <span class="badge badge-gold">Supervisor</span>
        <span class="badge badge-green">MinerU</span>
        <span class="badge badge-blue">Tushare/AkShare</span>
    </div>
</div>
""", unsafe_allow_html=True)

# API Key 健康检查
if not is_llm_ready():
    st.markdown("""
    <div class="warning-box">
        <strong>LLM API Key 未配置</strong><br>
        系统将在降级模式下运行。请在 <code>.env</code> 中配置 <code>LLM_PROVIDER</code> 和对应的 API Key。
    </div>
    """, unsafe_allow_html=True)

# 侧边栏
with st.sidebar:
    st.markdown('<div style="text-align:center;margin-bottom:1rem;"><span style="font-size:1.3rem;font-weight:700;color:#F1F5F9;">FinScope</span><br><span style="font-size:0.7rem;color:#94A3B8;">v0.1.0 | Multi-Agent Financial Analysis</span></div>', unsafe_allow_html=True)
    st.divider()

    st.markdown("### 分析配置")
    analysis_type = st.selectbox(
        "分析类型",
        ["company", "industry", "macro", "strategy"],
        format_func=lambda x: {"company": "公司深度分析", "industry": "行业研究", "macro": "宏观经济分析", "strategy": "投资策略研究"}[x],
    )

    stock_code_input = st.text_input("股票代码（可选）", placeholder="例如: 600196")

    uploaded_file = st.file_uploader("上传研报PDF（可选）", type=["pdf"])

    pdf_path = ""
    if uploaded_file:
        upload_dir = "./data/uploaded"
        os.makedirs(upload_dir, exist_ok=True)
        safe_name = re.sub(r'[^\w\-_.]', '_', uploaded_file.name)
        pdf_path = os.path.join(upload_dir, safe_name)
        with open(pdf_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
        st.success(f"已上传: {safe_name}")

    st.divider()
    st.markdown("### 操作")

    col1, col2 = st.columns(2)
    with col1:
        run_btn = st.button("开始分析", type="primary", disabled=st.session_state.is_running, use_container_width=True)
    with col2:
        reset_btn = st.button("重置", disabled=st.session_state.is_running, use_container_width=True)

    if reset_btn:
        st.session_state.thread_id = str(uuid.uuid4())
        st.session_state.messages = []
        st.session_state.current_report = ""
        st.session_state.execution_steps = []
        st.rerun()

    st.divider()
    st.markdown("### 执行监控")
    viz_container = st.container()
    step_visualizer = AgentStepVisualizer(viz_container)


# 欢迎卡片
if not st.session_state.messages:
    st.markdown("""
    <div class="card">
        <div class="card-title">欢迎使用 FinScope</div>
        <p style="color:#94A3B8;font-size:0.9rem;margin:0;">
            FinScope 采用 LangGraph Supervisor 多智能体架构，自动协调 4 个专业 AI Agent 完成金融分析任务：
        </p>
        <ul style="color:#94A3B8;font-size:0.85rem;margin-top:0.5rem;">
            <li><strong>ReportExtractor</strong> - 从研报PDF抽取关键金融实体</li>
            <li><strong>DataRetriever</strong> - 检索A股数据和财务指标</li>
            <li><strong>FinancialAnalyst</strong> - 多维度深度金融分析</li>
            <li><strong>ReportWriter</strong> - 撰写结构化分析报告</li>
        </ul>
    </div>
    """, unsafe_allow_html=True)


# 聊天历史
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])


def run_analysis(user_query: str):
    """执行金融分析主流程"""
    step_visualizer.clear()
    st.session_state.execution_steps = []

    graph = st.session_state.graph

    enhanced_query = user_query
    context_parts = []
    if stock_code_input:
        context_parts.append(f"股票代码: {stock_code_input}")
    context_parts.append(f"分析类型: {analysis_type}")
    if context_parts:
        enhanced_query = f"{user_query}\n[上下文: {' | '.join(context_parts)}]"

    step_visualizer.add_step("task_decomposition", f"Supervisor 启动，分析查询: {user_query[:60]}...")

    try:
        node_count = 0
        start_time = time.time()

        for chunk in graph.stream(
            user_query=enhanced_query,
            report_type=analysis_type,
            pdf_path=pdf_path,
            thread_id=st.session_state.thread_id,
        ):
            node_count += 1

            for node_name, node_output in chunk.items():
                step_type_map = {
                    "supervisor": "supervisor_thinking",
                    "report_extractor": "report_extraction",
                    "data_retriever": "data_retrieval",
                    "financial_analyst": "financial_analysis",
                    "report_writer": "report_writing",
                }
                step_type = step_type_map.get(node_name, node_name)

                detail_parts = []
                if node_name == "supervisor":
                    next_agent = node_output.get("next_agent", "?")
                    detail_parts.append(f"指派 -> {next_agent}")
                elif node_name == "report_extractor":
                    entities = node_output.get("extracted_entities", [])
                    detail_parts.append(f"抽取 {len(entities)} 个实体")
                elif node_name == "data_retriever":
                    fin_data = node_output.get("financial_data", {})
                    detail_parts.append(f"获取 {len(fin_data)} 项数据")
                elif node_name == "financial_analyst":
                    analysis = node_output.get("analysis_result", "")
                    detail_parts.append(f"分析 {len(analysis)} 字符")
                elif node_name == "report_writer":
                    report = node_output.get("final_report", "")
                    detail_parts.append(f"报告 {len(report)} 字符")
                    st.session_state.current_report = report

                detail = " | ".join(detail_parts) if detail_parts else "执行中..."
                duration_ms = (time.time() - start_time) * 1000
                step_visualizer.add_step(step_type, detail, duration_ms=duration_ms, status="complete")

        total_time = time.time() - start_time
        step_visualizer.add_step("complete", f"全部任务完成 ({node_count} 步, {total_time:.1f}s)", status="complete")
        return True

    except Exception as e:
        error_msg = f"分析执行异常: {str(e)[:200]}"
        step_visualizer.add_step("error", error_msg, status="error")
        st.session_state.current_report = f"## 分析执行异常\n\n```\n{str(e)}\n```"
        return False


# 输入框
user_query = st.chat_input("请输入金融分析问题...")

if user_query and not st.session_state.is_running:
    st.session_state.messages.append({"role": "user", "content": user_query})
    with st.chat_message("user"):
        st.markdown(user_query)

    st.session_state.is_running = True
    with st.chat_message("assistant"):
        with st.spinner("Supervisor 正在调度 Agent 团队..."):
            success = run_analysis(user_query)

        final_report = st.session_state.current_report
        if final_report:
            st.markdown(final_report)
            st.session_state.messages.append({"role": "assistant", "content": final_report})

    st.session_state.is_running = False
    st.rerun()

if reset_btn:
    st.rerun()
