"""
FinScope 事实抽取器（P1 地基）

职责: 表格 + 章节文本 -> Fact 记录（治理「数字无主体绑定」与「dict 碰撞丢数据」）

三层抽取，规则优先（银行合规要求判定可审计）:
1. 表格定位（confidence 0.9）: metric=行首列, period=列表头列 —— 最可靠
2. 文本正则（confidence 0.6）: 7 类指标正则（本模块为全项目唯一副本）
3. LLM 别名归一（confidence 0.5，受限叶子，可关）: 仅归一科目别名，不参与核心判定（P3 接入）

去重: dedup_key = company|metric|period，同键保留 confidence 最高者。
"""

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 7 类指标正则（全项目唯一副本）
# ---------------------------------------------------------------------------

METRIC_PATTERNS: Dict[str, str] = {
    "营业收入": r"营业(?:总)?收入[：:：为\s]*([\d,.]+[亿万萬億]?)",
    "归母净利润": r"归(?:属于)?母(?:公司)?(?:股东)?(?:的)?净利润[：:：为\s]*([\d,.]+[亿万萬億]?)",
    "毛利率": r"毛利[率][：:：为\s]*([\d.]+%)",
    "净利率": r"净利[率][：:：为\s]*([\d.]+%)",
    "ROE": r"ROE[：:：为\s]*([\d.]+%)",
    "EPS": r"(?:基本)?每股收益[：:：为\s]*([\d.]+)元?",
    "PE": r"(?:市盈率|PE)[：:：为\s]*([\d.]+)倍?",
}

# 列表头识别为「期间列」的模式
_PERIOD_HEADER_RE = re.compile(r"(20\d{2})|((本|上)(期|年度|半年|月))|(期初|期末)|(Mid|Full)")
_YEAR_RE = re.compile(r"20\d{2}")
# 明确日期: 2024年12月31日 / 2024年 6月30日（MinerU 常带空格）
_DATE_RE = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
# 半年度措辞: 止六個月 / 止六个月 / 上半年
_HALF_YEAR_RE = re.compile(r"止[六6][個个]月|上半年")

# B1: 编号行标签 —— 行首列是序号而非科目: '(A)' / '0' / '(ii)' / '1.' 等
_ROW_NUM_RE = re.compile(
    r"^(?:\(?[0-9]+(?:\.[0-9]+)?\)?|\(?[ivxIVX]{1,5}\)?|\(?[A-Za-z]\)?|[0-9]+[.、])$"
)

# B4: 合计/小计行标记（简繁双套）
_SUBTOTAL_RE = re.compile(r"合計|合计|小計|小计|總計|总计")

# B2: 数值解析（成对括号才视为会计负数，残缺括号按非数值丢弃）
_PLAIN_NUMBER_RE = re.compile(r"^([+-]?[\d,]+(?:\.\d+)?)%?$")
_PAREN_NUMBER_RE = re.compile(r"^\(([\d,]+(?:\.\d+)?)\)%?$")


# B6: 科目别名规则表（精确整串匹配，可审计；子串不匹配防误伤）
# 繁体为主（MinerU 对港股/繁体年报保真），简体兜底
_METRIC_ALIAS = {
    # 营业收入族
    "營業收入": "营业收入", "营业收入": "营业收入",
    "營業總收入": "营业收入", "营业总收入": "营业收入",
    "總收益": "营业收入", "总收益": "营业收入",
    "營收": "营业收入", "营收": "营业收入",
    # 净利润族
    "歸屬於母公司股東的淨利潤": "归母净利润", "归属于母公司股东的净利润": "归母净利润",
    "歸母淨利潤": "归母净利润", "归母净利润": "归母净利润",
    "淨利潤": "净利润", "净利润": "净利润",
    # 资产负债表三大件
    "資產總額": "资产总计", "资产总额": "资产总计", "總資產": "资产总计", "总资产": "资产总计",
    "負債總額": "负债合计", "负债总额": "负债合计", "總負債": "负债合计", "总负债": "负债合计",
    "股東權益總額": "股东权益合计", "股东权益总额": "股东权益合计",
    "權益總額": "股东权益合计", "权益总额": "股东权益合计",
    "所有者權益合計": "股东权益合计", "所有者权益合计": "股东权益合计",
    # 港股 IFRS: 淨資產 = 權益總額（同一科目两种表述；精确匹配不误伤"歸屬於...淨資產"）
    "淨資產": "股东权益合计", "净资产": "股东权益合计",
    # A股 CAS 标准表述
    "归属于上市公司股东的净利润": "归母净利润",
    "归属于母公司所有者的净利润": "归母净利润",
    "归属于上市公司股东的净资产": "归母净资产",
}

