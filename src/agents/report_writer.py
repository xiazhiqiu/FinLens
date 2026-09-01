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
from utils.llm_client import safe_invoke, is_llm_ready

# 企业级模块（可选导入）
try:
    from audit.data_lineage import DataLineage, DataSourceType
    ENTERPRISE_MODE = True
except ImportError:
    ENTERPRISE_MODE = False

logger = logging.getLogger(__name__)

# 数据血缘全局单例
_data_lineage = None


def _get_data_lineage() -> "DataLineage":
    """获取数据血缘单例"""
    global _data_lineage
    if _data_lineage is None and ENTERPRISE_MODE:
        _data_lineage = DataLineage()
    return _data_lineage


def _collect_full_context(state: FinancialAnalysisState) -> str:
    """收集全链路上下文"""
    parts = []

    parts.append(f"## 原始需求\n{state.get('user_query', '未提供')}")

    # PDF 压缩摘要
    pdf_summary = state.get("pdf_summary", "")
    if pdf_summary:
        parts.append("## PDF 全文摘要（来源: PDF 研报）")
        parts.append(pdf_summary[:2000])

    # 压缩后的页面内容（带页码）
    pdf_sections = state.get("pdf_sections", [])
    if pdf_sections:
        parts.append("## PDF 逐页提取内容（来源: PDF 研报，带页码）")
        for section in pdf_sections[:20]:  # 最多 20 页
            page_idx = section.get("page_idx", "?")
            key_points = section.get("key_points", [])
            financial_data = section.get("financial_data", {})

            page_content = [f"### Page {page_idx}"]
            if key_points:
                page_content.append("要点: " + "; ".join(key_points[:3]))
            if financial_data:
                for key, value in list(financial_data.items())[:5]:
                    page_content.append(f"  - {key}: {value}")
            parts.append("\n".join(page_content))

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

    格式:
    | 页码 | 内容类型 | 关键信息 |
    |------|----------|----------|
    | P 102 | 财务数据 | 营业收入 20.18 亿元 |
    """
    pdf_sections = state.get("pdf_sections", [])

    if not pdf_sections:
        return ""

    rows = []
    for section in pdf_sections:
        page_idx = section.get("page_idx", "?")
        key_points = section.get("key_points", [])
        financial_data = section.get("financial_data", {})
        has_llm = section.get("has_llm_compression", False)

        # 提取关键信息摘要
        info_parts = []
        if financial_data:
            for key, value in list(financial_data.items())[:3]:
                info_parts.append(f"{key}: {value}")
        if key_points:
            for point in key_points[:2]:
                info_parts.append(point[:50])

        if info_parts:
            info_summary = "; ".join(info_parts)
            # 截断过长内容
            if len(info_summary) > 100:
                info_summary = info_summary[:97] + "..."
            rows.append(f"| P {page_idx} | {'LLM' if has_llm else '规则'} | {info_summary} |")

    if not rows:
        return ""

    table = [
        "",
        "---",
        "",
        "## 来源表",
        "",
        "> 注：本表由程序自动生成，数据来源于 PDF 研报原文提取。",
        "",
        "| 页码 | 提取方式 | 关键信息 |",
        "|------|----------|----------|",
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


def report_writer_node(state: FinancialAnalysisState) -> Dict[str, Any]:
    """分析报告撰写节点"""
    agent_status = dict(state.get("agent_status", {}))
    error_log = list(state.get("error_log", []))

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
        return {"final_report": fallback, "agent_status": agent_status}

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
    review_result = state.get("review_result", "")
    if review_result == "pass":
        report_text += (
            "\n> 本报告已通过 Agent 复核审查。\n"
        )

    report_text += f"\n\n---\n> Generated by FinScope v0.3.0 | {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"

    logger.info("分析报告撰写完成: %d 字符", len(report_text))
    agent_status["report_writer"] = "done"

    return {"final_report": report_text, "agent_status": agent_status, "error_log": error_log}
