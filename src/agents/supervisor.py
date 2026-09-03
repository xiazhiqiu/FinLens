"""
FinScope Supervisor 调度 Agent

负责:
1. 读取当前状态，分析进度
2. 通过 LLM 决策下一个应执行的子 Agent
3. 硬熔断保护（iteration_count >= 15 → 强制 FINISH）
4. 单 Agent 连续调用检测（防止同 Agent 死循环）
5. [企业级] 安全检查 + 审计日志
"""

import json
import logging
import time
from typing import Dict, Any

from graphs.state import FinancialAnalysisState
from utils.llm_client import safe_invoke, is_llm_ready
from utils.config import get_settings

# 企业级模块（可选导入）
try:
    from security.auth import JWTAuth
    from security.input_guard import InputGuard
    from audit.audit_logger import AuditLogger, EventType, EventSeverity, get_audit_logger
    ENTERPRISE_MODE = True
except ImportError:
    ENTERPRISE_MODE = False

logger = logging.getLogger(__name__)

# 子 Agent 优先级顺序（LLM不可用时的规则回退）
AGENT_PRIORITY_ORDER = [
    "report_extractor",
    "data_retriever",
    "financial_analyst",
    "reviewer",
    "report_writer",
]

MAX_ITERATIONS = get_settings().MAX_AGENT_ITERATIONS
MAX_CONSECUTIVE_CALLS = get_settings().SINGLE_AGENT_MAX_CALLS

# 企业级组件（全局单例）
_input_guard = None


def _get_audit_logger():
    """获取全进程共享的审计日志单例"""
    return get_audit_logger() if ENTERPRISE_MODE else None


def _get_input_guard():
    """获取输入防护器"""
    global _input_guard
    if _input_guard is None and ENTERPRISE_MODE:
        _input_guard = InputGuard()
    return _input_guard


