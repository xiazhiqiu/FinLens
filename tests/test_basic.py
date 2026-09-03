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
    assert state["pdf_l1"] == {}


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
# [A4] 旧 page_compressor 及 pdf_sections/pdf_summary 链路已删除
# （P4 起唯一路径: L1 结构化 + 预算装配，测试见 test_p2_context / test_a1_writer / test_a2_reviewer）
# ============================================================