# A股适配: 行标签尾部内嵌单位 —— A股「主要会计数据」表标准格式
# '营业收入（元）' / '基本每股收益（元/股）' / '营业收入(万元)'；非单位括号（'其他（注）'）不匹配
_EMBEDDED_UNIT_RE = re.compile(
    r"^(?P<metric>.+?)[（(](?P<unit>(?:人民币)?(?:千元|万元|百万元|亿元|元)(?:/[万亿]?股)?)[）)]$"
)


def split_metric_unit(metric: str) -> tuple:
    """A股适配: 剥离行标签尾部内嵌单位。返回 (剥后科目名, 单位)；无匹配返回 (原值, '')。"""
    m = _EMBEDDED_UNIT_RE.match(metric.strip())
    if m:
        return m.group("metric").strip(), m.group("unit")
    return metric.strip(), ""


def normalize_metric(metric: str) -> str:
    """B6: 科目名归一化（精确匹配别名表；未命中返回原值）"""
    return _METRIC_ALIAS.get(metric.strip(), metric.strip())


def parse_number(raw: str) -> Optional[float]:
    """
    解析表格单元格数值。

    支持: '204.42' / '1,234,567' / '12.3%' / '(123)'（会计负数 → -123）/
    '2,383, 553, 485.39'（OCR 数字内部空格，去除无损）
    返回 None 表示非数值（文字单元格），不产出 Fact。
    """
    if raw is None:
        return None
    # B8 顺带修复: MinerU 常把千分位数字切出内部空格（'2,383, 553'）——
    # 数字内空白是纯噪声，去除无损（含空格的文本本就不匹配数值正则）
    s = re.sub(r"\s+", "", str(raw))
    if not s or s in {"-", "—", "–", "N/A", "不適用", "不适用"}:
        return None
    try:
        m = _PAREN_NUMBER_RE.match(s)
        if m:
            return -float(m.group(1).replace(",", ""))
        m = _PLAIN_NUMBER_RE.match(s)
        if m:
            return float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return None


def normalize_period(header: str) -> str:
    """
    期间列表头归一（B5: 期间精度到半年/季度，避免半年报列与年报列 dedup 错并）:
    - '2024年12月31日' / '截至2024年12月31日止年度' → FY2024
    - '2024年6月30日' / '截至2024年6月30日止六個月' → 2024H1
    - '2024年3月31日' → 2024Q1；'2024年9月30日' → 2024Q3
    - 其他明确日期 → 'YYYY-MM-DD'（精确标签，绝不与期末列合并）
    - 仅年份 → FYyyyy；本期/上期等无年份保留原文
    """
    m = _DATE_RE.search(header)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if (mo, d) == (12, 31):
            return f"FY{y}"
        if (mo, d) == (6, 30):
            return f"{y}H1"
        if (mo, d) == (3, 31):
            return f"{y}Q1"
        if (mo, d) == (9, 30):
            return f"{y}Q3"
        return f"{y}-{mo:02d}-{d:02d}"
    my = _YEAR_RE.search(header)
    if my:
        if _HALF_YEAR_RE.search(header):
            return f"{my.group(0)}H1"
        return f"FY{my.group(0)}"
    s = header.strip()
    return s[:24] if s else "未知期间"


