"""
P1 修复回归测试

覆盖:
1. P1-8 配置接线: supervisor / financial_graph 的熔断与限频参数必须读全局配置
2. P0-4 MinerU 输出路径: _find_content_list 递归查找（兼容新版子目录输出）
"""

import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_config_wiring_hardcoded_constants():
    """熔断/限频参数必须来自配置，改配置要生效（此前硬编码 15/3）"""
    from agents.supervisor import MAX_ITERATIONS as SUP_ITER, MAX_CONSECUTIVE_CALLS as SUP_CONS
    from graphs.financial_graph import MAX_ITERATIONS as GRAPH_ITER
    from utils.config import get_settings

    settings = get_settings()
    assert SUP_ITER == settings.MAX_AGENT_ITERATIONS, f"supervisor 迭代上限未接线: {SUP_ITER}"
    assert SUP_CONS == settings.SINGLE_AGENT_MAX_CALLS, f"supervisor 连续调用上限未接线: {SUP_CONS}"
    assert GRAPH_ITER == settings.MAX_AGENT_ITERATIONS, f"graph 迭代上限未接线: {GRAPH_ITER}"
    print("PASS: 熔断/限频参数已接线到全局配置")


def test_mineru_find_content_list_recursive():
    """MinerU 输出在 <output>/<pdf名>/<mode>/ 子目录，必须递归查找得到"""
    from extractors.mineru_extractor import _find_content_list

    tmp = Path(tempfile.mkdtemp(prefix="finscope_mineru_"))

    # 新版子目录结构
    nested = tmp / "复星医药研报" / "auto"
    nested.mkdir(parents=True)
    (nested / "content_list.json").write_text("[]", encoding="utf-8")
    found = _find_content_list(str(tmp))
    assert found is not None and found.name == "content_list.json", "递归查找必须命中子目录输出"

    # 旧版平铺结构
    tmp2 = Path(tempfile.mkdtemp(prefix="finscope_mineru_"))
    (tmp2 / "content_list.json").write_text("[]", encoding="utf-8")
    assert _find_content_list(str(tmp2)) is not None

    # 不存在
    tmp3 = Path(tempfile.mkdtemp(prefix="finscope_mineru_"))
    assert _find_content_list(str(tmp3)) is None
    assert _find_content_list(str(tmp3 / "nonexistent")) is None
    print("PASS: MinerU content_list 递归查找（子目录/平铺/不存在）")


if __name__ == "__main__":
    test_config_wiring_hardcoded_constants()
    test_mineru_find_content_list_recursive()
    print("\n全部通过 ✓")
