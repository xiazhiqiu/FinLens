"""
FinScope Reviewer Agent

负责:
1. 审查 report_writer 的最终报告
2. 检查数据-结论一致性、过度断言、维度完整性
3. 输出 pass/revise 判定 + 修改建议
4. [企业级] 审计日志记录审查结果
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from graphs.state import FinancialAnalysisState
from utils.config import get_settings
from utils.llm_client import safe_invoke, safe_invoke_with_tools, is_llm_ready

# 企业级模块（可选导入）
try:
    from audit.audit_logger import AuditLogger, EventType, get_audit_logger
    ENTERPRISE_MODE = True
except ImportError:
    ENTERPRISE_MODE = False

# [企业级] 合规规则引擎（可选导入，确定性预检，无 LLM 参与）
try:
    from compliance.regulation import RegulationEngine
    _regulation_engine = RegulationEngine()
except ImportError:
    _regulation_engine = None

logger = logging.getLogger(__name__)


def _parse_review_json(text: str):
    """
    [C组修复] 解析 Reviewer LLM 输出为 dict；失败返回 None（不抛异常）。
    容错: markdown code fence / 元评论前缀（取首 { 到末 } 切片）。
    """
    if not text:
        return None
    s = text
    try:
        if "```json" in s:
            start = s.find("```json") + 7
            end = s.find("```", start)
            if end > start:
                s = s[start:end].strip()
        elif "```" in s:
            start = s.find("```") + 3
            end = s.find("```", start)
            if end > start:
                s = s[start:end].strip()
        if "{" in s and "}" in s:
            s = s[s.find("{"): s.rfind("}") + 1]
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return None


class ReviewVerdict(BaseModel):
    """Reviewer 判定 schema（重试轮经 function calling 强约束，模型侧保证结构合法）"""
    verdict: Literal["pass", "revise"] = Field(description="审查判定: pass 或 revise")
    defect_locus: Literal["analysis", "report", "both"] = Field(
        description="缺陷归属: analysis(分析层) / report(呈现层) / both")
    defect_domain: Literal["", "overview", "operating", "financial", "governance", "events"] = Field(
        default="",
        description="缺陷领域（E2 领域模式下 Analyst 精准回炉；非领域模式或无法定位为空字符串）",
    )
    issues_found: List[str] = Field(default_factory=list, description="发现的问题清单")
    feedback: str = Field(default="", description="revise 时给出逐条可执行的修改建议，pass 时为空字符串")


def _schema_retry(system_prompt: str, user_message: str) -> Optional[Dict[str, Any]]:
    """
    [C组] schema 强约束重试: with_structured_output 走 function calling 通道，
    ReviewVerdict schema（字段/类型/枚举）由模型侧保证——解析失败类消灭。
    与工具循环互斥（function calling 通道被 schema 占用），故仅在无需再调工具的重试轮使用。
    失败返回 None（交给可见降级路径）。
    """
    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        from utils.llm_client import get_llm

        structured = get_llm().with_structured_output(ReviewVerdict)
        obj = structured.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ])
        if obj is None:
            return None
        return {
            "verdict": obj.verdict,
            "defect_locus": obj.defect_locus,
            "defect_domain": obj.defect_domain,
            "issues_found": obj.issues_found,
            "feedback": obj.feedback,
        }
    except Exception as e:
        logger.warning("schema 强约束重试失败: %s", str(e)[:150])
        return None


def _get_audit_logger() -> "AuditLogger":
    """获取全进程共享的审计日志单例"""
    return get_audit_logger() if ENTERPRISE_MODE else None


MAX_REVIEW_REVISIONS = 2

# [A2] 数字核验职责 + 工具指南（flag 开启时注入 prompt）
_FACT_CHECK_GUIDE = """## 数字核验职责（本次审查的核心能力）
报告中的关键财务数字必须与年报事实表核对，不许只看文字通顺：
1. 用 query_fact(company, metric, period) 查结构化事实（结果带页码/表格溯源）
2. 数字不符 → issues 必须写明「报告值 X vs 事实表值 Y [溯源]」，verdict=revise
3. 事实表未命中（科目别名差异）→ 用 search_section 找对应章节，fetch_context 取原文核对
4. 报告的页码引用 [P n] 必须与工具返回的溯源页码一致，不一致视为缺陷

