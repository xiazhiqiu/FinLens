"""
L1 地基流水线测试（P1，纯规则零 LLM）

覆盖: heading 归一化 / 章节切分 / T0-T3 分档 / 表格序列化 / 事实抽取 / 去重

数据来源说明:
- 结构断言类用例使用构造的 fixture（可离线、确定性）
- 真实年报用例见 tests/test_l1_real_joinn.py（样本缺失时自动 skip）
"""

import pytest

from extractors.mineru_extractor import _content_list_to_structured_pages
from extractors.section_segmenter import classify_tier, segment_sections
from extractors.table_serializer import build_table_record, parse_table_html, serialize_table
from extractors.fact_extractor import extract_facts, parse_number
from extractors.l1_builder import build_l1


# ---------------------------------------------------------------------------
# 1. heading 归一化（真实年报实证: header=页眉噪声, text+text_level>0=真标题）
# ---------------------------------------------------------------------------

def test_heading_normalization_text_level_is_heading():
    """真实年报语义: type=text 且 text_level>0 才是章节标题"""
    cl = [
        {"type": "text", "text": "財務表現", "text_level": 2, "page_idx": 0},
        {"type": "text", "text": "公司業績穩健增長。", "text_level": 0, "page_idx": 0},
    ]
    pages = _content_list_to_structured_pages(cl)
    items = pages[0]["items"]
    assert items[0]["type"] == "heading"
    assert items[0]["level"] == 2
    assert items[1]["type"] == "text"


def test_heading_normalization_drops_page_noise():
    """页眉(header)/页脚(footer)/页码(page_number) 必须在入口丢弃"""
    cl = [
        {"type": "header", "text": "釋義", "page_idx": 2},
        {"type": "footer", "text": "2024年度報告", "page_idx": 2},
        {"type": "page_number", "text": "3", "page_idx": 2},
        {"type": "text", "text": "正文內容", "text_level": 0, "page_idx": 2},
    ]
    items = _content_list_to_structured_pages(cl)[0]["items"]
    assert [i["type"] for i in items] == ["text"], "页眉/页脚/页码不应进入结构化输出"


def test_heading_normalization_accepts_all_spellings():
    """兼容各 MinerU 版本: title / header / heading 拼写凡 text_level>0 均识别"""
    cl = [
        {"type": "title", "text": "章节A", "text_level": 1, "page_idx": 0},
        {"type": "heading", "text": "章节B", "text_level": 2, "page_idx": 0},
    ]
    items = _content_list_to_structured_pages(cl)[0]["items"]
    assert [i["type"] for i in items] == ["heading", "heading"]
    assert [i["level"] for i in items] == [1, 2]


def test_table_item_keeps_html_and_caption():
    cl = [{
        "type": "table", "page_idx": 5,
        "table_body": "<table><tr><td>a</td><td>b</td></tr></table>",
        "table_caption": ["利润表"], "table_footnote": [],
    }]
    item = _content_list_to_structured_pages(cl)[0]["items"][0]
    assert item["type"] == "table"
    assert item["content"].startswith("<table>")
    assert item["caption"] == ["利润表"]


# ---------------------------------------------------------------------------
# 2. 章节切分
# ---------------------------------------------------------------------------

def test_segment_three_headings():
    pages = [{
        "page_idx": 0,
        "items": [
            {"type": "heading", "content": "第一章", "level": 1},
            {"type": "text", "content": "内容一"},
            {"type": "heading", "content": "第二章", "level": 1},
            {"type": "text", "content": "内容二"},
            {"type": "heading", "content": "第三章", "level": 2},
            {"type": "text", "content": "内容三"},
        ],
    }]
    secs = segment_sections(pages)
    assert len(secs) == 3
    assert [s["title"] for s in secs] == ["第一章", "第二章", "第三章"]
    assert secs[0]["text"] == "内容一\n"
    assert [s["section_id"] for s in secs] == ["s_001", "s_002", "s_003"]


def test_segment_merges_cross_page_continuation():
    """跨页同标题（带「（續）」后缀）应合并为一节，不碎片化"""
    pages = [
        {"page_idx": 10, "items": [
            {"type": "heading", "content": "綜合損益表", "level": 2},
            {"type": "text", "content": "上半部分"},
        ]},
        {"page_idx": 11, "items": [
            {"type": "heading", "content": "綜合損益表（續）", "level": 2},
            {"type": "text", "content": "下半部分"},
        ]},
    ]
    secs = segment_sections(pages)
    assert len(secs) == 1, "跨页续节必须合并"
    assert secs[0]["page_range"] == [10, 11]
    assert "上半部分" in secs[0]["text"] and "下半部分" in secs[0]["text"]


