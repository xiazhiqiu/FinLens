"""
FinScope 测试
"""

import pytest


def test_config_loads():
    """测试配置加载"""
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

    from utils.config import get_settings
    settings = get_settings()
    assert settings.LLM_PROVIDER in ["deepseek", "openai", "ollama"]
    assert settings.MAX_AGENT_ITERATIONS == 15


def test_state_creation():
    """测试状态创建"""
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

    from graphs.state import create_initial_state
    state = create_initial_state("测试查询", "company", "/path/to.pdf")

    assert state["user_query"] == "测试查询"
    assert state["report_type"] == "company"
    assert state["pdf_path"] == "/path/to.pdf"
    assert state["iteration_count"] == 0
    assert state["extracted_entities"] == []
    assert state["financial_data"] == {}
    assert state["pdf_sections"] == []
    assert state["pdf_summary"] == ""


def test_entity_extractor():
    """测试实体抽取"""
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

    from extractors.entity_extractor import extract_financial_entities

    text = """
    复星医药(600196.SH)发布2024年三季报。
    公司营业收入为309.21亿元，同比增长5.73%。
    归母净利润为31.85亿元，同比增长8.92%。
    毛利率为48.75%。
    评级：买入，目标价：45.00元。
    报告日期：2024年10月30日。
    """

    result = extract_financial_entities(text)
    assert result["error"] is False

    extraction = result["extraction"]
    assert "复星医药" in extraction["companies"] or len(extraction["companies"]) > 0
    assert "600196" in extraction["stock_codes"]
    assert len(extraction["financial_metrics"]) > 0
    assert "买入" in extraction["ratings"]
    assert "45.00" in extraction["target_prices"]


def test_validate_data_source():
    """测试数据源配置校验"""
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

    from utils.config import get_settings
    settings = get_settings()

    result = settings.validate_data_source()
    assert "configured" in result
    assert "status" in result
    assert "message" in result
    assert result["status"] in ["ready", "degraded", "unavailable"]


def test_financial_tools_no_token():
    """测试无 Token 时金融工具返回错误"""
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

    from tools.financial_tools import query_stock_info, query_financial_indicators
    import json

    # 测试股票信息查询（无 Token 时应返回 error 或成功，不返回模拟数据）
    result_str = query_stock_info.invoke({"stock_code": "600196"})
    result = json.loads(result_str)

    # 验证：要么成功（有真实数据），要么失败（有 error），但不能是模拟数据
    if result.get("error"):
        assert "message" in result
        assert "模拟" not in result.get("message", "")
    else:
        # 成功时验证数据来源
        assert result.get("data_source") != "内置模拟数据"


# ============================================================
# 页面压缩器测试
# ============================================================

def test_page_compressor_empty_input():
    """测试页面压缩器：空输入"""
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

    from extractors.page_compressor import compress_pages

    result = compress_pages([], use_llm=False)
    assert result["error"] is True
    assert "无结构化页面数据" in result["message"]


def test_page_compressor_rule_fallback():
    """测试页面压缩器：规则压缩降级"""
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

    from extractors.page_compressor import compress_pages

    # 模拟结构化页面（不调用 LLM）
    structured_pages = [
        {
            "page_idx": 0,
            "items": [
                {"type": "text", "content": "公司营业收入为309.21亿元，同比增长5.73%。", "bbox": [0, 0, 100, 100]},
                {"type": "text", "content": "归母净利润为31.85亿元，同比增长8.92%。", "bbox": [0, 100, 100, 200]},
            ],
        },
        {
            "page_idx": 1,
            "items": [
                {"type": "table", "content": "<table><tr><td>指标</td><td>数值</td></tr><tr><td>毛利率</td><td>48.75%</td></tr></table>", "bbox": [0, 0, 200, 100]},
                {"type": "text", "content": "公司面临市场风险和政策风险。", "bbox": [0, 100, 200, 200]},
            ],
        },
    ]

    result = compress_pages(structured_pages, use_llm=False)

    assert result["error"] is False
    assert result["total_pages"] == 2
    assert result["rule_compressed_count"] == 2
    assert result["llm_compressed_count"] == 0

    # 验证压缩结果
    pages = result["compressed_pages"]
    assert len(pages) == 2

    # Page 0 应该有关键要点
    page0 = pages[0]
    assert page0["page_idx"] == 0
    assert len(page0["key_points"]) > 0
    assert "营业收入" in page0["key_points"][0] or "增长" in page0["key_points"][0]

    # Page 1 应该有表格和财务数据
    page1 = pages[1]
    assert page1["page_idx"] == 1
    assert len(page1["tables"]) > 0
    # 表格数据可能以表头为 key（如 "指标"），或以实际内容为 key
    assert len(page1["financial_data"]) > 0


def test_page_compressor_financial_data_extraction():
    """测试页面压缩器：财务数据提取"""
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

    from extractors.page_compressor import _extract_financial_data_from_text

    text = "公司营业收入为309.21亿元，同比增长5.73%。归母净利润为31.85亿元。毛利率为48.75%。"

    data = _extract_financial_data_from_text(text)

    assert "营业收入" in data
    assert "309.21亿元" in data["营业收入"]
    assert "归母净利润" in data
    assert "31.85亿元" in data["归母净利润"]
    assert "毛利率" in data
    assert "48.75%" in data["毛利率"]


def test_page_compressor_summary_generation():
    """测试页面压缩器：摘要生成"""
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

    from extractors.page_compressor import _generate_summary

    compressed_pages = [
        {
            "page_idx": 0,
            "key_points": ["公司营收增长5.73%", "净利润增长8.92%"],
            "financial_data": {"营业收入": "309.21亿元"},
            "tables": [],
        },
        {
            "page_idx": 1,
            "key_points": ["公司面临市场风险"],
            "financial_data": {"毛利率": "48.75%"},
            "tables": [],
        },
    ]

    summary = _generate_summary(compressed_pages)

    assert "关键要点" in summary
    assert "营收增长" in summary
    assert "核心财务数据" in summary
    assert "309.21亿元" in summary


def test_page_compressor_items_to_text():
    """测试页面压缩器：items 转文本"""
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

    from extractors.page_compressor import _items_to_text

    items = [
        {"type": "header", "content": "第一章 公司概况", "level": 1, "bbox": [0, 0, 100, 50]},
        {"type": "text", "content": "复星医药是一家上市公司。", "bbox": [0, 50, 100, 100]},
        {"type": "table", "content": "<table><tr><td>数据</td></tr></table>", "bbox": [0, 100, 100, 200]},
    ]

    text = _items_to_text(items)

    assert "# 第一章 公司概况" in text
    assert "复星医药是一家上市公司" in text
    assert "数据" in text
