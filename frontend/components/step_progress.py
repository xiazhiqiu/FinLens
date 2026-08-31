"""
AgentStepVisualizer 组件 — Agent执行步骤实时可视化

在 Streamlit 侧边栏中以时间线形式展示:
- Supervisor → 子Agent → 工具调用的完整执行链路
- 每步的状态（运行中/成功/失败）、耗时、详情
"""

import streamlit as st
from datetime import datetime
from typing import List, Dict, Optional


class AgentStepVisualizer:
    """Agent 执行过程实时可视化组件"""

    STEPS_CONFIG: Dict[str, Dict[str, str]] = {
        "supervisor_thinking": {"icon": "🧠", "label": "Supervisor 决策中", "color": "#7C3AED"},
        "task_decomposition": {"icon": "📋", "label": "任务拆解", "color": "#2563EB"},
        "report_extraction": {"icon": "📄", "label": "研报信息抽取", "color": "#059669"},
        "data_retrieval": {"icon": "🔍", "label": "金融数据检索", "color": "#D97706"},
        "financial_analysis": {"icon": "📊", "label": "金融深度分析", "color": "#DC2626"},
        "report_writing": {"icon": "✍️", "label": "分析报告撰写", "color": "#7C3AED"},
        "tool_execution": {"icon": "🔧", "label": "工具调用", "color": "#6B7280"},
        "error": {"icon": "❌", "label": "执行异常", "color": "#EF4444"},
        "complete": {"icon": "✅", "label": "任务完成", "color": "#10B981"},
        "cutoff": {"icon": "⚠️", "label": "熔断终止", "color": "#F59E0B"},
    }

    STATUS_COLORS = {
        "running": "#3B82F6",
        "complete": "#10B981",
        "error": "#EF4444",
        "pending": "#9CA3AF",
    }

    def __init__(self, container):
        self.container = container
        self.steps: List[Dict] = []

    def add_step(
        self,
        step_type: str,
        detail: str = "",
        duration_ms: Optional[float] = None,
        status: str = "running",
    ):
        """添加一个执行步骤"""
        config = self.STEPS_CONFIG.get(step_type, {})
        step = {
            "type": step_type,
            "icon": config.get("icon", "▶️"),
            "label": config.get("label", step_type),
            "color": config.get("color", "#6B7280"),
            "detail": detail,
            "duration_ms": duration_ms,
            "status": status,
            "timestamp": datetime.now().strftime("%H:%M:%S.%f")[:-3],
        }
        self.steps.append(step)
        self._render()

    def update_last_step(self, status: str = "complete", detail: str = "", duration_ms: Optional[float] = None):
        """更新最后一步的状态"""
        if self.steps:
            self.steps[-1]["status"] = status
            if detail:
                self.steps[-1]["detail"] = detail
            if duration_ms is not None:
                self.steps[-1]["duration_ms"] = duration_ms
            self._render()

    def clear(self):
        """清空所有步骤"""
        self.steps.clear()
        self._render()

    def _render(self):
        """渲染完整执行时间线"""
        with self.container:
            st.markdown("### Agent 执行时间线")

            if not self.steps:
                st.caption("等待执行...")
                return

            for i, step in enumerate(self.steps):
                status_color = self.STATUS_COLORS.get(step["status"], "#9CA3AF")
                status_indicator = {
                    "running": "...",
                    "complete": "OK",
                    "error": "ERR",
                    "pending": "○",
                }.get(step["status"], "○")

                duration_str = ""
                if step["duration_ms"] is not None:
                    duration_str = f' <span style="font-size:0.75rem;color:#9CA3AF;margin-left:8px">{step["duration_ms"]:.0f}ms</span>'

                step_html = f"""
                <div style="display:flex;align-items:flex-start;margin-bottom:8px;padding:8px;border-radius:6px;background:{'#F0F9FF' if step['status'] == 'running' else '#F9FAFB'};border-left:3px solid {status_color};">
                    <span style="font-size:0.8rem;margin-right:8px;min-width:30px;color:{status_color};font-weight:bold;">{status_indicator}</span>
                    <span style="color:{step['color']};font-weight:600;font-size:0.85rem;margin-right:4px;min-width:110px;">{step['label']}</span>
                    <span style="font-size:0.8rem;color:#6B7280;flex:1;">{step['detail']}{duration_str}</span>
                    <span style="font-size:0.7rem;color:#D1D5DB;margin-left:4px;">{step['timestamp']}</span>
                </div>
                """

                if i < len(self.steps) - 1:
                    step_html += f'<div style="margin-left:18px;width:2px;height:4px;background:{status_color};opacity:0.3;"></div>'

                st.markdown(step_html, unsafe_allow_html=True)

            completed = sum(1 for s in self.steps if s["status"] == "complete")
            errors = sum(1 for s in self.steps if s["status"] == "error")
            st.caption(f"共 {len(self.steps)} 步 | OK {completed} | {'ERR ' + str(errors) if errors else ''}")