def test_segment_without_heading_yields_single_section():
    """无 heading（md 降级单页）不崩，整篇作单章"""
    pages = [{"page_idx": 0, "items": [{"type": "text", "content": "全文"}]}]
    secs = segment_sections(pages)
    assert len(secs) == 1
    assert secs[0]["text"] == "全文\n"


def test_segment_empty_input():
    assert segment_sections([]) == []


# ---------------------------------------------------------------------------
# 3. T0-T3 分档
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("合并利润表", "T0"),
    ("資產負債表", "T0"),          # 繁体
    ("投資要點", "T1"),            # 繁体
    ("风险提示", "T1"),
    ("行业分析", "T2"),
    ("企業管治報告", "T2"),         # 繁体
    ("免责声明", "T3"),
    ("釋義", "T3"),               # 繁体
])
def test_classify_tier_keywords(title, expected):
    tier, reason = classify_tier(title)
    assert tier == expected
    assert reason, "tier_reason 必须非空（审计可回放）"


def test_classify_tier_table_fallback_is_t0():
    """无关键词但含表格 -> 保守 T0（结构化数据宁可不压）"""
    tier, reason = classify_tier("股权激励计划", has_table=True, table_bodies=["<table>2024年</table>"])
    assert tier == "T0"
    assert "表格" in reason


def test_classify_tier_prose_default_t2():
    tier, _ = classify_tier("随机章节名", has_table=False)
    assert tier == "T2"


# ---------------------------------------------------------------------------
# 4. 表格序列化（lxml，rowspan/colspan）
# ---------------------------------------------------------------------------

def test_parse_table_expands_rowspan_colspan():
    html = (
        "<table>"
        "<tr><th>项目</th><th colspan='2'>2024年</th></tr>"
        "<tr><td>营收</td><td>100</td><td>90</td></tr>"
        "<tr><td rowspan='2'>合计</td><td>10</td><td>9</td></tr>"
        "<tr><td>8</td><td>7</td></tr>"
        "</table>"
    )
    rows = parse_table_html(html)
    assert rows[0] == ["项目", "2024年", "2024年"]
    assert rows[1] == ["营收", "100", "90"]
    assert rows[2] == ["合计", "10", "9"]
    assert rows[3] == ["合计", "8", "7"], "rowspan 必须向下填充"


def test_parse_table_invalid_html_returns_empty():
    assert parse_table_html("") == []
    assert parse_table_html("not html at all") == []


def test_serialize_wide_table_repeats_header_per_chunk():
    """分块后每块都必须带表头（任意切分点列语义可辨）"""
    headers = ["项目", "2024H1", "2023H1"]
    rows = [[f"科目{i}", str(i), str(i + 1)] for i in range(60)]
    tb = build_table_record("t_001", 12, "<table></table>", [], [])
    tb.update({"headers": headers, "rows": rows})

    chunks = serialize_table(tb, rows_per_chunk=30)
    assert len(chunks) == 2, "60 行 / 30 每块 = 2 块"
    for chunk in chunks:
        assert "| 项目 | 2024H1 | 2023H1 |" in chunk, "每块必须重复表头"
    assert "（接上表，第 31-60 行）" in chunks[1]


def test_serialize_escapes_pipe_in_cells():
    tb = build_table_record("t_002", 1, "<table><tr><td>a|b</td><td>c</td></tr><tr><td>1|2</td><td>3</td></tr></table>", [], [])
    chunks = serialize_table(tb)
    assert "｜" in chunks[0], "单元格内竖线需转全角，防破坏 Markdown 列"


# ---------------------------------------------------------------------------
# 5. 事实抽取
# ---------------------------------------------------------------------------

def test_parse_number_variants():
    assert parse_number("204.42") == 204.42
    assert parse_number("1,234,567") == 1234567
    assert parse_number("12.3%") == 12.3
    assert parse_number("N/A") is None
    assert parse_number("-") is None
    assert parse_number("不适用") is None


def test_fact_from_table_has_full_binding():
    """Fact 必须绑定 company/metric/period + source(row/col) —— 治理旧代码的无主体绑定"""
    tb = build_table_record(
        "t_004", 12,
        "<table>"
        "<tr><td>项目</td><td>2024年</td><td>2023年</td></tr>"
        "<tr><td>营业收入</td><td>204.42</td><td>195.14</td></tr>"
        "</table>",
        ["合并利润表"], [],
    )
    facts = extract_facts([], [tb], companies=["复星医药"])
    rev = [f for f in facts if f["metric"] == "营业收入" and f["period"] == "FY2024"]
    assert len(rev) == 1
    f = rev[0]
    assert f["company"] == "复星医药"
    assert f["value"] == 204.42
    assert f["source"]["table_id"] == "t_004"
    assert f["source"]["row"] == 1
    assert f["confidence"] == 0.9


