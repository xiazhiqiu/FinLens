"""
FinScope 金融工具链

Tushare 优先 + AkShare 降级 + 模拟数据兜底:
- query_stock_info: A股股票基本信息查询
- query_financial_indicators: 上市公司财务指标查询
- extract_report_key_info: PDF研报金融实体抽取

所有工具遵循"永不抛异常"设计，异常捕获后返回结构化 JSON。
"""

import json
import re
import time
import os
import logging
from typing import Optional, Dict, Any, Callable

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# 模块级代理清理（消除VPN/系统代理残留）
_PROXY_VARS = [
    "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
    "ALL_PROXY", "all_proxy",
]
for _pv in _PROXY_VARS:
    os.environ.pop(_pv, None)
os.environ["NO_PROXY"] = "*"


# ============================================================
# 通用重试封装
# ============================================================

def safe_request(
    func: Callable,
    func_name: str = "unknown",
    max_retries: int = 2,
    base_sleep: float = 1.5,
) -> Optional[Any]:
    """安全调用数据接口，失败自动休眠重试"""
    last_error = None
    for attempt in range(1 + max_retries):
        try:
            result = func()
            if result is None:
                if attempt < max_retries:
                    time.sleep(base_sleep * (attempt + 1))
                    continue
                return None
            return result
        except (ConnectionError, ConnectionAbortedError, TimeoutError, OSError) as e:
            last_error = e
            logger.warning("[%s] 第%d次网络异常: %s", func_name, attempt + 1, str(e)[:100])
            if attempt < max_retries:
                time.sleep(base_sleep * (attempt + 1))
        except Exception as e:
            error_msg = str(e).lower()
            if any(k in error_msg for k in ["connection", "timeout", "aborted"]):
                last_error = e
                if attempt < max_retries:
                    time.sleep(base_sleep * (attempt + 1))
                    continue
            logger.error("[%s] 不可重试异常: %s", func_name, str(e)[:150])
            return None

    logger.error("[%s] 全部尝试失败: %s", func_name, str(last_error)[:150])
    return None


# ============================================================
# 模拟数据（网络不可用时兜底）
# ============================================================

_MOCK_STOCK_INFO = {
    "公司名称": "示例公司",
    "所属行业": "制造业",
    "上市日期": "2000-01-01",
    "总股本": "1000000000",
    "公司简介": "（模拟数据，需网络连通后获取真实数据）",
}

_MOCK_FINANCIAL_INDICATORS = {
    "stock_code": "000000",
    "report_period": "最近报告期",
    "total_revenue": "模拟数据",
    "net_profit": "模拟数据",
    "gross_margin": "模拟数据",
    "roe": "模拟数据",
    "eps": "模拟数据",
    "data_source": "内置模拟数据",
}


# ============================================================
# Tushare 数据获取
# ============================================================

def _tushare_query_stock_info(stock_code: str) -> Optional[Dict]:
    """使用 Tushare 查询股票基本信息"""
    try:
        import tushare as ts
        from utils.config import settings

        if not settings.TUSHARE_TOKEN:
            return None

        ts.set_token(settings.TUSHARE_TOKEN)
        pro = ts.pro_api()

        # 基本信息
        df = pro.stock_company(ts_code=f"{stock_code}.SH" if stock_code.startswith("6") else f"{stock_code}.SZ")
        if df is None or df.empty:
            return None

        row = df.iloc[0]
        return {
            "公司名称": str(row.get("company_name", "")),
            "所属行业": str(row.get("industry", "")),
            "上市日期": str(row.get("list_date", "")),
            "总股本": str(row.get("total_share", "")),
            "注册地址": str(row.get("regist_address", "")),
        }
    except Exception as e:
        logger.warning("[Tushare] 查询股票信息失败: %s", str(e)[:100])
        return None


