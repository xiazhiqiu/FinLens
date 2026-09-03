"""
FinScope FinancialAnalyst Agent

负责:
1. 整合 extracted_entities + financial_data + pdf_context（预算装配）
2. LLM 基于结构化数据进行多维度分析
3. 强约束：所有结论必须来自已有数据，缺失时标注"数据不足"
4. 引用规范：所有结论必须标注页码来源 [P X]

[P2-P4 主链路] USE_MULTILEVEL_COMPRESSION=true 且 pdf_l1 存在时:
- 装配 pdf_context（预算驱动，不溢出）
- 挂载 fetch_context / query_fact / search_section 三工具，走有界工具循环
- 每次工具调用写 tool_call_history（银行审计留痕）
- [P5-E1] 上下文由 context_preparator 预装配（L2/L3 构建职责已移位），本节点只消费
- flag 关闭时走基础路径（无 PDF 内容，实体+市场数据；诊断降级用）
"""

import json
import logging
from datetime import datetime
from typing import Dict, Any

from graphs.state import FinancialAnalysisState
from utils.config import get_settings
from utils.llm_client import safe_invoke, is_llm_ready, safe_invoke_with_tools

logger = logging.getLogger(__name__)

ANALYSIS_DIMENSIONS = [
    "公司基本面分析",
    "财务表现分析",
    "行业地位与竞争格局",
    "潜在风险识别",
    "初步投资建议",
]

_TOOL_GUIDE = """## 可用工具（数据不足时主动调用，工具结果可溯源）
- fetch_context(scope): 章节/页码原文调取（scope 如 "s_012" 或 "p12-14"）
- query_fact(company, metric, period): 精确查结构化事实（三参数均可省略过滤）
- search_section(query): 章节关键词检索
调用规则:
1. 数字性结论优先用 query_fact 核验后再写（不许凭印象写数）
2. 需要某段详细论述时用 fetch_context 取原文
3. 每轮工具调用 ≤ 数条；能一次查清就一次查清
4. 工具结果标注的页码即引用依据 [P 页码]"""


def _build_analysis_input(state: FinancialAnalysisState) -> str:
    """拼装分析输入上下文（无 PDF / 诊断降级时的基础分析路径）"""
    parts = []

    parts.append(f"## 用户查询\n{state.get('user_query', '未提供')}")
    parts.append(f"## 研报类型\n{state.get('report_type', 'company')}")

    # 抽取的实体
    entities = state.get("extracted_entities", [])
    if entities:
        parts.append("## 研报抽取信息（来源: PDF 研报原文）")
        parts.append(f"共抽取 {len(entities)} 个金融实体:")
        for entity in entities[:30]:
            et = entity.get("entity_type", "unknown")
            en = entity.get("entity_name", "")
            ev = entity.get("entity_value", "")
            if ev:
                parts.append(f"  - [{et}] {en} = {ev}")
            else:
                parts.append(f"  - [{et}] {en}")
    else:
        parts.append("## 研报抽取信息\n（无研报数据）")

    # 公开市场数据
    fin_data = state.get("financial_data", {})
    if fin_data:
        parts.append("## 公开市场数据（来源: Tushare/AkShare）")
        parts.append(json.dumps(fin_data, ensure_ascii=False, indent=2))
    else:
        parts.append("## 公开市场数据\n（无市场数据）")

    return "\n\n".join(parts)


def _build_tool_audit_callback(tool_call_history: list):
    """工具审计回调: 每次调用写全 谁/何时/工具/参数/返回体量（银行审计留痕）"""

    def _audit(tool_name: str, args: Dict[str, Any], result: str) -> None:
        tool_call_history.append({
            "agent": "financial_analyst",
            "tool": tool_name,
            "args": args,
            "ts": datetime.now().isoformat(timespec="seconds"),
            "result_len": len(result or ""),
        })

    return _audit


