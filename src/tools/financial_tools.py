"""
FinScope 金融工具链

Tushare 优先 + AkShare 降级:
- query_stock_info: A股股票基本信息查询
- query_financial_indicators: 上市公司财务指标查询
- extract_report_key_info: PDF研报金融实体抽取

所有工具遵循"永不抛异常"设计，异常捕获后返回结构化 JSON。
无数据源时返回 error，不使用模拟数据。
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

def _em_market_symbol(stock_code: str) -> str:
    """将裸 6 位 A 股代码转为东财系接口要求的交易所前缀格式

    stock_profit_sheet_by_report_em 等 EM 报表接口要求 SH600196/SZ000001 格式，
    传裸代码（600196）会内部解析失败（实测 'NoneType' object is not subscriptable）。
    规则: 6 开头→SH（沪），0/3 开头→SZ（深），4/8/9 开头→BJ（北交所）。
    """
    code = str(stock_code).strip().upper()
    if not code.isdigit() or len(code) != 6:
        return code  # 已带前缀或非标准格式，原样返回
    if code.startswith("6"):
        return f"SH{code}"
    if code.startswith(("0", "3")):
        return f"SZ{code}"
    if code.startswith(("4", "8", "9")):
        return f"BJ{code}"
    return code


def _akshare_query_stock_info(stock_code: str) -> Optional[Dict]:
    """使用 AkShare 查询股票基本信息（双接口降级: 东财个股信息 → 巨潮公司概况）"""
    try:
        import akshare as ak

        # 路径1: 东方财富个股信息（item/value 竖表）
        df = safe_request(
            func=lambda: ak.stock_individual_info_em(symbol=stock_code),
            func_name="stock_individual_info_em",
            max_retries=2,
        )
        if df is not None and not (hasattr(df, "empty") and df.empty):
            info = {}
            for _, row in df.iterrows():
                key = str(row.get("item", ""))
                value = str(row.get("value", ""))
                if key and value and value.lower() not in ("none", "nan", ""):
                    info[key] = value
            if info:
                info["_endpoint"] = "eastmoney"
                return info
        logger.info("[AkShare] stock_individual_info_em 无数据/不可达，降级巨潮 stock_profile_cninfo")

        # 路径2: 巨潮资讯公司概况（EM 反爬断连时的备用，实测可用）
        df2 = safe_request(
            func=lambda: ak.stock_profile_cninfo(symbol=str(stock_code).strip().zfill(6)),
            func_name="stock_profile_cninfo",
            max_retries=1,
        )
        if df2 is not None and not (hasattr(df2, "empty") and df2.empty):
            row = df2.iloc[0].to_dict()
            info = {}
            col_map = {
                "公司名称": "公司全称",
                "A股简称": "公司简称",
                "所属行业": "所属行业",
                "上市日期": "上市日期",
                "成立日期": "成立日期",
                "注册资金": "注册资本",
                "官方网站": "官方网站",
            }
            for src, dst in col_map.items():
                value = str(row.get(src, "")).strip()
                if value and value.lower() not in ("none", "nan", ""):
                    info[dst] = value
            if info:
                info["_endpoint"] = "cninfo"
                return info

        return None

    except Exception as e:
        logger.warning("[AkShare] 查询股票信息失败: %s", str(e)[:100])
        return None


def _akshare_query_financial(stock_code: str) -> Optional[Dict]:
    """使用 AkShare 查询财务指标（EM 报表接口需交易所前缀格式，见 _em_market_symbol）"""
    try:
        import akshare as ak

        em_symbol = _em_market_symbol(stock_code)
        df = safe_request(
            func=lambda: ak.stock_profit_sheet_by_report_em(symbol=em_symbol),
            func_name="stock_profit_sheet_by_report_em",
        )

        if df is None or (hasattr(df, "empty") and df.empty):
            return None

        # EM 报表接口列为英文代码（REPORT_DATE/TOTAL_OPERATE_INCOME/...），按实际列名映射
        latest = df.iloc[0].to_dict()

        def _num(key: str) -> Optional[float]:
            try:
                v = latest.get(key)
                return float(v) if v is not None and str(v).lower() != "nan" else None
            except (TypeError, ValueError):
                return None

        def _fmt_yi(v: Optional[float]) -> str:
            return f"{v / 1e8:.2f}亿元" if v is not None else "N/A"

        def _fmt_pct(v: Optional[float]) -> str:
            return f"{v:.2f}%" if v is not None else "N/A"

        revenue = _num("TOTAL_OPERATE_INCOME")
        parent_profit = _num("PARENT_NETPROFIT")
        operate_cost = _num("OPERATE_COST")

        gross_margin = (
            (revenue - operate_cost) / revenue * 100
            if revenue and revenue > 0 and operate_cost is not None
            else None
        )
        net_margin = (
            parent_profit / revenue * 100
            if revenue and revenue > 0 and parent_profit is not None
            else None
        )

        report_date = str(latest.get("REPORT_DATE", "") or "")[:10] or "N/A"

        return {
            "stock_code": stock_code,
            "report_period": report_date,
            "total_revenue": _fmt_yi(revenue),
            "net_profit": _fmt_yi(parent_profit),
            "gross_margin": _fmt_pct(gross_margin),
            "net_margin": _fmt_pct(net_margin),
            "roe": "N/A",  # 利润表不含 ROE，需资产负债表口径，留待后续扩展
            "eps": str(latest.get("BASIC_EPS", "N/A")),
            "revenue_yoy_growth": _fmt_pct(_num("TOTAL_OPERATE_INCOME_YOY")),
            "net_profit_yoy_growth": _fmt_pct(_num("PARENT_NETPROFIT_YOY")),
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
            # [血缘诚实性] 按实际命中接口标注来源（东财个股信息 / 巨潮公司概况）
            endpoint = info_dict.pop("_endpoint", "eastmoney")
            data_source = "AkShare (巨潮资讯)" if endpoint == "cninfo" else "AkShare (东方财富)"

    # 无数据源可用
    if not info_dict:
        result = {
            "error": True,
            "message": f"未配置数据源或查询失败 (stock_code={stock_code})。请配置 TUSHARE_TOKEN 或检查网络连接。",
            "stock_code": stock_code,
        }
        return json.dumps(result, ensure_ascii=False, indent=2)

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

    # 无数据源可用
    if not indicators:
        result = {
            "error": True,
            "message": f"未配置数据源或查询失败 (stock_code={stock_code})。请配置 TUSHARE_TOKEN 或检查网络连接。",
        }
        return json.dumps(result, ensure_ascii=False, indent=2)

    if "data_source" not in indicators:
        indicators["data_source"] = data_source

    result = {"error": False, "indicators": indicators}
    return json.dumps(result, ensure_ascii=False, indent=2)


@tool
def extract_report_key_info(pdf_path: str) -> str:
    """
    从PDF金融研报中抽取关键实体信息，包括公司名称、股票代码、财务指标、评级、目标价等。
    同时返回结构化页面数据（用于后续 LLM 压缩）。

    参数:
        pdf_path: 研报PDF文件的本地路径

    返回:
        JSON格式字符串，包含抽取的结构化实体和结构化页面
    """
    logger.info("[Tool] extract_report_key_info: pdf_path=%s", pdf_path)

    if not pdf_path or not isinstance(pdf_path, str) or not pdf_path.strip():
        return json.dumps({"error": True, "message": "PDF路径参数为空"}, ensure_ascii=False)

    try:
        from extractors.mineru_extractor import extract_pdf_text
        from extractors.entity_extractor import extract_financial_entities

        pdf_path = pdf_path.strip()

        def _parse_and_extract() -> dict:
            """解析 + 实体抽取 + L1 构建（miss 时的完整链路）"""
            # 提取文本（含结构化页面）
            text_result = extract_pdf_text(pdf_path)
            if text_result.get("error"):
                return text_result

            # 抽取实体
            entity_result = extract_financial_entities(text_result["full_text"])
            if entity_result.get("error"):
                return entity_result

            # 补充元信息
            extraction = entity_result["extraction"]
            extraction["total_pages"] = text_result.get("total_pages", "N/A")
            extraction["file_path"] = pdf_path
            extraction["text_source"] = text_result.get("source", "unknown")

            # 添加结构化页面数据（用于后续压缩）
            extraction["structured_pages"] = text_result.get("structured_pages", [])

            # [P1] L1 结构化无损层（确定性、零 LLM）: 与解析结果一同落缓存，
            # 命中缓存时零重建。L1 构建失败不阻断主链路（never-throw）。
            try:
                from extractors.l1_builder import build_l1
                extraction["l1"] = build_l1(
                    extraction["structured_pages"],
                    companies=extraction.get("companies", []),
                )
            except Exception as e:
                logger.warning("[Tool] L1 构建失败（不阻断解析链路）: %s", str(e)[:150])
                extraction["l1"] = None

            return {"error": False, "extraction": extraction}

        # ---- 解析缓存（内容 SHA-256 键控；任何缓存故障不阻断主链路）----
        cache = None
        pdf_hash = None
        try:
            from extractors.parse_cache import compute_pdf_hash, get_parse_cache
            cache = get_parse_cache()
            if cache:
                pdf_hash = compute_pdf_hash(pdf_path)
        except Exception as e:
            logger.warning("[Tool] 缓存初始化失败，走无缓存路径: %s", e)
            cache = None

        if cache and pdf_hash:
            with cache.inflight(pdf_hash):
                cached = cache.get(pdf_hash)
                if cached is not None:
                    extraction = cached.get("extraction", {})
                    extraction["file_path"] = pdf_path  # 同内容不同路径的请求，刷新为当前路径
                    extraction["cache_hit"] = True
                    extraction["cached_at"] = cached.get("_cached_at", "")
                    extraction["parse_source"] = cached.get("_cache_parser", "unknown")
                    logger.info(
                        "[Tool] 解析缓存命中: %s (解析于 %s, 来源 %s)",
                        pdf_hash[:12], extraction["cached_at"], extraction["parse_source"],
                    )
                    return json.dumps({"error": False, "extraction": extraction}, ensure_ascii=False, indent=2)

                result = _parse_and_extract()
                if not result.get("error"):
                    parser = result["extraction"].get("text_source", "unknown")
                    result["extraction"]["cache_hit"] = False
                    cache.put(pdf_hash, result, parser=parser)
                return json.dumps(result, ensure_ascii=False, indent=2)

        # 无缓存可用: 原路径直查
        result = _parse_and_extract()
        if not result.get("error"):
            result["extraction"]["cache_hit"] = False
        return json.dumps(result, ensure_ascii=False, indent=2)

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
