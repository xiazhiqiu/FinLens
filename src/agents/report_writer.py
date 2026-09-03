"""
FinScope ReportWriter Agent

负责:
1. 整合全链路信息（实体 + 数据 + 分析结论 + PDF 压缩内容）
2. 生成符合券商研报规范的专业 Markdown 报告
3. 固定六大章节结构，保证输出一致性
4. 追加程序化来源表（非 LLM 生成，保证可靠性）
5. [企业级] 注入数据血缘信息
"""

import json
import logging
import re
from datetime import datetime
from typing import Dict, Any, List

from graphs.state import FinancialAnalysisState
from utils.config import get_settings
from utils.llm_client import safe_invoke, safe_invoke_with_tools, is_llm_ready

# 企业级模块（可选导入）
try:
    from audit.data_lineage import DataLineage, DataSourceType, get_lineage
    ENTERPRISE_MODE = True
except ImportError:
    ENTERPRISE_MODE = False

logger = logging.getLogger(__name__)

_WRITER_TOOL_GUIDE = """## 可用工具（撰写时按需取数，工具结果可溯源）
- fetch_context(scope): 章节/页码原文调取（scope 如 "s_012" 或 "p12-14"）
- query_fact(company, metric, period): 精确查结构化事实（三参数均可省略过滤）
- search_section(query): 章节关键词检索
调用规则:
1. 写到具体数字时优先用 query_fact 核验后再写（不许凭印象写数）
2. 需要某段详细论述或表格时用 fetch_context 取原文
3. 每轮工具调用 ≤ 数条；能一次查清就一次查清
4. 工具结果标注的页码即引用依据 [P 页码]"""


def _get_data_lineage() -> "DataLineage":
    """获取全进程共享的数据血缘单例（跨 Agent 可见，修复此前各自 new 导致的溯源失效）"""
    return get_lineage() if ENTERPRISE_MODE else None


def _collect_full_context(state: FinancialAnalysisState) -> str:
    """收集全链路上下文（无 PDF / 诊断降级时的基础写作路径）"""
    parts = []

    parts.append(f"## 原始需求\n{state.get('user_query', '未提供')}")

    # 抽取的实体
    entities = state.get("extracted_entities", [])
    if entities:
        by_type: Dict[str, list] = {}
        for e in entities:
            et = e.get("entity_type", "other")
            by_type.setdefault(et, []).append(e)

        parts.append("## 研报抽取信息（来源：PDF研报原文）")
        for entity_type, items in sorted(by_type.items()):
            names = [item.get("entity_name", "?") for item in items[:5]]
            parts.append(f"- {entity_type} ({len(items)}个): {', '.join(names)}")
    else:
        parts.append("## 研报抽取信息\n（无）")

    fin_data = state.get("financial_data", {})
    if fin_data:
        parts.append("## 公开市场数据（来源：Tushare/AkShare）")
        parts.append(json.dumps(fin_data, ensure_ascii=False, indent=2)[:2000])
    else:
        parts.append("## 公开市场数据\n（无）")

    analysis = state.get("analysis_result", "")
    if analysis:
        truncated = analysis[:3000]
        if len(analysis) > 3000:
            truncated += "\n\n...(分析内容已截断)..."
        parts.append(f"## 深度分析结论\n{truncated}")
    else:
        parts.append("## 深度分析结论\n（未完成分析）")

    # [企业级] 数据血缘信息
    lineage_node_id = state.get("lineage_node_id", "")
    lineage = _get_data_lineage()
    if lineage and lineage_node_id:
        upstream = lineage.trace_upstream(lineage_node_id)
        if upstream.get("sources"):
            parts.append("## 数据血缘（来源追踪）")
            for src in upstream["sources"]:
                parts.append(f"- {src['name']} (类型: {src['source_type']})")
            parts.append("\n> 注：以上数据来源已通过血缘追踪验证，请在报告中标注数据来源。")

    return "\n\n".join(parts)


