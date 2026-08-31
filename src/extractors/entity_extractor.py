"""
FinScope 金融实体抽取器

从研报 PDF 文本中抽取结构化金融实体：
- 公司名称
- 股票代码
- 财务指标（营收、净利润、毛利率等）
- 评级信息（买入、增持、中性等）
- 目标价
- 报告日期
"""

import re
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


def extract_financial_entities(full_text: str) -> Dict[str, Any]:
    """
    从研报文本中抽取金融实体

    Args:
        full_text: PDF 提取的全文

    Returns:
        {
            "error": False,
            "extraction": {
                "companies": [...],
                "stock_codes": [...],
                "financial_metrics": [...],
                "ratings": [...],
                "target_prices": [...],
                "report_date": "...",
                "summary": "...",
            }
        }
    """
    if not full_text or not isinstance(full_text, str):
        return {"error": True, "message": "文本内容为空"}

    extracted: Dict[str, Any] = {}

    # 1. 公司名称匹配
    company_patterns = [
        r"([一-龥]{2,8})(?:股份|集团|控股|科技|医药|证券|银行|保险|汽车|地产|能源|通信|电子|传媒|食品|饮料)",
        r"([一-龥]{2,8})(?:有限公司|有限责任公司)",
    ]
    companies_set = set()
    for pattern in company_patterns:
        matches = re.findall(pattern, full_text)
        for m in matches:
            if isinstance(m, tuple):
                companies_set.add("".join(m))
            else:
                companies_set.add(m)
    extracted["companies"] = list(companies_set)[:15]

    # 2. 股票代码匹配
    stock_codes = re.findall(r"\b([36]0\d{4}|000\d{3}|002\d{3})\b", full_text)
    extracted["stock_codes"] = list(set(stock_codes))[:10]

    # 3. 财务指标匹配
    metric_patterns = {
        "营业收入": r"营业(?:总)?收入[：:：为\s]*([\d,.]+[亿万]?)",
        "归母净利润": r"归(?:属于)?母(?:公司)?(?:股东)?(?:的)?净利润[：:：为\s]*([\d,.]+[亿万]?)",
        "毛利率": r"毛利[率][：:：为\s]*([\d.]+%)",
        "净利率": r"净利[率][：:：为\s]*([\d.]+%)",
        "ROE": r"ROE[：:：为\s]*([\d.]+%)",
        "EPS": r"(?:基本)?每股收益[：:：为\s]*([\d.]+)元?",
        "PE": r"(?:市盈率|PE)[：:：为\s]*([\d.]+)倍?",
    }
    financial_metrics = []
    for metric_name, pattern in metric_patterns.items():
        matches = re.findall(pattern, full_text)
        for m in matches[:3]:
            financial_metrics.append({
                "name": metric_name,
                "value": str(m).strip() if m else "",
            })
    extracted["financial_metrics"] = financial_metrics

    # 4. 评级与目标价匹配
    rating_patterns = [
        r"评级[：:：\s]*(买入|增持|中性|减持|卖出|跑赢|跑输|持有|强推|推荐|审慎推荐)",
        r"(?:维持|给予|上调|下调).{0,10}?(买入|增持|中性|减持|卖出|跑赢|跑输|持有|强推|推荐)评级",
    ]
    ratings_list = set()
    for pattern in rating_patterns:
        ratings_list.update(re.findall(pattern, full_text))
    extracted["ratings"] = list(ratings_list)

    target_prices = re.findall(r"目标(?:价|价格)[：:：\s]*([\d.]+)[元]?", full_text)
    extracted["target_prices"] = list(set(target_prices))[:5]

    # 5. 报告日期
    date_patterns = [
        r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2})[日]?",
        r"报告日期[：:：\s]*(\d{4}[-/]\d{1,2}[-/]\d{1,2})",
    ]
    extracted["report_date"] = "未识别"
    for pattern in date_patterns:
        match = re.search(pattern, full_text)
        if match:
            extracted["report_date"] = match.group(1).strip()
            break

    # 6. 摘要
    extracted["summary"] = full_text[:2000].strip()

    total_entities = (
        len(extracted.get("companies", []))
        + len(extracted.get("stock_codes", []))
        + len(financial_metrics)
        + len(extracted.get("ratings", []))
    )
    logger.info("[实体抽取] 完成: %d 个实体", total_entities)

    return {"error": False, "extraction": extracted}