def _tushare_query_financial(stock_code: str) -> Optional[Dict]:
    """使用 Tushare 查询财务指标"""
    try:
        import tushare as ts
        from utils.config import settings

        if not settings.TUSHARE_TOKEN:
            return None

        ts.set_token(settings.TUSHARE_TOKEN)
        pro = ts.pro_api()

        ts_code = f"{stock_code}.SH" if stock_code.startswith("6") else f"{stock_code}.SZ"

        # 利润表
        income = pro.income(ts_code=ts_code, limit=1)
        if income is None or income.empty:
            return None

        row = income.iloc[0]
        return {
            "stock_code": stock_code,
            "report_period": str(row.get("end_date", "")),
            "total_revenue": str(row.get("revenue", "N/A")),
            "net_profit": str(row.get("n_income_attr_p", "N/A")),
            "revenue_yoy_growth": str(row.get("revenue_yoy", "N/A")),
            "net_profit_yoy_growth": str(row.get("n_income_attr_p_yoy", "N/A")),
            "data_source": "Tushare",
        }
    except Exception as e:
        logger.warning("[Tushare] 查询财务指标失败: %s", str(e)[:100])
        return None


# ============================================================
# AkShare 数据获取（降级方案）
# ============================================================

def _akshare_query_stock_info(stock_code: str) -> Optional[Dict]:
    """使用 AkShare 查询股票基本信息"""
    try:
        import akshare as ak

        df = safe_request(
            func=lambda: ak.stock_individual_info_em(symbol=stock_code),
            func_name="stock_individual_info_em",
        )

        if df is None or (hasattr(df, "empty") and df.empty):
            return None

        info = {}
        for _, row in df.iterrows():
            key = str(row.get("item", ""))
            value = str(row.get("value", ""))
            if key and value and value.lower() not in ("none", "nan", ""):
                info[key] = value
        return info if info else None

    except Exception as e:
        logger.warning("[AkShare] 查询股票信息失败: %s", str(e)[:100])
        return None


def _akshare_query_financial(stock_code: str) -> Optional[Dict]:
    """使用 AkShare 查询财务指标"""
    try:
        import akshare as ak

        df = safe_request(
            func=lambda: ak.stock_profit_sheet_by_report_em(symbol=stock_code),
            func_name="stock_profit_sheet_by_report_em",
        )

        if df is None or (hasattr(df, "empty") and df.empty):
            return None

        latest = df.head(1).to_dict("records")[0]
        return {
            "stock_code": stock_code,
            "report_period": str(latest.get("报告期", "N/A")),
            "total_revenue": str(latest.get("营业总收入", "N/A")),
            "net_profit": str(latest.get("归母净利润", "N/A")),
            "gross_margin": str(latest.get("毛利率", "N/A")),
            "net_margin": str(latest.get("净利率", "N/A")),
            "roe": str(latest.get("净资产收益率", "N/A")),
            "eps": str(latest.get("基本每股收益", "N/A")),
            "revenue_yoy_growth": str(latest.get("营业总收入同比增长率", "N/A")),
            "net_profit_yoy_growth": str(latest.get("归母净利润同比增长率", "N/A")),
            "data_source": "AkShare (东方财富)",
        }

    except Exception as e:
        logger.warning("[AkShare] 查询财务指标失败: %s", str(e)[:100])
        return None


# ============================================================
# LangChain 工具定义
# ============================================================

