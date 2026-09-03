"""
FinScope 领域分析 Agent 组 + Synthesizer（P5-E2）

5 领域按 A 股年报十节模板分工（key 与 chapter_tagger.DOMAINS 对齐）:
overview(0,1,8,99) / operating(3) / financial(2,9,10) / governance(4,5) / events(6,7)

- 领域上下文: ContextPreparator 预装配的 domain_contexts（各自独立预算，直读专属章节）
- facts 全局可查: 每个领域 agent 挂 fetch_context/query_fact/search_section 三工具
  （跨领域勾稽不塌——任何领域查任何科目都走 query_fact 全局事实表）
- 并行: ThreadPoolExecutor(DOMAIN_MAX_PARALLEL_AGENTS)；单领域失败不阻断其余
- Synthesizer: 合并领域产出为统一 analysis_result（跨域矛盾显式标注）；LLM 失败确定性拼接
- 修订: defect_domain 非空 → 只回炉该领域，其余产出从 state.domain_analyses 携带复用

注意: financial_analyst 必须在**函数内**延迟导入本模块（本模块模块级引用其
_TOOL_GUIDE，若两侧都模块级导入会循环）。
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List

from graphs.state import FinancialAnalysisState
from utils.config import get_settings
from utils.llm_client import is_llm_ready, safe_invoke, safe_invoke_with_tools

from agents.financial_analyst import _TOOL_GUIDE  # 工具指南复用同一份口径（导入约定见模块 docstring）
from extractors.chapter_tagger import DOMAINS

logger = logging.getLogger(__name__)

_DOMAIN_NAMES = {d["key"]: d["name"] for d in DOMAINS}

# 领域分析要点（prompt 片段）
_DOMAIN_BRIEFS = {
    "overview": "主营业务与商业模式、行业定位、报告期重要风险因素（含重要提示与风险章节）。",
    "operating": "报告期经营情况回顾、收入构成与变动原因、核心竞争力、未来展望（MD&A）。",
    "financial": "主要会计数据与财务指标、资产负债结构、利润表与现金流量关键科目、审计意见。",
    "governance": "公司治理运作、董监高情况、环境与社会责任、员工与合规。",
    "events": "重大诉讼仲裁、承诺履行、关联交易、股份变动与股东结构。",
}

_SYNTH_ORDER = ("overview", "operating", "financial", "governance", "events")


def _run_one_domain(key: str, domain_context: str, state: FinancialAnalysisState) -> Dict[str, Any]:
    """单领域 agent 完整运行（有界工具循环）。返回 {text, error, tool_history}。"""
    settings = get_settings()
    l1 = state.get("pdf_l1") or {}
    name = _DOMAIN_NAMES.get(key, key)
    tool_history: List[Dict[str, Any]] = []

    def _audit(tool_name: str, args: Dict[str, Any], result: str) -> None:
        tool_history.append({
            "agent": f"domain_{key}",
            "tool": tool_name,
            "args": args,
            "ts": datetime.now().isoformat(timespec="seconds"),
            "result_len": len(result or ""),
        })

    from agents.context_tools import build_context_tools
    tools = build_context_tools(l1, on_tool_call=_audit)

    revision = bool(state.get("review_feedback") and (state.get("domain_analyses") or {}).get(key))
    prev_domain = (state.get("domain_analyses") or {}).get(key, "")

    if revision:
        system_prompt = f"""你是资深金融行业研究员（{name} 领域·修订模式）。

## 审查反馈（只针对本领域，逐条处理）
{state.get("review_feedback", "")}

## 上一版本领域分析（修订基础，只改反馈涉及的问题）
{prev_domain[:4000]}

## 领域专属上下文（供核对，严禁编造新数据；可继续用工具核验数字）
{domain_context}

{_TOOL_GUIDE}

## 输出修订后的完整领域分析（Markdown，保留 [P 页码] 引用）
"""
    else:
        system_prompt = f"""你是资深金融行业研究员，负责「{name}」领域的深度分析。

## 领域职责
{_DOMAIN_BRIEFS.get(key, "")}

## 领域专属上下文（预算装配，直读本领域章节；装不下的用工具调取）
{domain_context}

## 强制约束与引用规范
- 所有结论必须基于上下文或工具返回结果；数据缺失标注「数据不足，暂不评价」
- 严禁编造数据、指标具体数值；严禁「买入/卖出」等确定性投资建议
- 引用 PDF 原文数据的结论必须标注页码 [P 页码]

