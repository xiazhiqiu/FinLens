"""
FinScope DataRetriever Agent

负责:
1. 从 extracted_entities 中提取股票代码
2. 并行调用 query_stock_info + query_financial_indicators
3. 清洗、整合返回数据，存入 financial_data
4. [企业级] 记录数据血缘节点
"""

import json
import re
import logging
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from graphs.state import FinancialAnalysisState
from tools.financial_tools import query_stock_info, query_financial_indicators

# 企业级模块（可选导入）
try:
    from audit.data_lineage import DataLineage, DataSourceType, TransformationType, get_lineage
    ENTERPRISE_MODE = True
except ImportError:
    ENTERPRISE_MODE = False

logger = logging.getLogger(__name__)


def _get_data_lineage() -> "DataLineage":
    """获取全进程共享的数据血缘单例（跨 Agent 可见，修复此前各自 new 导致的溯源失效）"""
    return get_lineage() if ENTERPRISE_MODE else None


def _extract_stock_codes(entities: List[Dict[str, Any]]) -> List[str]:
    """从实体中提取股票代码"""
    codes = []
    for entity in entities:
        if entity.get("entity_type") == "stock_code":
            code = entity.get("entity_name", "")
            if code and len(code) == 6 and code.isdigit():
                codes.append(code)

    seen = set()
    unique_codes = []
    for c in codes:
        if c not in seen:
            seen.add(c)
            unique_codes.append(c)
    return unique_codes[:5]


def _safe_tool_call(tool_func, params: dict, tool_name: str) -> Optional[Dict]:
    """安全调用单个工具"""
    try:
        result_str = tool_func.invoke(params)
        return json.loads(result_str)
    except Exception as e:
        logger.error("[%s] 调用异常: %s", tool_name, e)
        return None


def data_retriever_node(state: FinancialAnalysisState) -> Dict[str, Any]:
    """金融数据检索节点"""
    agent_status = dict(state.get("agent_status", {}))
    error_log = list(state.get("error_log", []))
    entities = state.get("extracted_entities", [])
    existing_financial_data = dict(state.get("financial_data", {}))

    # 提取股票代码
    stock_codes = _extract_stock_codes(entities)

    if not stock_codes:
        user_query = state.get("user_query", "")
        found_codes = re.findall(r"\b([36]0\d{4}|000\d{3}|002\d{3})\b", user_query)
        stock_codes = list(set(found_codes))[:3]

    if not stock_codes:
        logger.warning("未找到股票代码，数据检索跳过")
        agent_status["data_retriever"] = "done"
        return {"agent_status": agent_status}

    logger.info("待检索股票代码: %s", stock_codes)

    # 并行调用工具
    primary_code = stock_codes[0]
    stock_info_result = None
    financial_result = None

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_stock = executor.submit(_safe_tool_call, query_stock_info, {"stock_code": primary_code}, "query_stock_info")
        future_financial = executor.submit(_safe_tool_call, query_financial_indicators, {"stock_code": primary_code}, "query_financial_indicators")

        for future in as_completed([future_stock, future_financial]):
            try:
                if future == future_stock:
                    stock_info_result = future.result()
                else:
                    financial_result = future.result()
            except Exception as e:
                error_log.append(f"[DataRetriever] 并行调用异常: {e}")

    # 数据清洗与整合
    financial_data = dict(existing_financial_data)

    if stock_info_result and not stock_info_result.get("error"):
        info = stock_info_result.get("info", {})
        financial_data["stock_info"] = {
            "code": primary_code,
            "name": info.get("公司名称", "未知"),
            "industry": info.get("所属行业") or info.get("行业", "未知"),  # Tushare 返回"所属行业"，AkShare 返回"行业"
            "listing_date": info.get("上市日期", "未知"),
            "total_shares": info.get("总股本", "N/A"),
            "source": stock_info_result.get("data_source", "未知"),
        }
        logger.info("股票基本信息查询成功: %s", info.get("公司名称", primary_code))
    else:
        error_msg = stock_info_result.get("message", "未知错误") if stock_info_result else "工具调用失败"
        error_log.append(f"[DataRetriever] 股票信息查询失败 ({primary_code}): {error_msg}")

    if financial_result and not financial_result.get("error"):
        indicators = financial_result.get("indicators", {})
        financial_data["financial_indicators"] = indicators
        logger.info("财务指标查询成功: period=%s", indicators.get("report_period", "N/A"))
    else:
        error_msg = financial_result.get("message", "未知错误") if financial_result else "工具调用失败"
        error_log.append(f"[DataRetriever] 财务指标查询失败 ({primary_code}): {error_msg}")

    if len(stock_codes) > 1:
        financial_data["additional_codes"] = stock_codes[1:]

    agent_status["data_retriever"] = "done"

    # [企业级] 记录数据血缘节点
    lineage_node_id = ""
    lineage = _get_data_lineage()
    if lineage:
        # 按实际命中的数据源记录血缘（修复此前降级到 AkShare 仍写死 Tushare 的问题）
        actual_source = (
            financial_data.get("financial_indicators", {}).get("data_source", "")
            or financial_data.get("stock_info", {}).get("source", "")
        )
        if "Tushare" in actual_source:
            lineage_source_type = DataSourceType.TUSHARE
        elif "AkShare" in actual_source:
            lineage_source_type = DataSourceType.AKSHARE
        else:
            lineage_source_type = DataSourceType.API

        # 创建数据源节点
        source_node = lineage.create_source_node(
            name=f"{actual_source or '金融数据'}: {primary_code}",
            source_type=lineage_source_type,
            metadata={"stock_code": primary_code, "data_keys": list(financial_data.keys()), "source": actual_source},
        )
        # 获取上游节点（来自 report_extractor 的 lineage_node_id）
        upstream_id = state.get("lineage_node_id", "")
        parent_ids = [source_node.node_id]
        if upstream_id:
            parent_ids.append(upstream_id)

        # 创建派生节点
        derived_node = lineage.create_derived_node(
            name=f"财务数据整合: {primary_code}",
            source_type=DataSourceType.MANUAL,
            parent_ids=parent_ids,
            metadata={"stock_code": primary_code, "indicator_count": len(financial_data.get("financial_indicators", {}))},
        )
        # 记录转换
        lineage.record_transformation(
            transform_type=TransformationType.AGGREGATE,
            description=f"合并研报抽取 + 股票数据: {primary_code}",
            input_node_ids=parent_ids,
            output_node_id=derived_node.node_id,
        )
        lineage_node_id = derived_node.node_id

    return {
        "financial_data": financial_data,
        "agent_status": agent_status,
        "error_log": error_log,
        "lineage_node_id": lineage_node_id,
    }