## 可用工具（查证用，每次调用结果都有审计留痕）
- fetch_context(scope): 章节/页码原文调取（scope 如 "s_012" 或 "p12-14"）——校验页码引用真实性
- query_fact(company, metric, period): 精确查结构化事实（三参数均可省略过滤）
- search_section(query): 章节关键词检索"""


def _run_compliance_precheck(final_report: str) -> List[Dict[str, Any]]:
    """
    [企业级] 确定性合规预检（正则规则，无 LLM 参与）

    只负责"判定"，不负责"改写"——脱敏改写仍由终态闸门执行。
    返回 violations 列表；CRITICAL 级违规将强制 revise（不可被 LLM 判 pass 覆盖）。
    """
    if not final_report or _regulation_engine is None:
        return []
    try:
        result = _regulation_engine.check_compliance(final_report)
        return result.violations or []
    except Exception as e:
        logger.warning("合规预检异常（不影响主流程）: %s", str(e)[:100])
        return []


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

    # 审查最终报告（而非分析结果）
    final_report = state.get("final_report", "")
    if final_report:
        parts.append(f"## 待审查最终报告\n{final_report[:5000]}")
    else:
        parts.append("## 待审查最终报告\n（空）")

    # [P5-E5] 跨源核对告警（ContextPreparator 确定性对账产出，供审查裁决）
    cross_checks = state.get("cross_source_checks") or []
    if cross_checks:
        from extractors.cross_checker import render_cross_warnings
        warn_text = render_cross_warnings(cross_checks)
        if warn_text:
            parts.append(warn_text)

    # 如果有历史审查反馈（修订场景）
    review_feedback = state.get("review_feedback", "")
    if review_feedback:
        parts.append(f"## 上一轮审查反馈\n{review_feedback}")

    return "\n\n".join(parts)


def reviewer_node(state: FinancialAnalysisState) -> Dict[str, Any]:
    """复核审查节点（审查最终报告 + 确定性合规预检）"""
    agent_status = dict(state.get("agent_status", {}))
    error_log = list(state.get("error_log", []))
    review_revision_count = state.get("review_revision_count", 0)

    final_report = state.get("final_report", "")

    # [企业级] 确定性合规预检（先于 LLM，违规记录随状态流转到终态闸门）
    rule_violations = _run_compliance_precheck(final_report)
    critical_count = sum(1 for v in rule_violations if v.get("severity") == "critical")
    violation_lines = (
        "\n".join(
            f"- [{v.get('rule_id', '?')}/{v.get('severity', '?')}] {v.get('message', '')}"
            for v in rule_violations
        )
        if rule_violations else "（无）"
    )

    # 熔断：修订次数超限（质量可放行，但合规违规仍随状态交给终态闸门处置）
    if review_revision_count >= MAX_REVIEW_REVISIONS:
        logger.warning("审查修订次数达到上限 (%d)，强制通过", MAX_REVIEW_REVISIONS)
        agent_status["reviewer"] = "done"
        return {
            "review_result": "pass",
            "review_feedback": f"修订次数达到上限 ({MAX_REVIEW_REVISIONS})，强制通过",
            "compliance_violations": rule_violations,
            "agent_status": agent_status,
        }

    if not final_report or "分析不可用" in final_report or "分析失败" in final_report:
        logger.warning("最终报告为空或失败，跳过审查")
        agent_status["reviewer"] = "done"
        return {
            "review_result": "pass",
            "review_feedback": "最终报告为空，跳过审查",
            "compliance_violations": rule_violations,
            "agent_status": agent_status,
        }

    review_input = _build_review_input(state)

    # [A2] flag 开启且事实表就绪 → 挂工具做数字核验；关闭走原纯文本审查（防回归）
    settings = get_settings()
    l1 = state.get("pdf_l1") or {}
    use_multilevel = settings.USE_MULTILEVEL_COMPRESSION and bool(l1.get("facts"))

    fact_check_section = (_FACT_CHECK_GUIDE + "\n\n") if use_multilevel else ""

    system_prompt = f"""你是 FinScope 的复核审查员（Reviewer），负责审查最终投资分析报告的质量。

## 你的职责
审查报告的质量，确保：
1. **数据-结论一致性**: 每个结论引用的数字必须在提供的数据中存在
2. **无过度断言**: 不出现"必然"、"100%"、"保证"等绝对化表述
3. **维度完整性**: 报告是否覆盖了核心内容（公司概况/财务/行业/风险/建议）
4. **数据依赖声明**: 数据缺失时是否标注"数据不足"

{fact_check_section}## 合规预检结果（确定性规则引擎产出，不可辩驳的硬约束）
以下违规由正则规则引擎检出，你的审查意见必须逐条覆盖：
{violation_lines}

## 缺陷归属判定（defect_locus）
判定问题根因位置，供调度器精准路由返工：
- "analysis": 结论本身错误/分析维度缺失 → 需要分析 Agent 修订后再重写报告
- "report": 仅呈现层问题（免责声明/来源标注/措辞/结构/合规用语） → 只需写作 Agent 修订
- "both": 两类问题同时存在

## 缺陷领域判定（defect_domain，领域架构下供分析组精准回炉）
报告问题所属领域: 财务数据/指标错误 → "financial"；经营/业务分析缺陷 → "operating"；
治理/ESG 相关 → "governance"；重要事项/股东信息 → "events"；公司概况/风险提示 → "overview"；
跨领域或无法定位 → ""