def test_fact_dedup_same_key_keeps_best_confidence():
    """
    同一 (company, metric, period) 只保留一条 —— 治理旧代码 dict.update 覆盖丢数据。

    注意: 去重键含 period。期间不同 = 不同事实（如 FY2024 vs FY2023），不去重；
          期间未知（文本正则层）也保留，不静默丢弃（宁可多一条低置信，不可无声丢数）。
    """
    tb1 = build_table_record(
        "t_005", 12,
        "<table><tr><td>项目</td><td>2024年</td></tr><tr><td>营业收入</td><td>204.42</td></tr></table>",
        [], [],
    )
    # 同一期间，另一张表重复出现同一科目 -> 必须只留一条
    tb2 = build_table_record(
        "t_006", 30,
        "<table><tr><td>项目</td><td>2024年</td></tr><tr><td>营业收入</td><td>204.42</td></tr></table>",
        [], [],
    )
    facts = extract_facts([], [tb1, tb2], companies=["复星医药"])
    hits = [f for f in facts if f["metric"] == "营业收入" and f["period"] == "FY2024"]
    assert len(hits) == 1, "同 dedup_key 必须去重为一条"
    assert hits[0]["value"] == 204.42


def test_fact_different_periods_are_not_deduped():
    """不同期间 = 不同事实，去重不得误杀（FY2024 与 FY2023 都要在）"""
    tb = build_table_record(
        "t_007", 12,
        "<table>"
        "<tr><td>项目</td><td>2024年</td><td>2023年</td></tr>"
        "<tr><td>营业收入</td><td>204.42</td><td>195.14</td></tr>"
        "</table>",
        [], [],
    )
    facts = extract_facts([], [tb], companies=["复星医药"])
    rev = [f for f in facts if f["metric"] == "营业收入"]
    assert len(rev) == 2
    assert {f["period"] for f in rev} == {"FY2024", "FY2023"}
    assert {f["value"] for f in rev} == {204.42, 195.14}


def test_fact_unknown_period_not_silently_dropped():
    """
    文本正则层（期间未知，confidence 0.6）不被表格层静默吞掉 —— 保留但标注低置信。
    宁可多一条低置信事实，不可无声丢数（这正是旧代码 dict 碰撞的教训）。
    """
    tb = build_table_record(
        "t_008", 12,
        "<table><tr><td>项目</td><td>2024年</td></tr><tr><td>营业收入</td><td>204.42</td></tr></table>",
        [], [],
    )
    sections = [{"section_id": "s_001", "page_range": [12, 12], "text": "营业收入：999.99"}]
    facts = extract_facts(sections, [tb], companies=["复星医药"])
    hits = [f for f in facts if f["metric"] == "营业收入"]
    assert len(hits) == 2, "期间不同的两条事实都必须保留，不得静默丢弃"
    table_fact = next(f for f in hits if f["period"] == "FY2024")
    text_fact = next(f for f in hits if f["period"] != "FY2024")
    assert table_fact["confidence"] == 0.9 and table_fact["value"] == 204.42
    assert text_fact["confidence"] == 0.6 and text_fact["value"] == 999.99


def test_fact_text_layer_extraction():
    sections = [{"section_id": "s_001", "page_range": [7, 7], "text": "毛利率：49.67%，同比提升。"}]
    facts = extract_facts(sections, [], companies=["复星医药"])
    gm = [f for f in facts if f["metric"] == "毛利率"]
    assert len(gm) == 1
    assert gm[0]["value"] == 49.67
    assert gm[0]["confidence"] == 0.6


# ---------------------------------------------------------------------------
# 6. L1 端到端（零 LLM）
# ---------------------------------------------------------------------------

def test_build_l1_end_to_end():
    pages = [
        {"page_idx": 0, "items": [
            {"type": "heading", "content": "合并利润表", "level": 2},
            {"type": "table", "content": (
                "<table><tr><td>项目</td><td>2024年</td><td>2023年</td></tr>"
                "<tr><td>营业收入</td><td>204.42</td><td>195.14</td></tr>"
                "<tr><td>归母净利润</td><td>17.21</td><td>15.87</td></tr></table>"
            ), "caption": ["利润表"], "footnote": []},
        ]},
        {"page_idx": 1, "items": [
            {"type": "heading", "content": "免责声明", "level": 2},
            {"type": "text", "content": "本报告不构成投资建议。"},
        ]},
    ]
    l1 = build_l1(pages, companies=["复星医药"])
    assert l1["stats"]["n_sections"] == 2
    assert l1["stats"]["n_tables"] == 1
    assert l1["stats"]["n_facts"] >= 4, "2 行 × 2 个期间列 = 4 条事实"
    tiers = {s["title"]: s["tier"] for s in l1["sections"]}
    assert tiers["合并利润表"] == "T0"
    assert tiers["免责声明"] == "T3"
