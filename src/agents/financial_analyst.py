"""
FinScope FinancialAnalyst Agent

负责:
1. 整合 extracted_entities + financial_data
2. LLM 基于结构化数据进行多维度分析
3. 强约束：所有结论必须来自已有数据，缺失时标注"数据不足"
"""

import json
import logging
from typing import Dict, Any

from graphs.state import FinancialAnalysisState
from utils.llm_client import safe_invoke, is_llm_ready

logger = logging.getLogger(__name__)

ANALYSIS_DIMENSIONS = [
    "公司基本面分析",
    "财务表现分析",
    "行业地位与竞争格局",
    "潜在风险识别",
    "初步投资建议",
]


def _build_analysis_input(state: FinancialAnalysisState) -> str:
    """拼装分析输入上下文"""
    parts = []

    parts.append(f"## 用户查询\n{state.get('user_query', '未提供')}")
    parts.append(f"## 研报类型\n{state.get('report_type', 'company')}")

    entities = state.get("extracted_entities", [])
    if entities:
        parts.append("## 研报抽取信息（来源: PDF研报原文）")
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

    fin_data = state.get("financial_data", {})
    if fin_data:
        parts.append("## 公开市场数据（来源: Tushare/AkShare）")
        parts.append(json.dumps(fin_data, ensure_ascii=False, indent=2))
    else:
        parts.append("## 公开市场数据\n（无市场数据）")

    return "\n\n".join(parts)


def financial_analyst_node(state: FinancialAnalysisState) -> Dict[str, Any]:
    """金融深度分析节点"""
    agent_status = dict(state.get("agent_status", {}))
    error_log = list(state.get("error_log", []))

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

## 分析数据
{analysis_input}

## 输出格式
使用 Markdown 格式，以 ## 开头的二级标题区分各分析维度。
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