## 输出格式（严格 JSON）
{{
  "verdict": "pass 或 revise",
  "defect_locus": "analysis 或 report 或 both",
  "defect_domain": "financial 或 operating 或 governance 或 events 或 overview 或 空字符串",
  "issues_found": ["问题1", "问题2"],
  "feedback": "如果 revise，给出逐条可执行的修改建议；如果 pass，输出空字符串"
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
            "compliance_violations": rule_violations,
            "agent_status": agent_status,
        }

    tool_call_history = list(state.get("tool_call_history", []))

    tools = None
    _audit = None
    if use_multilevel:
        # [A2] 有界工具循环：关键数字 query_fact 核对 + 页码引用真实性校验（审计留痕）
        from agents.context_tools import build_context_tools

        def _audit(tool_name: str, args: Dict[str, Any], result: str) -> None:
            tool_call_history.append({
                "agent": "reviewer",
                "tool": tool_name,
                "args": args,
                "ts": datetime.now().isoformat(timespec="seconds"),
                "result_len": len(result or ""),
            })

        tools = build_context_tools(l1, on_tool_call=_audit)

    def _invoke_review(user_msg: str) -> Dict[str, Any]:
        if tools:
            return safe_invoke_with_tools(
                system_prompt, user_msg, tools,
                max_rounds=settings.MAX_TOOL_ROUNDS_PER_AGENT,
                on_tool_call=_audit,
            )
        return safe_invoke(system_prompt, user_msg)

    result = _invoke_review("请审查报告质量；关键数字必须用工具核对事实表后再下结论。")

    if result.get("error"):
        error_log.append(f"[Reviewer] LLM 调用失败: {result.get('message', '未知错误')}")
        agent_status["reviewer"] = "done"
        return {
            # [C组修复] 审查未发生不得静默放行: pass 但显式降级标注（终态闸门/用户可见）
            "review_result": "pass",
            "review_feedback": (
                f"⚠ 审查降级：Reviewer LLM 调用失败，本报告未经过审查，请人工复核。"
                f"审查失败原因: {result.get('message', '未知错误')}"
            ),
            "compliance_violations": rule_violations,
            "agent_status": agent_status,
            "error_log": error_log,
            "tool_call_history": tool_call_history,
        }

    # 解析审查结果（[C组修复] 失败重试一次，仍失败保守降级——绝不静默当通过）
    review = _parse_review_json(result.get("content", ""))
    if review is None and result.get("content"):
        # 重试走 schema 强约束通道（首轮数字核验已完成，重试无需再调工具）
        logger.warning("Reviewer JSON 解析失败，schema 强约束重试一次")
        review = _schema_retry(
            system_prompt,
            "请重新审查并给出最终判定。注意：issues_found 中数字不符必须写明"
            "「报告值 X vs 事实表值 Y [溯源]」。",
        )

    if review is not None:
        verdict = review.get("verdict", "pass")
        feedback = review.get("feedback", "")
        issues_found = review.get("issues_found", [])
        defect_locus = review.get("defect_locus", "both")
        defect_domain = review.get("defect_domain", "")
    else:
        # [C组修复] 两轮均不可解析 → 保守降级: 不烧修订轮数（问题不在 Writer），
        # 但降级必须可见可审计（feedback 前置警示 + error_log 留痕），绝不静默通过
        error_log.append("[Reviewer] 输出不可解析（含一次重试），降级放行并标注未审查")
        logger.warning("Reviewer 输出不可解析（重试后仍失败），降级放行并标注")
        verdict = "pass"
        feedback = "⚠ 审查降级：Reviewer 输出不可解析（含一次重试），审查未完成，本报告未经完整审查，请人工复核。"
        issues_found = ["Reviewer 输出不可解析，审查未完成"]
        defect_domain = ""

    # [企业级] CRITICAL 违规不可被 LLM 判 pass 覆盖（仍有修订额度时强制 revise）
    if verdict == "pass" and critical_count > 0 and review_revision_count < MAX_REVIEW_REVISIONS:
        logger.warning("合规预检发现 %d 条 CRITICAL 违规，强制 revise", critical_count)
        verdict = "revise"
        defect_locus = "report"
        critical_msg = (
            "必须修正以下合规违规（规则引擎检出，不可忽略）: "
            + "; ".join(
                f"{v.get('rule_id', '?')} {v.get('message', '')}"
                for v in rule_violations if v.get("severity") == "critical"
            )
        )
        feedback = (feedback + "\n" + critical_msg).strip()
        issues_found = list(issues_found) + [critical_msg]

    # 归一化 defect_locus / defect_domain
    if verdict == "revise":
        if defect_locus not in ("analysis", "report", "both"):
            defect_locus = "both"
        if defect_domain not in ("overview", "operating", "financial", "governance", "events"):
            defect_domain = ""
        if defect_locus == "report":
            defect_domain = ""  # 报告呈现层问题不回炉领域 agent
    else:
        defect_locus = ""
        defect_domain = ""

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
        "defect_locus": defect_locus,
        "defect_domain": defect_domain,
        "compliance_violations": rule_violations,
        # revise 时备份当前版本，供下游 Agent 增量修订（默认 reducer 是覆盖，不备份就丢了）
        "prev_analysis_result": state.get("analysis_result", "") if verdict == "revise" else state.get("prev_analysis_result", ""),
        "prev_final_report": final_report if verdict == "revise" else state.get("prev_final_report", ""),
        "agent_status": agent_status,
        "error_log": error_log,
        # [A2] 工具核验留痕（旧路径为 state 原值透传）
        "tool_call_history": tool_call_history,
    }
