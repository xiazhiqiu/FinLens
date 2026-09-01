"""
FinScope Reviewer Agent

负责:
1. 审查 financial_analyst 的分析结果
2. 检查数据-结论一致性、过度断言、维度完整性
3. 输出 pass/revise 判定 + 修改建议
4. [企业级] 审计日志记录审查结果
"""

import json
import logging
from typing import Dict, Any, List

from graphs.state import FinancialAnalysisState
from utils.llm_client import safe_invoke, is_llm_ready

# 企业级模块（可选导入）
try:
    from audit.audit_logger import AuditLogger, EventType
    ENTERPRISE_MODE = True
except ImportError:
    ENTERPRISE_MODE = False

logger = logging.getLogger(__name__)

# 数据血缘全局单例
_audit_logger = None


def _get_audit_logger() -> "AuditLogger":
    """获取审计日志单例"""
    global _audit_logger
    if _audit_logger is None and ENTERPRISE_MODE:
        _audit_logger = AuditLogger(enable_console=False, enable_file=True)
    return _audit_logger


MAX_REVIEW_REVISIONS = 2


def _build_review_input(state: FinancialAnalysisState) -> str:
    """拼装审查输入上下文"""
    parts = []

    parts.append(f"## 用户查询\n{state.get('user_query', '未提供')}")

    entities = state.get("extracted_entities", [])
    if entities:
        parts.append(f"## 研报抽取实体（共 {len(entities)} 个）")
        for e in entities[:20]:
            et = e.get("entity_type", "unknown")
            en = e.get("entity_name", "")
            ev = e.get("entity_value", "")
            if ev:
                parts.append(f"  - [{et}] {en} = {ev}")
            else:
                parts.append(f"  - [{et}] {en}")

    fin_data = state.get("financial_data", {})
    if fin_data:
        parts.append("## 公开市场数据")
        parts.append(json.dumps(fin_data, ensure_ascii=False, indent=2)[:3000])
    else:
        parts.append("## 公开市场数据\n（无）")

    analysis = state.get("analysis_result", "")
    if analysis:
        parts.append(f"## 待审查分析结果\n{analysis}")
    else:
        parts.append("## 待审查分析结果\n（空）")

    # 如果有历史审查反馈（修订场景）
    review_feedback = state.get("review_feedback", "")
    if review_feedback:
        parts.append(f"## 上一轮审查反馈\n{review_feedback}")

    return "\n\n".join(parts)


def reviewer_node(state: FinancialAnalysisState) -> Dict[str, Any]:
    """复核审查节点"""
    agent_status = dict(state.get("agent_status", {}))
    error_log = list(state.get("error_log", []))
    review_revision_count = state.get("review_revision_count", 0)

    # 熔断：修订次数超限
    if review_revision_count >= MAX_REVIEW_REVISIONS:
        logger.warning("审查修订次数达到上限 (%d)，强制通过", MAX_REVIEW_REVISIONS)
        agent_status["reviewer"] = "done"
        return {
            "review_result": "pass",
            "review_feedback": f"修订次数达到上限 ({MAX_REVIEW_REVISIONS})，强制通过",
            "agent_status": agent_status,
        }

    analysis = state.get("analysis_result", "")
    if not analysis or "分析不可用" in analysis or "分析失败" in analysis:
        logger.warning("分析结果为空或失败，跳过审查")
        agent_status["reviewer"] = "done"
        return {
            "review_result": "pass",
            "review_feedback": "分析结果为空，跳过审查",
            "agent_status": agent_status,
        }

    review_input = _build_review_input(state)

    system_prompt = f"""你是 FinScope 的复核审查员（Reviewer），负责审查金融分析报告的质量。

## 你的职责
审查 analyst 的分析结果，确保：
1. **数据-结论一致性**: 每个结论引用的数字必须在提供的数据中存在
2. **无过度断言**: 不出现"必然"、"100%"、"保证"等绝对化表述
3. **维度完整性**: 5个分析维度（基本面/财务/行业/风险/建议）是否都有覆盖
4. **数据依赖声明**: 数据缺失时是否标注"数据不足，暂不评价"

## 输出格式（严格 JSON）
{{
  "verdict": "pass 或 revise",
  "issues_found": ["问题1", "问题2"],
  "feedback": "如果 revise，给出具体修改建议；如果 pass，输出空字符串"
}}

## 待审查内容
{review_input}
"""

    if not is_llm_ready():
        logger.warning("LLM 不可用，跳过审查直接通过")
        agent_status["reviewer"] = "done"
        return {
            "review_result": "pass",
            "review_feedback": "LLM 不可用，跳过审查",
            "agent_status": agent_status,
        }

    result = safe_invoke(system_prompt, "请审查分析结果质量。")

    if result.get("error"):
        error_log.append(f"[Reviewer] LLM 调用失败: {result.get('message', '未知错误')}")
        agent_status["reviewer"] = "done"
        return {
            "review_result": "pass",
            "review_feedback": f"审查失败: {result.get('message', '未知错误')}",
            "agent_status": agent_status,
            "error_log": error_log,
        }

    response_text = result.get("content", "")

    # 解析审查结果
    verdict = "pass"
    feedback = ""
    issues_found = []

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

            review = json.loads(response_text)
            verdict = review.get("verdict", "pass")
            feedback = review.get("feedback", "")
            issues_found = review.get("issues_found", [])
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Reviewer JSON 解析失败: %s，默认通过")
            verdict = "pass"

    # 记录审查结果到审计日志
    audit_logger = _get_audit_logger()
    if audit_logger:
        user_id = state.get("user_id", "anonymous")
        audit_logger.log_event(
            event_type=EventType.ANALYSIS_COMPLETE,
            user_id=user_id,
            description=f"Agent 审查: {verdict}",
            details={
                "verdict": verdict,
                "issues_count": len(issues_found),
                "revision_count": review_revision_count,
            },
        )

    logger.info("Reviewer 审查完成: verdict=%s, issues=%d", verdict, len(issues_found))
    agent_status["reviewer"] = "done"

    return {
        "review_result": verdict,
        "review_feedback": feedback,
        "review_revision_count": review_revision_count + (1 if verdict == "revise" else 0),
        "agent_status": agent_status,
        "error_log": error_log,
    }