def _multilevel_analyst_run(
    state: FinancialAnalysisState,
    agent_status: Dict[str, str],
    error_log: list,
    revision_mode: bool,
) -> Dict[str, Any]:
    """
    [P5-E1 起分析节点] 消费 ContextPreparator 预装配的 pdf_context + 有界工具循环。

    装配/L2/L3 构建职责已移位至 context_preparator（一次构建全链复用）；
    本函数只保留兜底: state.pdf_context 为空（Preparator 未跑/失败，如单测直接调用）
    时现场装配一次（用 state 已有的 l2/l3 缓存，不新增 LLM 构建，零副作用）。
    """
    settings = get_settings()
    l1 = state.get("pdf_l1") or {}

    from agents.context_tools import build_context_tools

    # [P5-E1] 优先消费预装配产物；空则兜底装配（不构建 L2/L3）
    pdf_context = state.get("pdf_context", "")
    if not pdf_context:
        try:
            from extractors.context_assembler import assemble
            pdf_l2 = state.get("pdf_l2") or {}
            pdf_l3 = state.get("pdf_l3") or {}
            pdf_context = assemble(
                state.get("user_query", ""), settings.CONTEXT_BUDGET_TOKENS, l1,
                l2=pdf_l2 or None, l3=(pdf_l3.get("text") or "") or None,
            )["context"]
        except Exception as e:
            error_log.append(f"[FinancialAnalyst] 兜底装配失败，回退旧路径: {str(e)[:150]}")
            return None  # 上层回退旧路径

    # 2) 组装上下文（查询/实体/市场数据 + 装配后的研报内容）
    parts = [
        f"## 用户查询\n{state.get('user_query', '未提供')}",
        f"## 研报类型\n{state.get('report_type', 'company')}",
    ]
    entities = state.get("extracted_entities", [])
    if entities:
        ent_lines = "\n".join(
            f"- [{e.get('entity_type')}] {e.get('entity_name')}"
            + (f" = {e.get('entity_value')}" if e.get("entity_value") else "")
            for e in entities[:30]
        )
        parts.append(f"## 研报抽取实体（{len(entities)} 个）\n{ent_lines}")
    fin_data = state.get("financial_data", {})
    if fin_data:
        parts.append("## 公开市场数据（Tushare/AkShare）\n" + json.dumps(fin_data, ensure_ascii=False, indent=2)[:3000])
    parts.append("## PDF 研报内容（预算装配版，装不下的章节在末尾指针中，需要时用工具调取）\n" + pdf_context)
    analysis_data = "\n\n".join(parts)

    # 3) prompt
    system_prompt = f"""你是一位资深金融行业研究员，拥有10年以上的A股研究经验。

## 你的任务
基于以下结构化数据，对标的公司进行专业深度分析。

## 分析维度（请严格按照此结构输出）
1. **公司基本面分析** - 主营业务、核心竞争力、管理层评价
2. **财务表现分析** - 营收趋势、盈利能力、偿债能力、现金流
3. **行业地位与竞争格局** - 市场份额、竞争优势、行业趋势
4. **潜在风险识别** - 经营风险、财务风险、政策风险、市场风险
5. **初步投资建议** - 基于以上分析的综合性观点

## 强制约束
- 所有结论必须基于下方数据或工具返回结果
- 数据缺失的维度，必须标注「数据不足，暂不评价」
- 严禁编造数据、指标具体数值
- 严禁给出「买入/卖出」等确定性投资建议

## 引用规范（必须遵守）
- 引用 PDF 原文数据的结论必须标注页码: [P 页码]（工具结果中的 pN/来源即页码依据）
- 未标注页码的结论将被视为无效

## 分析数据
{analysis_data}

{_TOOL_GUIDE}

## 输出格式
使用 Markdown，以 ## 开头二级标题区分各分析维度。每个关键结论后标注 [P 页码]。
"""

    if revision_mode:
        truncated_prev = (state.get("prev_analysis_result") or "")[:6000]
        system_prompt = f"""你是资深金融行业研究员（修订模式）。

## 任务背景
你此前的分析已通过复核审查，审查员指出了以下问题。请在其基础上做**针对性修订**，不要推倒重来。

## 审查反馈（必须逐条处理）
{state.get('review_feedback', '')}

## 上一版分析（修订基础）
{truncated_prev}

## 参考数据（与上轮相同，供核对，严禁编造新数据；可继续用工具核验数字）
{analysis_data}

## 修订要求
1. 只修改反馈指出的问题，未涉及部分保持原样（保留正确结论与页码引用）
2. 保持引用规范: [P 页码]
3. 输出修订后的完整分析（Markdown，保留 ## 二级标题结构）
"""

    if not is_llm_ready():
        agent_status["financial_analyst"] = "done"
        return {
            "analysis_result": "## 分析不可用\n\nLLM API Key 未配置，请在 `.env` 文件中设置。",
            "agent_status": agent_status,
            "pdf_context": pdf_context,
        }

    # 4) 有界工具循环（审计留痕 + 预算熔断）
    tool_call_history = list(state.get("tool_call_history", []))
    audit_cb = _build_tool_audit_callback(tool_call_history)
    tools = build_context_tools(l1, on_tool_call=audit_cb)
    result = safe_invoke_with_tools(
        system_prompt,
        "请基于提供的数据进行分析；数据不足时调用工具补取后再回答。",
        tools,
        max_rounds=settings.MAX_TOOL_ROUNDS_PER_AGENT,
        on_tool_call=audit_cb,
    )

    if result.get("error"):
        error_log.append(f"[FinancialAnalyst] LLM 调用失败: {result.get('message', '未知错误')}")
        agent_status["financial_analyst"] = "error"
        return {
            "analysis_result": f"## 分析失败\n\n{result.get('message', '未知错误')}",
            "agent_status": agent_status,
            "error_log": error_log,
            "pdf_context": pdf_context,
            "tool_call_history": tool_call_history,
        }

    analysis_text = result.get("content", "")
    if not analysis_text.strip():
        error_log.append("[FinancialAnalyst] LLM 返回空内容")
        agent_status["financial_analyst"] = "error"
        return {
            "analysis_result": "## 分析失败\n\nLLM 返回空内容",
            "agent_status": agent_status,
            "error_log": error_log,
            "pdf_context": pdf_context,
            "tool_call_history": tool_call_history,
        }

    n_calls = len(result.get("tool_calls") or [])
    logger.info("金融分析完成（多级链路）: %d 字符, 工具调用 %d 次", len(analysis_text), n_calls)
    agent_status["financial_analyst"] = "done"

    return {
        "analysis_result": analysis_text,
        "agent_status": agent_status,
        "error_log": error_log,
        "pdf_context": pdf_context,
        "tool_call_history": tool_call_history,
    }


