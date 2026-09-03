"""
B 组事实质量修复测试（B1–B5，2026-09-03）

实证来源: joinn 2024 年报（H 股繁体）
- B1: p45/p46 募集资金用途表 8 条编号行垃圾事实（'(A)'/'0'/'(ii)'...）
- B2: 79 条括号负数符号反了，分布 33 页（原正则 group 索引错，负号从未生效）
- B3: 10 条含 % raw（'16.94%' → value=16.94 无量纲标记）
- B4: '小計' 行未标记（真实样本 6 个合计/小计行）
- B5: 期间只到年份（'於2024年 12月31日的' 等 6 种日期表头全归 FY2024）
"""

from extractors.fact_extractor import extract_facts, normalize_period, parse_number


# ---------------------------------------------------------------------------
# B1: 编号行标签过滤
# ---------------------------------------------------------------------------

def test_b1_paren_number_re():
    assert parse_number("(1,234)") == -1234
    assert parse_number("(1,512,794)") == -1512794.0
    assert parse_number("(123.45)") == -123.45
    assert parse_number("(123") is None, "残缺括号应按非数值丢弃"
    assert parse_number("123)") is None
    assert parse_number("(abc)") is None


def test_b1_numbering_rows_skipped():
    """行首列是序号（编号行）而非科目 → 不产出 Fact"""
    tables = [{
        "table_id": "t_001",
        "page_idx": 45,
        "unit": "",
        "headers": ["", "於2024年 12月31日的"],
        "rows": [
            ["(A)", "57.7"],
            ["0", "16.0"],
            ["(ii)", "36.7"],
            ["(B)", "294.9"],
            ["1.", "8.4"],
            ["所得款項用途", "1,917,487"],  # 正常科目行必须保留
        ],
    }]
    facts = extract_facts([], tables, companies=["昭衍新药"])
    metrics = [f["metric"] for f in facts]
    assert metrics == ["所得款項用途"], f"编号行全部过滤、正常科目保留，实际: {metrics}"


def test_b1_short_legit_labels_not_filtered():
    """短而合法的科目（收益/毛利/存貨/股本）不得误伤"""
    tables = [{
        "table_id": "t_002",
        "page_idx": 21,
        "unit": "",
        "headers": ["項目", "2024年", "2023年"],
        "rows": [
            ["收益", "1,917,487", "1,649,233"],
            ["毛利", "635,101", "541,235"],
            ["存貨", "88,406", "61,927"],
        ],
    }]
    facts = extract_facts([], tables, companies=["昭衍新药"])
    assert {f["metric"] for f in facts} == {"收益", "毛利", "存貨"}


# ---------------------------------------------------------------------------
# B2: 括号负数（会计惯例）
# ---------------------------------------------------------------------------

def test_b2_negative_in_facts():
    tables = [{
        "table_id": "t_003",
        "page_idx": 26,
        "unit": "人民幣千元",
        "headers": ["項目", "2024年12月31日"],
        "rows": [
            ["融資成本", "(1,512,794)"],
            ["收益", "1,917,487"],
        ],
    }]
    facts = extract_facts([], tables, companies=["昭衍新药"])
    by_metric = {f["metric"]: f for f in facts}
    assert by_metric["融資成本"]["value"] == -1512794.0, "括号负数必须为负"
    assert by_metric["融資成本"]["raw"] == "(1,512,794)", "raw 保留原文"
    assert by_metric["收益"]["value"] == 1917487.0


# ---------------------------------------------------------------------------
# B3: % 量纲标记
# ---------------------------------------------------------------------------

def test_b3_is_pct_flag():
    tables = [{
        "table_id": "t_004",
        "page_idx": 102,
        "unit": "",
        "headers": ["參數", "2024年12月31日"],
        "rows": [
            ["税前折現率", "16.94%"],
            ["收益", "1,917,487"],
        ],
    }]
    facts = extract_facts([], tables, companies=["昭衍新药"])
    by_metric = {f["metric"]: f for f in facts}
    assert by_metric["税前折現率"]["is_pct"] is True
    assert by_metric["税前折現率"]["value"] == 16.94
    assert by_metric["税前折現率"]["raw"] == "16.94%"
    assert by_metric["收益"]["is_pct"] is False