def _build_source_table(state: FinancialAnalysisState) -> str:
    """
    程序化生成来源表（非 LLM 生成，保证可靠性）

    [A4] 数据源从旧 pdf_sections（eager 压缩产物）切换为 pdf_l1.facts
    （结构化无损层，带页码/表格/行列溯源）。

    格式:
    | 页码 | 科目 | 期间 | 数值 |
    |------|------|------|------|
    | P 102 | 营业收入 | FY2024 | 20.18 |
    """
    facts = (state.get("pdf_l1") or {}).get("facts") or []
    if not facts:
        return ""

    rows = []
    for f in facts[:15]:  # 来源表抽样，避免长年报刷屏
        src = f.get("source") or {}
        page = src.get("page_idx", "?")
        tid = src.get("table_id", "")
        loc = f" [t:{tid}]" if tid else ""
        raw = f.get("raw", "")
        rows.append(
            f"| P {page}{loc} | {f.get('metric', '?')} | {f.get('period', '?')} | {raw} |"
        )

    table = [
        "",
        "---",
        "",
        "## 来源表（年报结构化事实抽样）",
        "",
        "> 注：本表由程序自动生成，数据来源于 PDF 年报 L1 结构化抽取，含页码与表格溯源。",
        "",
        "| 页码 | 科目 | 期间 | 数值 |",
        "|------|------|------|------|",
    ]
    table.extend(rows)

    return "\n".join(table)


def _build_fallback_report(state: FinancialAnalysisState) -> str:
    """降级报告（LLM 不可用时使用模板生成）"""
    user_query = state.get("user_query", "金融分析")
    fin_data = state.get("financial_data", {})
    entities = state.get("extracted_entities", [])
    analysis = state.get("analysis_result", "")

    stock_name = "标的公司"
    if fin_data.get("stock_info"):
        stock_name = fin_data["stock_info"].get("name", stock_name)

    report_date = datetime.now().strftime("%Y年%m月%d日")

    sections = [
        f"# {stock_name} 投资分析报告（简要版）",
        f"> 生成日期: {report_date}",
        f"> 原始查询: {user_query}",
        "", "---", "",
        "## 一、核心结论与投资摘要", "",
    ]

    if analysis:
        first_lines = analysis.strip().split("\n")[:5]
        sections.extend(first_lines)
    else:
        sections.append("（分析模块未执行，请配置 LLM API Key 后重试）")

    sections.extend(["", "## 二公司概况", ""])

    if fin_data.get("stock_info"):
        info = fin_data["stock_info"]
        sections.append(f"- 公司全称: {info.get('name', 'N/A')}")
        sections.append(f"- 所属行业: {info.get('industry', 'N/A')}")
        sections.append(f"- 股票代码: {info.get('code', 'N/A')}")
    else:
        sections.append("（无公开市场数据）")

    if entities:
        sections.extend(["", "### 研报关键信息摘要"])
        for e in entities[:10]:
            sections.append(f"- [{e.get('entity_type', '?')}] {e.get('entity_name', '?')}")

    sections.extend(["", "## 三、财务分析", ""])

    if fin_data.get("financial_indicators"):
        ind = fin_data["financial_indicators"]
        sections.append("| 指标 | 数值 |")
        sections.append("|------|------|")
        for key, value in ind.items():
            if key not in ("stock_code", "data_source", "error"):
                sections.append(f"| {key} | {value} |")
    else:
        sections.append("（无财务数据）")

    sections.extend([
        "", "## 四、行业观点与竞争格局", "",
        "（请配置 LLM 后获取深度分析）", "",
        "## 五、风险提示", "",
        "- 市场风险: 股价波动受多重因素影响",
        "- 政策风险: 行业监管政策变化可能影响经营",
        "- 经营风险: 公司经营业绩存在不确定性", "",
        "## 六、投资建议（仅供参考）", "",
        "> 免责声明: 本报告由 FinScope 自动生成，仅供学习研究参考，不构成任何投资建议。",
        "", "---",
        f"> Generated by FinScope v0.1.0 | {report_date}",
    ])

    return "\n".join(sections)


def _finalize_report(report_text: str, state: FinancialAnalysisState) -> str:
    """成功产物的统一后处理: 免责声明 + 程序化来源表 + 审查标注 + 版本脚注（新旧链路共用）"""
    # 追加免责声明
    if "免责" not in report_text and "不构成" not in report_text:
        report_text += (
            "\n\n---\n"
            "> 免责声明: 本报告由 FinScope 多智能体系统自动生成，"
            "仅供学习研究参考，不构成任何投资建议。股市有风险，投资需谨慎。\n"
        )

    # 追加程序化来源表（非 LLM 生成）
    source_table = _build_source_table(state)
    if source_table:
        report_text += source_table

    # [企业级] 追加审查标注
    if state.get("review_result", "") == "pass":
        report_text += "\n> 本报告已通过 Agent 复核审查。\n"

    report_text += f"\n\n---\n> Generated by FinScope v0.3.0 | {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
    return report_text