def supervisor_node(state: FinancialAnalysisState) -> Dict[str, Any]:
    """Supervisor 调度节点"""
    iteration = state.get("iteration_count", 0)
    agent_status = dict(state.get("agent_status", {}))
    error_log = list(state.get("error_log", []))
    user_id = state.get("user_id", "anonymous")

    # [企业级] 审计日志 - 记录调度开始
    audit_logger = _get_audit_logger()
    start_time = time.time()

    if audit_logger:
        audit_logger.log_event(
            event_type=EventType.ANALYSIS_START,
            user_id=user_id,
            description=f"Supervisor 调度开始 (iter={iteration})",
            details={"iteration": iteration, "agent_status": agent_status},
        )

    # 硬熔断保护
    if iteration >= MAX_ITERATIONS:
        logger.warning("硬熔断触发: iteration=%d >= %d", iteration, MAX_ITERATIONS)
        error_log.append(f"[硬熔断] 迭代次数达到上限 ({MAX_ITERATIONS})，强制终止")

        if audit_logger:
            audit_logger.log_event(
                event_type=EventType.SYSTEM_ERROR,
                user_id=user_id,
                description="硬熔断触发",
                details={"iteration": iteration, "max_iterations": MAX_ITERATIONS},
                severity=EventSeverity.WARNING,
            )

        agent_status["supervisor"] = "done"
        return {
            "next_agent": "FINISH",
            "iteration_count": iteration + 1,
            "agent_status": agent_status,
            "error_log": error_log,
        }

    # 收集已完成 Agent
    completed_agents = [
        name for name, status in agent_status.items()
        if status == "done" and name != "supervisor"
    ]

    # 检测单Agent连续调用
    agent_call_history = state.get("agent_call_history", [])
    if len(agent_call_history) >= MAX_CONSECUTIVE_CALLS:
        last_n = agent_call_history[-MAX_CONSECUTIVE_CALLS:]
        if len(set(last_n)) == 1:
            stuck_agent = last_n[0]
            logger.warning("Agent '%s' 已连续调用 %d 次，强制跳过", stuck_agent, MAX_CONSECUTIVE_CALLS)
            error_log.append(f"[重复调度预警] {stuck_agent} 已连续调用 {MAX_CONSECUTIVE_CALLS} 次")

    # 拼装上下文
    context = "\n".join([
        f"用户查询: {state.get('user_query', '未提供')}",
        f"研报类型: {state.get('report_type', 'company')}",
        f"当前迭代: {iteration}/{MAX_ITERATIONS}",
        f"已完成Agent: {completed_agents if completed_agents else '无'}",
        f"已抽取实体数: {len(state.get('extracted_entities', []))}",
        f"已获取财务数据: {'有' if state.get('financial_data') else '无'}",
        f"分析结果: {'已完成' if state.get('analysis_result') else '未完成'}",
        f"报告: {'已完成' if state.get('final_report') else '未完成'}",
        f"审查结果: {state.get('review_result', '无')}",
        f"缺陷归属: {state.get('defect_locus') or '无'}",
        f"合规违规数: {len(state.get('compliance_violations', []))}",
    ])

    # LLM 决策
    system_prompt = f"""你是 FinScope 的 Supervisor（总调度Agent）。

## 你的职责
分析当前进度，合理指派下一个子Agent执行任务。

## 可用子Agent
| Agent名称 | 职责 | 触发条件 |
|-----------|------|---------|
| report_extractor | 从PDF研报中抽取关键金融实体 | 有研报需解析且尚未抽取 |
| data_retriever | 检索A股/宏观金融数据 | 需要股票信息或财务指标 |
| financial_analyst | 深度金融分析 | 已有足够数据待分析 |
| report_writer | 撰写结构化分析报告 | 分析已完成待输出报告 |
| reviewer | 审查最终报告质量（数据一致性/过度断言/维度完整） | 报告完成后审查 |

## 决策原则
1. 按逻辑顺序: 先抽取 → 再检索 → 再分析 → 再撰写 → 最后审查
2. 如果某一阶段已完成，直接跳到下一阶段
3. 报告完成后必须审查（reviewer）
4. 审查返回 pass → FINISH
5. 审查返回 revise 时，按缺陷归属（defect_locus）精准路由返工:
   - defect_locus = "report" → 直接回 report_writer 修订（分析结论没问题，不要重跑分析）
   - defect_locus = "analysis" 或 "both" → 回 financial_analyst 修订，完成后再回 report_writer 重写
6. 返工完成后必须重新审查（reviewer）
7. 如果所有阶段已完成，立即 FINISH

## 当前进度
{context}

## 输出格式（严格要求）
仅输出一个 JSON 对象:
{{"next_agent": "report_extractor|data_retriever|financial_analyst|reviewer|report_writer|FINISH", "reason": "一句话决策理由"}}
"""

    if is_llm_ready():
        result = safe_invoke(system_prompt, "请做出下一步决策。")
        response_text = result.get("content", "") if not result.get("error") else ""
    else:
        response_text = ""

    # 解析 LLM 输出
    next_agent = "FINISH"
    reason = ""

    if response_text:
        try:
            # 去除 markdown code fence
            if "```json" in response_text:
                start = response_text.find("```json") + 7
                end = response_text.find("```", start)
                if end > start:
                    response_text = response_text[start:end].strip()
            elif "```" in response_text:
                start = response_text.find("```") + 3
                end = response_text.find("```", start)
                if end > start:
                    response_text = response_text[start:end].strip()

            decision = json.loads(response_text)
            next_agent = decision.get("next_agent", "FINISH")
            reason = decision.get("reason", "")
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Supervisor JSON 解析失败: %s", e)
    else:
        # 规则回退
        # [修订路由] LLM 不可用时，revise 仍按缺陷归属返工（不受 agent_status=done 影响）
        if state.get("review_result") == "revise":
            locus = state.get("defect_locus", "both")
            history = state.get("agent_call_history", [])
            try:
                last_review_idx = len(history) - 1 - history[::-1].index("reviewer")
            except ValueError:
                last_review_idx = -1
            rerun_since_review = history[last_review_idx + 1:]

            if "report_writer" in rerun_since_review:
                next_agent = "reviewer"  # 修订已完成，重新审查
            elif locus in ("analysis", "both") and "financial_analyst" not in rerun_since_review:
                next_agent = "financial_analyst"
            else:
                next_agent = "report_writer"
            reason = f"规则回退: revise(locus={locus}) → {next_agent}"
        else:
            for agent_name in AGENT_PRIORITY_ORDER:
                if agent_status.get(agent_name) != "done":
                    next_agent = agent_name
                    reason = f"LLM不可用，规则回退 → {agent_name}"
                    break

    # 验证合法性
    valid_targets = AGENT_PRIORITY_ORDER + ["FINISH", "supervisor"]
    if next_agent not in valid_targets:
        next_agent = "FINISH"
        reason = "非法Agent名称，已回退终止"

    # 检查是否应跳过卡住的Agent
    if next_agent != "FINISH":
        new_call_history = agent_call_history + [next_agent]
        if len(new_call_history) >= MAX_CONSECUTIVE_CALLS:
            last_n = new_call_history[-MAX_CONSECUTIVE_CALLS:]
            if len(set(last_n)) == 1:
                error_log.append(f"[强制终止] {next_agent} 连续调度达到上限")
                next_agent = "FINISH"
                reason = f"{last_n[0]} 连续调度达到上限，强制终止"
    else:
        new_call_history = agent_call_history

    # 更新状态
    agent_status["supervisor"] = "done"
    if next_agent != "FINISH":
        agent_status[next_agent] = "running"

    logger.info("Supervisor: iter=%d → %s (reason: %s)", iteration, next_agent, reason[:80])

    # [企业级] 审计日志 - 记录调度决策
    duration_ms = (time.time() - start_time) * 1000
    if audit_logger:
        audit_logger.log_event(
            event_type=EventType.ANALYSIS_COMPLETE,
            user_id=user_id,
            description=f"Supervisor 决策: {next_agent}",
            details={
                "iteration": iteration,
                "next_agent": next_agent,
                "reason": reason,
                "completed_agents": completed_agents,
            },
            duration_ms=duration_ms,
        )

    return {
        "next_agent": next_agent,
        "iteration_count": iteration + 1,
        "agent_status": agent_status,
        "error_log": error_log,
        "agent_call_history": new_call_history,
    }