def _table_facts(
    tables: List[Dict[str, Any]],
    company: str,
    per_table_cap: int = 120,
) -> List[Dict[str, Any]]:
    """
    表格定位抽取（confidence 0.9）

    metric = 行首列（row label）; period = 期间列表头; value = 行列交叉点。
    无期间列头的表（如释义表）不产出 Fact —— 行列语义不明的数字没有事实价值。
    """
    facts: List[Dict[str, Any]] = []
    for tb in tables:
        headers = tb.get("headers") or []
        rows = tb.get("rows") or []
        if not headers or not rows:
            continue

        # 识别期间列（排除第 0 列行标签列）
        period_cols = [
            (i, normalize_period(h)) for i, h in enumerate(headers)
            if i > 0 and _PERIOD_HEADER_RE.search(h or "")
        ]
        if not period_cols:
            continue

        emitted = 0
        for r_idx, row in enumerate(rows):
            if not row:
                continue
            metric = (row[0] or "").strip()
            if not metric:
                continue
            # A股适配: 剥离行标签内嵌单位（'营业收入（元）' → metric=营业收入, unit=元）
            metric, embedded_unit = split_metric_unit(metric)
            if not metric or len(metric) > 40:
                continue  # 行标签过长多为说明文字，非科目
            if _ROW_NUM_RE.match(metric):
                continue  # B1: 编号行标签是序号而非科目，跳过
            is_subtotal = bool(_SUBTOTAL_RE.search(metric))  # B4: 合计/小计行
            metric_std = normalize_metric(metric)  # B6: 别名归一
            for c_idx, period in period_cols:
                if c_idx >= len(row):
                    continue
                raw = (row[c_idx] or "").strip()
                val = parse_number(raw)
                if val is None:
                    continue
                facts.append({
                    "company": company,
                    "metric": metric,
                    "metric_std": metric_std,  # B6: 标准科目名（查询与去重口径）
                    "period": period,
                    "value": val,
                    "raw": raw,
                    "is_pct": raw.endswith("%"),  # B3: 比率与绝对值分离
                    "is_subtotal": is_subtotal,
                    "unit": embedded_unit or tb.get("unit", ""),  # 行级内嵌单位优先于表级
                    "source": {
                        "page_idx": tb.get("page_idx"),
                        "table_id": tb.get("table_id"),
                        "row": r_idx + 1,
                        "col": headers[c_idx],
                    },
                    "confidence": 0.9,
                    "dedup_key": f"{company}|{metric_std}|{period}",  # B6: 繁简变体正确合并
                })
                emitted += 1
                if emitted >= per_table_cap:
                    break
            if emitted >= per_table_cap:
                break

    return facts


def _text_facts(sections: List[Dict[str, Any]], company: str) -> List[Dict[str, Any]]:
    """文本正则抽取（confidence 0.6）—— 复用 7 类指标正则"""
    facts: List[Dict[str, Any]] = []
    for sec in sections:
        text = sec.get("text") or ""
        if not text:
            continue
        page_hint = (sec.get("page_range") or [None])[0]
        for metric_name, pattern in METRIC_PATTERNS.items():
            for m in re.findall(pattern, text)[:3]:  # 每指标每章最多 3 条
                raw = str(m).strip()
                metric_std = normalize_metric(metric_name)  # B6
                facts.append({
                    "company": company,
                    "metric": metric_name,
                    "metric_std": metric_std,  # B6
                    "period": "报告期未标注",
                    "value": parse_number(raw),
                    "raw": raw,
                    "is_pct": raw.endswith("%"),  # B3: 比率与绝对值分离
                    "is_subtotal": False,
                    "unit": "",
                    "source": {
                        "page_idx": page_hint,
                        "table_id": None,
                        "section_id": sec.get("section_id"),
                    },
                    "confidence": 0.6,
                    "dedup_key": f"{company}|{metric_std}|报告期未标注",  # B6
                })
    return [f for f in facts if f["value"] is not None]


def extract_facts(
    sections: List[Dict[str, Any]],
    tables: List[Dict[str, Any]],
    companies: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    三层抽取主入口（P1 只启用规则层；LLM 别名归一 P3 接入）

    Args:
        sections: segment_sections 产物
        tables:   l1_builder 组装的 Table 记录
        companies: 报告主体公司名（来自实体抽取），空则 company 留空（下游标注低可信）

    Returns:
        List[Fact]，已按 dedup_key 去重（同键保留 confidence 最高者，先表格后文本）
    """
    company = ""
    if companies:
        for c in companies:
            if c and isinstance(c, str):
                company = c
                break

    facts = _table_facts(tables, company)
    facts.extend(_text_facts(sections, company))

    # 去重: 同 dedup_key 保留 confidence 最高者（同置信先到先得）
    best: Dict[str, Dict[str, Any]] = {}
    for f in facts:
        key = f["dedup_key"]
        prev = best.get(key)
        if prev is None or f["confidence"] > prev["confidence"]:
            best[key] = f

    # 编号
    result = sorted(best.values(), key=lambda f: (f["source"].get("page_idx") or 0))
    for i, f in enumerate(result, 1):
        f["fact_id"] = f"f_{i:04d}"

    table_n = sum(1 for f in result if f["confidence"] >= 0.9)
    logger.info("[FactExtractor] %d 条事实 (表格定位 %d / 文本正则 %d)", len(result), table_n, len(result) - table_n)
    return result
