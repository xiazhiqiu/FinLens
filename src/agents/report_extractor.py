"""
FinScope ReportExtractor Agent

负责:
1. 检测 state 中是否有待解析的 PDF 路径
2. 调用 extract_report_key_info 工具抽取结构化金融实体
3. 调用 page_compressor 压缩 PDF 内容
4. 清洗工具返回结果，存入 extracted_entities
5. 将压缩结果写入 pdf_sections 和 pdf_summary
6. [企业级] 记录数据血缘节点
"""

import os
import json
import logging
from typing import Dict, Any, List

from graphs.state import FinancialAnalysisState
from tools.financial_tools import extract_report_key_info
from extractors.page_compressor import compress_pages

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


def report_extractor_node(state: FinancialAnalysisState) -> Dict[str, Any]:
    """研报信息抽取节点"""
    agent_status = dict(state.get("agent_status", {}))
    error_log = list(state.get("error_log", []))
    existing_entities = list(state.get("extracted_entities", []))

    # 获取 PDF 路径
    pdf_path = state.get("pdf_path", "")
    if not pdf_path:
        if existing_entities:
            agent_status["report_extractor"] = "done"
            return {"agent_status": agent_status}
        logger.info("未提供 PDF 路径，跳过研报抽取")
        agent_status["report_extractor"] = "done"
        return {"agent_status": agent_status}

    # 文件存在性检查
    if not os.path.isfile(pdf_path):
        logger.warning("PDF 文件不存在: %s", pdf_path)
        error_log.append(f"[ReportExtractor] PDF 文件不存在: {pdf_path}")
        agent_status["report_extractor"] = "done"
        return {"agent_status": agent_status, "error_log": error_log}

    # 工具调用
    try:
        tool_result_str = extract_report_key_info.invoke({"pdf_path": pdf_path})
        tool_result = json.loads(tool_result_str)
    except json.JSONDecodeError as e:
        error_log.append(f"[ReportExtractor] JSON 解析失败: {e}")
        agent_status["report_extractor"] = "done"
        return {"agent_status": agent_status, "error_log": error_log}
    except Exception as e:
        error_log.append(f"[ReportExtractor] 工具调用异常: {e}")
        agent_status["report_extractor"] = "done"
        return {"agent_status": agent_status, "error_log": error_log}

    # 检查工具是否成功
    if tool_result.get("error"):
        error_log.append(f"[ReportExtractor] 工具失败: {tool_result.get('message', '未知错误')}")
        agent_status["report_extractor"] = "done"
        return {"agent_status": agent_status, "error_log": error_log}

    # 清洗与结构化
    extraction = tool_result.get("extraction", {})
    new_entities: List[Dict[str, Any]] = []

    for company_name in extraction.get("companies", []):
        if company_name and isinstance(company_name, str) and len(company_name) >= 2:
            new_entities.append({"entity_type": "company", "entity_name": company_name, "source": "pdf_extraction"})

    for code in extraction.get("stock_codes", []):
        if code and isinstance(code, str):
            new_entities.append({"entity_type": "stock_code", "entity_name": code, "source": "pdf_extraction"})

    for metric in extraction.get("financial_metrics", []):
        if metric.get("name") and metric.get("value"):
            new_entities.append({"entity_type": "financial_metric", "entity_name": metric["name"], "entity_value": metric["value"], "source": "pdf_extraction"})

    for rating in extraction.get("ratings", []):
        if rating and isinstance(rating, str):
            new_entities.append({"entity_type": "rating", "entity_name": rating, "source": "pdf_extraction"})

    for price in extraction.get("target_prices", []):
        if price:
            new_entities.append({"entity_type": "target_price", "entity_name": str(price), "source": "pdf_extraction"})

    report_date = extraction.get("report_date", "")
    if report_date and report_date != "未识别":
        new_entities.append({"entity_type": "report_date", "entity_name": report_date, "source": "pdf_extraction"})

    if new_entities:
        logger.info("研报抽取完成: %d 个实体", len(new_entities))

    # PDF 深度利用：压缩结构化页面
    structured_pages = extraction.get("structured_pages", [])
    pdf_sections = []
    pdf_summary = ""

    if structured_pages:
        logger.info("开始压缩 PDF 页面: %d 页", len(structured_pages))
        try:
            compressor_result = compress_pages(structured_pages, use_llm=True)
            if not compressor_result.get("error"):
                pdf_sections = compressor_result.get("compressed_pages", [])
                pdf_summary = compressor_result.get("summary", "")
                logger.info(
                    "PDF 压缩完成: LLM=%d, 规则=%d",
                    compressor_result.get("llm_compressed_count", 0),
                    compressor_result.get("rule_compressed_count", 0),
                )
            else:
                logger.warning("PDF 压缩失败: %s", compressor_result.get("message", "未知错误"))
                error_log.append(f"[ReportExtractor] PDF 压缩失败: {compressor_result.get('message', '')}")
        except Exception as e:
            logger.warning("PDF 压缩异常: %s", str(e)[:100])
            error_log.append(f"[ReportExtractor] PDF 压缩异常: {str(e)[:100]}")

    agent_status["report_extractor"] = "done"

    # [企业级] 记录数据血缘节点
    lineage_node_id = ""
    lineage = _get_data_lineage()
    if lineage:
        node = lineage.create_source_node(
            name=f"PDF研报抽取: {os.path.basename(pdf_path)}",
            source_type=DataSourceType.PDF_EXTRACTION,
            metadata={
                "pdf_path": pdf_path,
                "entity_count": len(new_entities),
                "compressed_pages": len(pdf_sections),
            },
        )
        lineage_node_id = node.node_id

    return {
        "extracted_entities": existing_entities + new_entities,
        "pdf_sections": pdf_sections,
        "pdf_summary": pdf_summary,
        "agent_status": agent_status,
        "error_log": error_log,
        "lineage_node_id": lineage_node_id,
    }