def _build_writer_audit_callback(tool_call_history: list):
    """工具审计回调: 每次调用写全 谁/何时/工具/参数/返回体量（银行审计留痕）"""

    def _audit(tool_name: str, args: Dict[str, Any], result: str) -> None:
        tool_call_history.append({
            "agent": "report_writer",
            "tool": tool_name,
            "args": args,
            "ts": datetime.now().isoformat(timespec="seconds"),
            "result_len": len(result or ""),
        })

    return _audit


def _multilevel_writer_run(
    state: FinancialAnalysisState,
    agent_status: Dict[str, str],
    error_log: list,
    revision_mode: bool,
) -> Dict[str, Any]:
    """
    [A1 新链路] 预算装配 + 有界工具循环撰写。

    触发: USE_MULTILEVEL_COMPRESSION=true 且 pdf_l1 含章节。
    设计（沿用 Analyst 已验证模式）:
    - 上下文 = L1 预算装配（l2/l3 直接读 state——Analyst 已写回，跨 agent 复用零重压）
    - 挂载 fetch_context / query_fact / search_section 三工具
    - 每次工具调用写 tool_call_history（银行审计留痕）
    - 返回 None 表示回退旧路径（装配失败）
    """
    settings = get_settings()
    l1 = state.get("pdf_l1") or {}

    from extractors.context_assembler import assemble
    from agents.context_tools import build_context_tools

    # 1) [P5-E1] 优先消费 ContextPreparator 预装配产物；空则兜底装配（不建 L2/L3）
    pdf_context = state.get("pdf_context", "")
    if not pdf_context:
        try:
            assembled = assemble(
                state.get("user_query", ""), settings.CONTEXT_BUDGET_TOKENS, l1,
                l2=(state.get("pdf_l2") or {}) or None,
                l3=((state.get("pdf_l3") or {}).get("text") or "") or None,
            )
            pdf_context = assembled["context"]
        except Exception as e:
            error_log.append(f"[ReportWriter] 兜底装配失败，回退旧路径: {str(e)[:150]}")
            return None

    # 2) 组装上下文（需求 + 实体 + 市场数据 + 分析结论 + 装配后的年报内容）
    parts = [f"## 原始需求\n{state.get('user_query', '未提供')}"]

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
        parts.append("## 公开市场数据（Tushare/AkShare）\n" + json.dumps(fin_data, ensure_ascii=False, indent=2)[:2000])

    analysis = state.get("analysis_result", "")
    if analysis:
        parts.append(f"## 深度分析结论（Analyst 产出，报告的素材基础）\n{analysis[:3000]}")
    else:
        parts.append("## 深度分析结论\n（未完成分析）")

    # [P5-E4] 派生指标表（确定性算子产物；LLM 只引用「数值」列，禁止重算改写）
    from extractors.derived_metrics import render_derived_metrics
    derived_table = render_derived_metrics(state.get("derived_metrics") or [])
    if derived_table:
        parts.append(
            "## 派生财务指标（确定性算子产物，引用「数值」列，禁止重算/取整/改写）\n" + derived_table
        )
        parts.append(
            "## 派生指标铁律（必须遵守）\n"
            "- 「派生财务指标」表的数值由确定性算子计算（公式与来源列可审计），直接引用，禁止自行重算"
        )

    # [P5-E5] 跨源核对告警（mismatch 科目引用需谨慎，以 facts 表为准）
    from extractors.cross_checker import render_cross_warnings
    cross_warn = render_cross_warnings(state.get("cross_source_checks") or [])
    if cross_warn:
        parts.append(cross_warn)

    parts.append("## PDF 年报内容（预算装配版，装不下的章节在末尾指针中，需要时用工具调取）\n" + pdf_context)
    context_text = "\n\n".join(parts)

    # 3) prompt
    system_prompt = f"""你是国内顶级券商研究所的首席分析师，专精于撰写A股投资分析报告。

## 你的任务
基于以下全部信息，撰写一份专业、结构完整的投资分析报告。

## 报告结构（卖方研报模板，必须严格遵循）
### 一、投资要点（论点前置: 3-5 条核心结论，每条含关键数字与 [P 页码]）
### 二、公司概况（主营业务、股权结构、行业定位）
### 三、财务分析（增长性/盈利能力/偿债能力/现金流——优先引用「派生财务指标」表数值）
### 四、经营分析与行业格局
### 五、治理与ESG
### 六、重要事项与风险提示
### 七、投资建议（仅供参考）
- 必须包含免责声明

## 引用规范（必须遵守）
- 所有引用 PDF 原文数据的结论，必须标注页码，格式: [P 页码]
- 示例: "2024年营业收入为 20.18 亿元 [P 102]"
- 未标注页码的结论将被视为无效

## 风格要求
- 专业严谨，符合国内券商研报表述规范
- 数据引用时标注来源
- 数据缺失处标注「数据不足」

## 可用信息
{context_text}

{_WRITER_TOOL_GUIDE}
"""

    # [修订模式] 覆盖为增量修订 prompt（同样挂工具，供核对反馈涉及的数字）
    if revision_mode:
        prev_report = state.get("prev_final_report", "")
        truncated_prev = prev_report[:6000]
        if len(prev_report) > 6000:
            truncated_prev += "\n...(过长已截断)..."
        system_prompt = f"""你是券商首席分析师（报告修订模式）。

## 任务背景
你撰写的报告未通过复核审查。请针对以下问题**定向修订**，不要重写全文。

## 审查反馈（必须逐条处理）
{state.get("review_feedback", "")}

## 上一版报告（修订基础）
{truncated_prev}

## 可用信息（供核对，严禁引入信息中不存在的数据）
{context_text}

{_WRITER_TOOL_GUIDE}

## 修订要求
1. 只修改反馈指出的问题，未涉及的章节保持原样
2. 若反馈涉及合规违规（规则引擎检出），必须逐条修正措辞，不得保留原表述
3. 保留报告章节结构（卖方七段模板）、[P 页码] 引用与免责声明
4. 输出修订后的完整报告（Markdown）
"""

    if not is_llm_ready():
        return None  # 回退旧路径（降级报告由旧路径生成）

    # 4) 有界工具循环（审计留痕 + 预算熔断）
    tool_call_history = list(state.get("tool_call_history", []))
    audit_cb = _build_writer_audit_callback(tool_call_history)
    tools = build_context_tools(l1, on_tool_call=audit_cb)
    result = safe_invoke_with_tools(
        system_prompt,
        "请撰写专业投资分析报告；需要具体数字或原文时先调用工具核验。",
        tools,
        max_rounds=settings.MAX_TOOL_ROUNDS_PER_AGENT,
        on_tool_call=audit_cb,
    )

    if result.get("error"):
        error_log.append(f"[ReportWriter] LLM 调用失败: {result.get('message', '未知错误')}")
        agent_status["report_writer"] = "done"
        return {
            "final_report": _build_fallback_report(state),
            "agent_status": agent_status,
            "error_log": error_log,
            "tool_call_history": tool_call_history,
        }

    report_text = result.get("content", "")
    if not report_text.strip():
        agent_status["report_writer"] = "done"
        return {
            "final_report": _build_fallback_report(state),
            "agent_status": agent_status,
            "error_log": error_log,
            "tool_call_history": tool_call_history,
        }

    # [A5] 工具循环路径的最终回复常带元评论前缀（"I now have..."），
    # 报告本体从首个 Markdown H1（"# " 开头的行）开始；无 H1 时原样保留
    for _i, _ln in enumerate(report_text.lstrip().split("\n")):
        if _ln.startswith("# "):
            if _i > 0:  # 首行即 H1 则无需剥离
                report_text = "\n".join(report_text.lstrip().split("\n")[_i:])
            break

    n_calls = len(result.get("tool_calls") or [])
    logger.info("报告撰写完成（多级链路）: %d 字符, 工具调用 %d 次", len(report_text), n_calls)
    agent_status["report_writer"] = "done"

    return {
        "final_report": _finalize_report(report_text, state),
        "agent_status": agent_status,
        "error_log": error_log,
        "tool_call_history": tool_call_history,
    }


