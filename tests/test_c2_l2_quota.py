# -*- coding: utf-8 -*-
"""[C2] L2 预留配额测试: 晚到的大章 L2 必须能注入（设计意图回归）"""
import sys
sys.path.insert(0, r'd:\develop\财报分析系统\src')
import pytest
from extractors.context_assembler import assemble


def _sec(sid, title, text, tier="T2", page=10):
    return {
        "section_id": sid, "title": title, "tier": tier,
        "page_range": [page, page + 1], "text": text, "table_ids": [],
    }


def _l1(sections):
    return {"sections": sections, "tables": [], "facts": [], "stats": {}}


SMALL = "公司坚持稳健经营，主营业务持续增长。" * 12   # ~250 tok
BIG = "公司经营情况良好，收入稳步提升，市场占有率扩大。" * 80  # ~4000 tok
L2_TEXT = "### 要点\n- 营收增长 10% [p12]\n- 净利润增长 8% [p12]"


def test_l2_quota_holds_room_for_late_big_section():
    """有 L2 缓存时: 直注让位（预算-预留），晚到大章以 L2 摘要注入而非纯指针"""
    sections = [_sec("S1", "前言", SMALL), _sec("S2", "概况", SMALL),
                _sec("S3", "经营", SMALL), _sec("S4", "大章", BIG)]
    l2 = {"S4": {"section_id": "S4", "text": L2_TEXT}}
    r = assemble("q", 800, _l1(sections), l2=l2)
    assert "S4" in r["l2_injected"], "晚到大章必须以 L2 注入（配额生效）"
    assert r["stats"]["l2_reserve"] > 0
    assert r["used"] <= 800


def test_no_l2_cache_no_reserve():
    """无 L2 缓存: 不空占预算，直注可用全额"""
    sections = [_sec("S1", "前言", SMALL), _sec("S2", "概况", SMALL),
                _sec("S3", "经营", SMALL)]
    r = assemble("q", 1200, _l1(sections), l2=None)
    assert r["stats"]["l2_reserve"] == 0
    assert r["stats"]["n_injected"] == 3, "全额预算下小章全部直注"


def test_l2_too_big_for_reserve_falls_to_pointer():
    """L2 条目连预留都放不下 → 仍退化指针（不破坏硬约束）"""
    sections = [_sec("S1", "前言", SMALL), _sec("S4", "大章", BIG)]
    big_l2 = {"S4": {"section_id": "S4", "text": "要点 " * 300}}
    r = assemble("q", 300, _l1(sections), l2=big_l2)
    assert "S4" in r["pointers"]
    assert r["used"] <= 300


def test_hard_budget_constraint_with_quota():
    """配额路径下硬约束仍成立（used ≤ budget）"""
    sections = [_sec(f"S{i}", f"章{i}", BIG) for i in range(10)]
    l2 = {f"S{i}": {"section_id": f"S{i}", "text": L2_TEXT} for i in range(10)}
    r = assemble("q", 1000, _l1(sections), l2=l2)
    assert r["used"] <= 1000
    assert len(r["l2_injected"]) >= 1