{_TOOL_GUIDE}

## 输出格式
Markdown，直接以领域内容开头（不要重复大标题），关键结论后标注 [P 页码]。
"""

    result = safe_invoke_with_tools(
        system_prompt,
        "请基于提供的数据进行领域分析；数据不足时调用工具补取后再回答。",
        tools,
        max_rounds=settings.MAX_TOOL_ROUNDS_PER_AGENT,
        on_tool_call=_audit,
    )
    if result.get("error") or not result.get("content", "").strip():
        return {"text": "", "error": True, "tool_history": tool_history}
    return {"text": result["content"], "error": False, "tool_history": tool_history}


def run_domain_agents(
    state: FinancialAnalysisState,
    agent_status: Dict[str, str],
    error_log: list,
) -> Dict[str, Any]:
    """
    并行运行领域 agent 组。

    修订语义: defect_domain 非空 → 只重跑该领域（其余从 state.domain_analyses 携带）；
    修订但 defect_domain 为空 → 全量重跑（无法定位就保守都看一遍）。

    Returns: {"analyses": {domain_key: 分析文本}, "tool_history": [...]}
             （应跑领域全灭且无携带 → analyses 为空 dict，调用方回退全局路径）
    """
    settings = get_settings()
    domain_contexts = state.get("domain_contexts") or {}
    if not domain_contexts:
        return {"analyses": {}, "tool_history": []}

    prev = {k: v for k, v in (state.get("domain_analyses") or {}).items()
            if k in domain_contexts and v}
    only = state.get("defect_domain") or ""
    if only and only in domain_contexts:
        todo = [only]
        carried = {k: v for k, v in prev.items() if k != only}
    else:
        todo = list(domain_contexts.keys())
        carried = {}

    def _work(key: str) -> Dict[str, Any]:
        try:
            return _run_one_domain(key, domain_contexts[key], state)
        except Exception as e:  # 单领域失败不阻断其余
            logger.warning("[DomainAgents] %s 运行异常: %s", key, str(e)[:120])
            return {"text": "", "error": True, "tool_history": []}

    analyses: Dict[str, str] = dict(carried)
    merged_history: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(settings.DOMAIN_MAX_PARALLEL_AGENTS, len(todo))) as pool:
        for key, res in zip(todo, pool.map(_work, todo)):
            if res["error"] or not res["text"]:
                error_log.append(f"[DomainAgents] 领域 {key} 分析失败（跳过）")
                continue
            analyses[key] = res["text"]
            merged_history.extend(res["tool_history"])

    logger.info("[DomainAgents] 完成: %d/%d 领域新产出（携带复用 %d）",
                len(analyses) - len(carried), len(todo), len(carried))
    return {"analyses": analyses, "tool_history": merged_history}


def synthesize_analyses(domain_analyses: Dict[str, str], state: FinancialAnalysisState) -> str:
    """Synthesizer: 合并领域产出 → 统一 analysis_result。LLM 失败 → 确定性拼接（固定领域序）。"""
    if not domain_analyses:
        return ""
    ordered = [(k, domain_analyses[k]) for k in _SYNTH_ORDER if k in domain_analyses]

    if is_llm_ready():
        sections_text = "\n\n".join(
            f"### {_DOMAIN_NAMES.get(k, k)}\n{t[:4000]}" for k, t in ordered
        )
        system_prompt = f"""你是资深金融行业研究员（综合分析），把各领域分析合并为一份统一的分析结论。

## 规则
1. 保留各领域关键结论与 [P 页码] 引用，不得丢失数字
2. 跨领域矛盾（如经营叙述的收入与财务报表收入不一致）必须显式标注「⚠ 跨域不一致」，不得静默取舍
3. 输出结构: ## 公司概览与风险 / ## 经营分析 / ## 财务分析 / ## 治理与ESG / ## 重要事项与股东 / ## 综合结论
4. 严禁编造各领域分析中不存在的数据

## 领域分析
{sections_text}
"""
        result = safe_invoke(system_prompt, "请合并领域分析为统一结论。")
        if not result.get("error") and result.get("content", "").strip():
            return result["content"]

    # 确定性兜底: 固定领域序拼接（LLM 不可用/失败时保底可用）
    lines = ["> （Synthesizer 规则拼接版: LLM 不可用，按领域原文拼接）"]
    for k, t in ordered:
        lines.append(f"## {_DOMAIN_NAMES.get(k, k)}\n\n{t}")
    return "\n\n".join(lines)