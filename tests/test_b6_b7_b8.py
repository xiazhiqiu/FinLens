"""
B6/B7/B8 单元测试（2026-09-03）

B6 科目别名规则表: 归一化 + metric_std 字段 + dedup_key 合并 + query_fact 查询侧
B7 勾稽校验: 通过打标 / 不符降置信 / 无匹配跳过 / 单位折算
B8 单位邻接解析: 邻接文本严格解析 / caption 优先 / 无标记不误命中
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ---------------------------------------------------------------------------
# B6: 科目别名归一
# ---------------------------------------------------------------------------

def test_b6_normalize_metric_exact_match():
    from extractors.fact_extractor import normalize_metric

    # 精确命中（繁简 + 口语）
    assert normalize_metric("總收益") == "营业收入"
    assert normalize_metric("營業總收入") == "营业收入"
    assert normalize_metric("營收") == "营业收入"
    assert normalize_metric("歸屬於母公司股東的淨利潤") == "归母净利润"
    assert normalize_metric("資產總額") == "资产总计"
    assert normalize_metric("權益總額") == "股东权益合计"
    # A股 CAS 标准表述
    assert normalize_metric("归属于上市公司股东的净利润") == "归母净利润"
    assert normalize_metric("归属于母公司所有者的净利润") == "归母净利润"
    # 未命中返回原值（子串不匹配防误伤）
    assert normalize_metric("非臨床研究服務") == "非臨床研究服務"
    assert normalize_metric("投资收益") == "投资收益"
    assert normalize_metric("其他收益") == "其他收益"


def test_b6_ashare_embedded_unit_stripped():
    """[furui 实测] A股「主要会计数据」表单位内嵌在科目名尾部"""
    from extractors.fact_extractor import _table_facts, split_metric_unit

    # 纯函数层
    assert split_metric_unit("营业收入（元）") == ("营业收入", "元")
    assert split_metric_unit("基本每股收益（元/股）") == ("基本每股收益", "元/股")
    assert split_metric_unit("营业收入(万元)") == ("营业收入", "万元")
    assert split_metric_unit("其他（注）") == ("其他（注）", ""), "非单位括号不剥离"
    assert split_metric_unit("研发人员数量（人）") == ("研发人员数量（人）", ""), "非货币单位不剥离"

    # 表格抽取层: 剥离后别名命中 + unit 落到 fact + 与裸名同键去重
    tables = [
        _make_table(["项目", "2024年"], [["营业收入（元）", "3,322,399,262.33"],
                                          ["归属于上市公司股东的净利润（元）", "219,215,799.89"]]),
    ]
    facts = _table_facts(tables, company="福然德")
    by_std = {f["metric_std"]: f for f in facts}
    assert "营业收入" in by_std and by_std["营业收入"]["unit"] == "元"
    assert "归母净利润" in by_std and by_std["归母净利润"]["value"] == 219215799.89
    assert by_std["归母净利润"]["metric"] == "归属于上市公司股东的净利润", "原始科目名保留溯源"
    # 行级内嵌单位优先于表级单位
    tables2 = [_make_table(["项目", "2024年"], [["营业收入（万元）", "1,234"]], unit="元")]
    f2 = _table_facts(tables2, company="X")[0]
    assert f2["unit"] == "万元"


def _make_table(headers, rows, unit="", page_idx=10, table_id="t_001"):
    return {"headers": headers, "rows": rows, "unit": unit,
            "page_idx": page_idx, "table_id": table_id}


def test_b6_facts_carry_metric_std_and_dedup_merges_variants():
    from extractors.fact_extractor import _table_facts

    # 两张表: 一张用繁体"營業收入"，一张用"營業總收入"（同期间同值）
    tables = [
        _make_table(["項目", "2024年12月31日止年度"], [["營業收入", "2,018,334"]]),
        _make_table(["項目", "2024年12月31日止年度"], [["營業總收入", "2,018,334"]]),
    ]
    facts = _table_facts(tables, company="昭衍新药")
    assert len(facts) == 2
    assert all(f["metric_std"] == "营业收入" for f in facts)
    assert all(f["metric"] != f["metric_std"] for f in facts), "原始科目名必须保留（溯源）"
    # dedup_key 用标准名 → 繁简变体同键
    assert len({f["dedup_key"] for f in facts}) == 1


def test_b6_query_fact_matches_via_alias():
    from agents.context_tools import build_context_tools

    l1 = {
        "sections": [],
        "tables": [],
        "facts": [{
            "company": "昭衍新药", "metric": "總收益", "metric_std": "营业收入",
            "period": "FY2024", "value": 2018334, "raw": "2,018,334",
            "unit": "人民币千元", "source": {"page_idx": 21, "table_id": "t_005"},
        }],
    }
    tools = {t.name: t for t in build_context_tools(l1)}
    out = tools["query_fact"].invoke({"company": "", "metric": "营业收入", "period": ""})
    assert "2,018,334" in out, "查询标准名必须命中繁体原始科目"


# ---------------------------------------------------------------------------
# B7: 勾稽校验
# ---------------------------------------------------------------------------

def _fin(metric_std, period, value, unit="人民币千元", confidence=0.9):
    return {"metric": metric_std, "metric_std": metric_std, "period": period,
            "value": value, "unit": unit, "confidence": confidence,
            "raw": str(value), "source": {}}


def test_b7_identity_pass_marks_facts():
    from extractors.identity_checker import run_identity_checks

    facts = [
        _fin("资产总计", "FY2024", 1000.0),
        _fin("负债合计", "FY2024", 600.0),
        _fin("股东权益合计", "FY2024", 400.0),
    ]
    checks = run_identity_checks(facts)
    assert len(checks) == 1 and checks[0]["ok"] is True
    assert all(f.get("identity_checked") for f in facts)
    assert all(f["confidence"] == 0.9 for f in facts), "通过不降置信"


def test_b7_identity_mismatch_lowers_confidence():
    from extractors.identity_checker import run_identity_checks

    facts = [
        _fin("资产总计", "FY2024", 1000.0),
        _fin("负债合计", "FY2024", 600.0),
        _fin("股东权益合计", "FY2024", 500.0),  # 600+500 != 1000
    ]
    checks = run_identity_checks(facts)
    assert checks[0]["ok"] is False
    assert all(f.get("identity_mismatch") for f in facts)
    assert all(abs(f["confidence"] - 0.54) < 1e-9 for f in facts), "降置信 ×0.6"


def test_b7_no_match_silent_skip():
    from extractors.identity_checker import run_identity_checks

    # 只有资产总计，无负债/权益 → 允许无匹配
    facts = [_fin("资产总计", "FY2024", 1000.0), _fin("营业收入", "FY2024", 200.0)]
    checks = run_identity_checks(facts)
    assert checks == []
    assert "identity_checked" not in facts[0]
    assert facts[0]["confidence"] == 0.9


def test_b7_unit_scaling_and_tolerance():
    from extractors.identity_checker import run_identity_checks

    # 单位不一致也能对上: 资产 1.0 亿 = 负债 60,000 千元 + 权益 40,000 千元
    facts = [
        _fin("资产总计", "FY2024", 1.0, unit="人民币亿元"),
        _fin("负债合计", "FY2024", 60000.0, unit="人民币千元"),
        _fin("股东权益合计", "FY2024", 40000.0, unit="人民币千元"),
    ]
    checks = run_identity_checks(facts)
    assert checks[0]["ok"] is True, "单位折算后应通过"

    # 容差: 差 0.05（< max(0.01%×1000, 0.005)... 注: 0.01%×1000=0.1 > 0.05）应通过
    facts2 = [
        _fin("资产总计", "FY2024", 1000.05, unit=""),
        _fin("负债合计", "FY2024", 600.0, unit=""),
        _fin("股东权益合计", "FY2024", 400.0, unit=""),
    ]
    checks2 = run_identity_checks(facts2)
    assert checks2[0]["ok"] is True

    # 单位口径不明（部分已知部分空）→ 跳过
    facts3 = [
        _fin("资产总计", "FY2024", 1000.0, unit=""),
        _fin("负债合计", "FY2024", 600.0, unit="人民币千元"),
        _fin("股东权益合计", "FY2024", 400.0, unit=""),
    ]
    checks3 = run_identity_checks(facts3)
    assert checks3[0]["ok"] is None and checks3[0]["reason"] == "unit_mixed"
    assert "identity_checked" not in facts3[0]


# ---------------------------------------------------------------------------
# B8: 单位邻接解析
# ---------------------------------------------------------------------------

def _pages_with_table(pre_text, page_idx=5):
    return [{
        "page_idx": page_idx,
        "items": [
            {"type": "text", "content": pre_text},
            {"type": "table", "content": "<table><tr><td>項目</td><td>2024年</td></tr>"
                                         "<tr><td>營業收入</td><td>2,018</td></tr></table>"},
        ],
    }]


def test_b8_adjacent_text_unit_parsed():
    from extractors.l1_builder import build_l1

    l1 = build_l1(_pages_with_table("綜合損益表（人民幣千元）摘要在下表列示"))
    tb = l1["tables"][0]
    assert tb["unit"] == "人民幣千元", "邻接文本带括号单位应被解析"

    l1b = build_l1(_pages_with_table("下表列示本集團財務資料。單位：百萬元"))
    assert l1b["tables"][0]["unit"] == "百萬元", "單位: 标记应被解析"


def test_b8_strict_mode_no_marker_no_hit():
    from extractors.l1_builder import build_l1

    # 邻接文本无明确单位标记 → 宁缺毋滥，不误命中（P1 教训：千元表误标百萬元）
    l1 = build_l1(_pages_with_table("本集團於年內實現穩健增長，收入規模持續擴大"))
    assert l1["tables"][0]["unit"] == "", "无标记不得猜测单位"


def test_b8_caption_unit_takes_priority():
    from extractors.table_serializer import build_table_record

    rec = build_table_record(
        table_id="t_001", page_idx=1,
        html="<table><tr><td>a</td></tr></table>",
        caption=["單位：万元"], footnote=[],
        adjacent_text="（人民幣千元）",
    )
    assert rec["unit"] == "万元", "caption 单位优先于邻接文本"


def test_b8_unit_flows_into_facts():
    from extractors.l1_builder import build_l1

    l1 = build_l1(_pages_with_table("綜合損益表（人民幣千元）"), companies=["昭衍新药"])
    assert l1["facts"], "表格事实应存在"
    assert all(f["unit"] == "人民幣千元" for f in l1["facts"])


def test_b8_parse_number_internal_spaces():
    """[furui 实测] MinerU 千分位数字内部空格（'2,383, 553, 485.39'）必须可解析"""
    from extractors.fact_extractor import parse_number

    assert parse_number("2,383, 553, 485.39") == 2383553485.39
    assert parse_number("188, 555, 158.43") == 188555158.43
    assert parse_number("(1, 234)") == -1234.0, "括号负数带内部空格"
    assert parse_number("12. 3%") == 12.3, "百分号前空格"
    # 文本单元格不受影响
    assert parse_number("营业 收入 稳健") is None
    assert parse_number("") is None
    assert parse_number("-") is None