def financial_analyst_node(state: FinancialAnalysisState) -> Dict[str, Any]:
    """金融深度分析节点（USE_MULTILEVEL_COMPRESSION 开启且 pdf_l1 就绪时走新链路）"""
    agent_status = dict(state.get("agent_status", {}))
    error_log = list(state.get("error_log", []))

    settings = get_settings()
    l1 = state.get("pdf_l1") or {}
    use_multilevel = settings.USE_MULTILEVEL_COMPRESSION and bool(l1.get("sections"))

    # [修订模式] 有上一版分析 + 审查反馈时，做增量修订而非无记忆重跑
    review_feedback = state.get("review_feedback", "")
    prev_analysis = state.get("prev_analysis_result", "")
    revision_mode = bool(review_feedback and prev_analysis)

    if use_multilevel:
        # [P5-E2] 领域模式: Preparator 判定十节覆盖达标（domain_contexts 非空）→ 领域 agent 组
        # （延迟导入: domain_analysts 模块级引用本模块 _TOOL_GUIDE，防循环导入）
        if settings.USE_DOMAIN_AGENTS and state.get("domain_contexts"):
            from agents.domain_analysts import run_domain_agents, synthesize_analyses
            dom = run_domain_agents(state, agent_status, error_log)
            if dom["analyses"]:
                analysis_text = synthesize_analyses(dom["analyses"], state)
                agent_status["financial_analyst"] = "done"
                return {
                    "analysis_result": analysis_text,
                    "domain_analyses": dom["analyses"],
                    "tool_call_history": list(state.get("tool_call_history", [])) + dom["tool_history"],
                    "agent_status": agent_status,
                    "error_log": error_log,
                }
            # 领域组全灭 → 回退全局路径（error_log 已留痕）

        new_result = _multilevel_analyst_run(state, agent_status, error_log, revision_mode)
        if new_result is not None:
            return new_result
        # 装配失败回退旧路径（error_log 已留痕）

    analysis_input = _build_analysis_input(state)

    system_prompt = f"""你是一位资深金融行业研究员，拥有10年以上的A股研究经验。

## 你的任务
基于以下结构化数据，对标的公司进行专业深度分析。

## 分析维度（请严格按照此结构输出）
1. **公司基本面分析** - 主营业务、核心竞争力、管理层评价
2. **财务表现分析** - 营收趋势、盈利能力、偿债能力、现金流
3. **行业地位与竞争格局** - 市场份额、竞争优势、行业趋势
4. **潜在风险识别** - 经营风险、财务风险、政策风险、市场风险
5. **初步投资建议** - 基于以上分析的综合性观点

## 强制约束
- 所有结论必须基于下方提供的数据
- 数据缺失的维度，必须标注「数据不足，暂不评价」
- 严禁编造数据、指标具体数值
- 严禁给出「买入/卖出」等确定性投资建议

## 引用规范（必须遵守）
- 所有引用 PDF 原文数据的结论，必须标注页码，格式: [P 页码]
- 示例: "2024年营业收入为 20.18 亿元 [P 102]"
- 示例: "公司面临生物资产公允价值波动风险 [P 105]"
- 未标注页码的结论将被视为无效

## 分析数据
{analysis_input}

## 输出格式
使用 Markdown 格式，以 ## 开头的二级标题区分各分析维度。
每个关键结论后必须标注 [P 页码]。
"""

    # [修订模式] 覆盖为增量修订 prompt：以上一版为基础，只改反馈指出的问题
    if revision_mode:
        truncated_prev = prev_analysis[:6000]
        if len(prev_analysis) > 6000:
            truncated_prev += "\n...(过长已截断)..."
        system_prompt = f"""你是资深金融行业研究员（修订模式）。

## 任务背景
你此前的分析已通过复核审查，审查员指出了以下问题。请在其基础上做**针对性修订**，不要推倒重来。

## 审查反馈（必须逐条处理）
{review_feedback}

## 上一版分析（修订基础）
{truncated_prev}

## 参考数据（与上轮相同，供核对，严禁编造新数据）
{analysis_input}

## 修订要求
1. 只修改反馈指出的问题，未涉及的部分保持原样（保留原有正确结论与页码引用）
2. 保持引用规范: 引用 PDF 原文的结论必须标注 [P 页码]
3. 数据缺失的维度继续标注「数据不足，暂不评价」
4. 输出修订后的完整分析（Markdown，保留 ## 二级标题结构）
"""

    if not is_llm_ready():
        fallback_msg = "## 分析不可用\n\nLLM API Key 未配置，请在 `.env` 文件中设置。"
        agent_status["financial_analyst"] = "done"
        return {"analysis_result": fallback_msg, "agent_status": agent_status}

    result = safe_invoke(system_prompt, "请基于提供的数据进行分析。")

    if result.get("error"):
        error_log.append(f"[FinancialAnalyst] LLM 调用失败: {result.get('message', '未知错误')}")
        agent_status["financial_analyst"] = "error"
        return {
            "analysis_result": f"## 分析失败\n\n{result.get('message', '未知错误')}",
            "agent_status": agent_status,
            "error_log": error_log,
        }

    analysis_text = result.get("content", "")
    if not analysis_text.strip():
        error_log.append("[FinancialAnalyst] LLM 返回空内容")
        agent_status["financial_analyst"] = "error"
        return {"analysis_result": "## 分析失败\n\nLLM 返回空内容", "agent_status": agent_status, "error_log": error_log}

    logger.info("金融分析完成: %d 字符", len(analysis_text))
    agent_status["financial_analyst"] = "done"

    return {"analysis_result": analysis_text, "agent_status": agent_status, "error_log": error_log}
