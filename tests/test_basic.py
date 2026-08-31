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