@tool
def query_stock_info(stock_code: str) -> str:
    """
    查询A股股票基本信息，包括公司全称、所属行业、上市日期、总股本等。

    参数:
        stock_code: A股股票代码，如 "600196"（复星医药）、"000001"（平安银行）

    返回:
        JSON格式字符串，包含公司名称、行业、上市日期等信息
    """
    logger.info("[Tool] query_stock_info: stock_code=%s", stock_code)

    info_dict = None
    data_source = "未知"

    # 优先 Tushare
    from utils.config import settings
    if settings.TUSHARE_PRIORITY and settings.TUSHARE_TOKEN:
        info_dict = _tushare_query_stock_info(stock_code)
        if info_dict:
            data_source = "Tushare"

    # 降级 AkShare
    if not info_dict:
        info_dict = _akshare_query_stock_info(stock_code)
        if info_dict:
            data_source = "AkShare (东方财富)"

    # 兜底模拟数据
    if not info_dict:
        info_dict = dict(_MOCK_STOCK_INFO)
        info_dict["股票代码"] = stock_code
        data_source = "内置模拟数据"

    result = {
        "error": False,
        "stock_code": stock_code,
        "info": info_dict,
        "data_source": data_source,
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


@tool
def query_financial_indicators(stock_code: str, report_period: str = "latest") -> str:
    """
    查询上市公司核心财务指标，包括营业收入、净利润、毛利率、ROE、EPS等。

    参数:
        stock_code: A股股票代码
        report_period: 报告期，默认 "latest"

    返回:
        JSON格式字符串，包含财务指标数据
    """
    logger.info("[Tool] query_financial_indicators: stock_code=%s", stock_code)

    indicators = None
    data_source = "未知"

    # 优先 Tushare
    from utils.config import settings
    if settings.TUSHARE_PRIORITY and settings.TUSHARE_TOKEN:
        indicators = _tushare_query_financial(stock_code)
        if indicators:
            data_source = "Tushare"

    # 降级 AkShare
    if not indicators:
        indicators = _akshare_query_financial(stock_code)
        if indicators:
            data_source = "AkShare (东方财富)"

    # 兜底模拟数据
    if not indicators:
        indicators = dict(_MOCK_FINANCIAL_INDICATORS)
        indicators["stock_code"] = stock_code
        data_source = "内置模拟数据"

    if "data_source" not in indicators:
        indicators["data_source"] = data_source

    result = {"error": False, "indicators": indicators}
    return json.dumps(result, ensure_ascii=False, indent=2)


@tool
def extract_report_key_info(pdf_path: str) -> str:
    """
    从PDF金融研报中抽取关键实体信息，包括公司名称、股票代码、财务指标、评级、目标价等。

    参数:
        pdf_path: 研报PDF文件的本地路径

    返回:
        JSON格式字符串，包含抽取的结构化实体
    """
    logger.info("[Tool] extract_report_key_info: pdf_path=%s", pdf_path)

    if not pdf_path or not isinstance(pdf_path, str) or not pdf_path.strip():
        return json.dumps({"error": True, "message": "PDF路径参数为空"}, ensure_ascii=False)

    try:
        from extractors.mineru_extractor import extract_pdf_text
        from extractors.entity_extractor import extract_financial_entities

        # 提取文本
        text_result = extract_pdf_text(pdf_path.strip())
        if text_result.get("error"):
            return json.dumps(text_result, ensure_ascii=False, indent=2)

        # 抽取实体
        entity_result = extract_financial_entities(text_result["full_text"])
        if entity_result.get("error"):
            return json.dumps(entity_result, ensure_ascii=False, indent=2)

        # 补充元信息
        extraction = entity_result["extraction"]
        extraction["total_pages"] = text_result.get("total_pages", "N/A")
        extraction["file_path"] = pdf_path
        extraction["text_source"] = text_result.get("source", "unknown")

        return json.dumps({"error": False, "extraction": extraction}, ensure_ascii=False, indent=2)

    except ImportError as e:
        return json.dumps({"error": True, "message": f"缺少依赖: {str(e)[:100]}"}, ensure_ascii=False)
    except Exception as e:
        logger.error("[Tool] extract_report_key_info 异常: %s", e)
        return json.dumps({"error": True, "message": f"PDF解析失败: {str(e)[:200]}"}, ensure_ascii=False)


# 工具列表
FINANCIAL_TOOLS = [query_stock_info, query_financial_indicators, extract_report_key_info]

TOOL_MAP = {
    "query_stock_info": query_stock_info,
    "query_financial_indicators": query_financial_indicators,
    "extract_report_key_info": extract_report_key_info,
}