def report_writer_node(state: FinancialAnalysisState) -> Dict[str, Any]:
    """分析报告撰写节点（USE_MULTILEVEL_COMPRESSION 开启且 pdf_l1 就绪时走新链路）"""
    agent_status = dict(state.get("agent_status", {}))
    error_log = list(state.get("error_log", []))

    settings = get_settings()
    l1 = state.get("pdf_l1") or {}
    use_multilevel = settings.USE_MULTILEVEL_COMPRESSION and bool(l1.get("sections"))

    # [修订模式] 有上一版报告 + 审查反馈时，做增量修订而非重写全文
    review_feedback = state.get("review_feedback", "")
    prev_report = state.get("prev_final_report", "")
    revision_mode = bool(review_feedback and prev_report)

    if use_multilevel:
        new_result = _multilevel_writer_run(state, agent_status, error_log, revision_mode)
        if new_result is not None:
            return new_result
        # 装配失败回退旧路径（error_log 已留痕）

    full_context = _collect_full_context(state)

    system_prompt = f"""你是国内顶级券商研究所的首席分析师，专精于撰写A股投资分析报告。

## 你的任务
基于以下全部信息，撰写一份专业、结构完整的投资分析报告。

## 报告结构（必须严格遵循）
### 一、核心结论与投资摘要
### 二、公司概况
### 三、财务分析
### 四、行业观点与竞争格局
### 五、风险提示
### 六、投资建议（仅供参考）
- 必须包含免责声明

## 引用规范（必须遵守）
- 所有引用 PDF 原文数据的结论，必须标注页码，格式: [P 页码]
- 示例: "2024年营业收入为 20.18 亿元 [P 102]"
- 未标注页码的结论将被视为无效

## 风格要求
- 专业严谨，符合国内券商研报表述规范
- 数据引用时标注来源
- 数据缺失处标注「数据不足」

## 可用信息
{full_context}
"""

    # [修订模式] 覆盖为增量修订 prompt：以上一版报告为基础，只改反馈指出的问题
    if revision_mode:
        truncated_prev = prev_report[:6000]
        if len(prev_report) > 6000:
            truncated_prev += "\n...(过长已截断)..."
        system_prompt = f"""你是券商首席分析师（报告修订模式）。

## 任务背景
你撰写的报告未通过复核审查。请针对以下问题**定向修订**，不要重写全文。

## 审查反馈（必须逐条处理）
{review_feedback}

## 上一版报告（修订基础）
{truncated_prev}

## 可用信息（供核对，严禁引入信息中不存在的数据）
{full_context}

## 修订要求
1. 只修改反馈指出的问题，未涉及的章节保持原样
2. 若反馈涉及合规违规（规则引擎检出），必须逐条修正措辞，不得保留原表述
3. 保留六大章节结构、[P 页码] 引用与免责声明
4. 输出修订后的完整报告（Markdown）
"""

    if not is_llm_ready():
        logger.warning("LLM 不可用，生成降级报告")
        fallback = _build_fallback_report(state)
        agent_status["report_writer"] = "done"
        return {"final_report": fallback, "agent_status": agent_status}

    result = safe_invoke(system_prompt, "请撰写专业投资分析报告。")

    if result.get("error"):
        error_log.append(f"[ReportWriter] LLM 调用失败: {result.get('message', '未知错误')}")
        fallback = _build_fallback_report(state)
        agent_status["report_writer"] = "done"
        return {"final_report": fallback, "agent_status": agent_status, "error_log": error_log}

    report_text = result.get("content", "")
    if not report_text.strip():
        fallback = _build_fallback_report(state)
        agent_status["report_writer"] = "done"
        return {"final_report": fallback, "agent_status": agent_status, "error_log": error_log}

    # 统一后处理: 免责声明 + 来源表 + 审查标注 + 版本脚注（与多级链路共用）
    report_text = _finalize_report(report_text, state)

    logger.info("分析报告撰写完成: %d 字符", len(report_text))
    agent_status["report_writer"] = "done"

    return {"final_report": report_text, "agent_status": agent_status, "error_log": error_log}