# ---------------------------------------------------------------------------
# B4: 合计/小计行标记
# ---------------------------------------------------------------------------

def test_b4_is_subtotal_flag():
    tables = [{
        "table_id": "t_005",
        "page_idx": 21,
        "unit": "人民幣千元",
        "headers": ["項目", "2024年12月31日"],
        "rows": [
            ["非臨床研究服務", "1,917,487"],
            ["小計", "1,960,233"],
            ["總計", "2,050,981"],
            ["其他收入", "90,716"],
        ],
    }]
    facts = extract_facts([], tables, companies=["昭衍新药"])
    by_metric = {f["metric"]: f for f in facts}
    assert by_metric["小計"]["is_subtotal"] is True
    assert by_metric["總計"]["is_subtotal"] is True
    assert by_metric["非臨床研究服務"]["is_subtotal"] is False
    assert by_metric["其他收入"]["is_subtotal"] is False
    # 标记不改变抽取本身 —— 由下游（分析/勾稽）决定是否剔除重复计量


def test_b4_subtotal_simplified_variants():
    """简体『合计/小计/总计』同样命中（规则简繁双套）"""
    tables = [{
        "table_id": "t_006",
        "page_idx": 1,
        "unit": "",
        "headers": ["项目", "2024年"],
        "rows": [["合计", "100"], ["流动资产合计", "80"]],
    }]
    facts = extract_facts([], tables, companies=["某公司"])
    by_metric = {f["metric"]: f for f in facts}
    assert by_metric["合计"]["is_subtotal"] is True
    assert by_metric["流动资产合计"]["is_subtotal"] is True, "『流动资产合计』含『合计』也应标记"


# ---------------------------------------------------------------------------
# B5: 期间精度（半年/季度归一，杜绝 dedup 错并）
# ---------------------------------------------------------------------------

def test_b5_normalize_period_variants():
    # 年末（12-31）→ FY（与既有约定兼容）
    assert normalize_period("於2024年 12月31日的") == "FY2024"
    assert normalize_period("截至2024年12月31日止年度") == "FY2024"
    # 半年末（6-30）→ H1
    assert normalize_period("2024年6月30日") == "2024H1"
    assert normalize_period("截至2024年6月30日止六個月") == "2024H1"
    assert normalize_period("截至2024年6月30日止六个月") == "2024H1"
    assert normalize_period("2024年上半年") == "2024H1"
    # 季度末 → Q
    assert normalize_period("2024年3月31日") == "2024Q1"
    assert normalize_period("2024年9月30日") == "2024Q3"
    # 其他明确日期 → 精确标签（绝不与期末列合并）
    assert normalize_period("於2023年1月1日") == "2023-01-01"
    # 仅年份 → FY；本期/上期保留原文
    assert normalize_period("2024年") == "FY2024"
    assert normalize_period("本期") == "本期"


def test_b5_no_dedup_collision_between_half_and_full_year():
    """半年报列与年报列同表共存时，必须产出两条独立事实而非 dedup 顶掉"""
    tables = [{
        "table_id": "t_007",
        "page_idx": 5,
        "unit": "人民幣千元",
        "headers": ["項目", "2024年6月30日", "2024年12月31日"],
        "rows": [["收益", "900,000", "1,917,487"]],
    }]
    facts = extract_facts([], tables, companies=["昭衍新药"])
    assert len(facts) == 2, "两列期间必须各自成条"
    by_period = {f["period"]: f for f in facts}
    assert by_period["2024H1"]["value"] == 900000.0
    assert by_period["FY2024"]["value"] == 1917487.0
