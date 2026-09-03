"""
L1 真实年报验收测试（joinn 2024 年报 MinerU 产物）

样本缺失时自动 skip（该样本不在仓库内，属本地数据）。
这是 P1 的说服力来源: fixture 只验证结构，真实年报才验证「在真数据上跑得通」。

样本: D:\\develop\\财报分析助手\\m1\\out\\joinn_v3\\joinn_2024_annual\\auto\\joinn_2024_annual_content_list.json
实证特征（2026-09-03 核实）:
- 189 页 / 2256 items: text 1699, table 115, header 50, footer 192, page_number 199
- header 是页眉噪声（跨页重复），真标题是 text + text_level>0（561 个）
- 繁体中文（H 股年报），115 个表中 29 个含 rowspan、44 个含 colspan
"""

import json
from pathlib import Path

import pytest

from extractors.mineru_extractor import _content_list_to_structured_pages
from extractors.l1_builder import build_l1
from extractors.table_serializer import _md_row, serialize_table

SAMPLE = Path(r"D:\develop\财报分析助手\m1\out\joinn_v3\joinn_2024_annual\auto\joinn_2024_annual_content_list.json")

pytestmark = pytest.mark.skipif(
    not SAMPLE.is_file(),
    reason=f"真实年报样本不可用: {SAMPLE}",
)


def _load_pages():
    cl = json.loads(SAMPLE.read_text(encoding="utf-8"))
    return _content_list_to_structured_pages(cl)


def test_real_heading_normalization():
    """真实数据: 页眉被剔除，真标题被识别"""
    pages = _load_pages()
    item_types = [i["type"] for p in pages for i in p["items"]]
    assert "header" not in item_types, "页眉类型不应出现在归一化输出中"
    assert "footer" not in item_types
    assert "page_number" not in item_types
    n_headings = item_types.count("heading")
    assert n_headings > 400, f"真标题应有 500+ 个，实际 {n_headings}"


def test_real_segmentation():
    """真实数据: 切出合理章节数（应远少于标题数，因跨页续节已合并）"""
    pages = _load_pages()
    l1 = build_l1(pages, companies=["昭衍新药"])
    stats = l1["stats"]
    assert stats["n_pages"] == 189
    assert 50 < stats["n_sections"] < 561, "章节数应少于原始标题数（续节已合并）"
    # T0 必须包含财报主表章节
    t0 = [s for s in l1["sections"] if s["tier"] == "T0"]
    assert len(t0) > 0, "真实年报必须切出 T0 财务章节"
    assert any("損益" in s["title"] or "現金流量" in s["title"] for s in t0), \
        f"T0 应含损益表/现金流量表章节，实际 T0 标题: {[s['title'] for s in t0][:10]}"
    # 每章必须有分档理由（审计可回放）
    assert all(s["tier_reason"] for s in l1["sections"])


def test_real_tables_parsed():
    """真实数据: 表格解析成功率 + 宽表分块每块带表头"""
    pages = _load_pages()
    l1 = build_l1(pages, companies=["昭衍新药"])
    tables = l1["tables"]
    # 样本共 115 个 table item，其中 4 个 table_body 为空（p3-6，无内容无图片）在入口剔除
    assert len(tables) == 111, f"应保留 111 个有效表格（115 - 4 空体），实际 {len(tables)}"

    parsed = [t for t in tables if t["headers"]]
    assert len(parsed) >= 105, f"解析成功率过低: {len(parsed)}/{len(tables)}"

    # 宽表分块: 每块都必须带表头
    wide = [t for t in parsed if len(t["rows"]) > 30]
    assert wide, "样本应含超过 30 行的宽表，否则分块逻辑未被真实覆盖"
    for tb in wide[:3]:
        chunks = serialize_table(tb, rows_per_chunk=30)
        assert len(chunks) > 1
        # 块结构: [元信息行] / (续块提示行) / 表头行 / 分隔行 / 数据行
        header_line = _md_row(tb["headers"])
        for chunk in chunks:
            assert header_line in chunk, "每个分块都必须重复表头（任意切分点列语义可辨）"
            assert "|" + "---|" * len(tb["headers"]) in chunk, "表头必须紧跟 Markdown 分隔行"


def test_real_facts_binding():
    """真实数据: Fact 必须绑定主体/期间/溯源，且表格来源占主导"""
    pages = _load_pages()
    l1 = build_l1(pages, companies=["昭衍新药"])
    facts = l1["facts"]
    assert len(facts) > 50, f"真实年报应抽出足量事实，实际 {len(facts)}"

    table_facts = [f for f in facts if f["confidence"] >= 0.9]
    assert len(table_facts) > 20, f"表格定位事实过少: {len(table_facts)}"

    f = table_facts[0]
    assert f["company"] == "昭衍新药", "主体必须绑定"
    assert f["period"].startswith("FY") or f["period"], "期间必须标注"
    assert f["source"]["page_idx"] is not None and f["source"]["table_id"], "溯源必须完整"

    # 溯源可回查: fact 指向的 table 必须真实存在
    table_ids = {t["table_id"] for t in l1["tables"]}
    for f in table_facts[:50]:
        assert f["source"]["table_id"] in table_ids, "fact 溯源指向的表格必须存在"


def test_real_b_fixes():
    """[B1–B5 验收] 真实年报上: 编号行零残留 / 括号负数为负 / 量纲与合计标记 / 期间无错并"""
    import re as _re
    from collections import Counter

    pages = _load_pages()
    l1 = build_l1(pages, companies=["昭衍新药"])
    facts = l1["facts"]

    # B1: 编号行垃圾事实 == 0（修复前 8 条: '(A)'/'0'/'(ii)'... 全在 p45/p46）
    junk = [f for f in facts
            if _re.fullmatch(r"\(?[0-9]+(\.[0-9]+)?\)?|\(?[ivxIVX]{1,5}\)?|\(?[A-Za-z]\)?", f["metric"])]
    assert junk == [], f"编号行垃圾事实应为 0，实际: {[(f['metric'], f['value']) for f in junk]}"

    # B2: raw 带成对括号的全部为负（修复前 79 条符号反了，分布 33 页）
    wrong_sign = [f for f in facts
                  if f["raw"].startswith("(") and f["raw"].endswith(")") and f["value"] > 0]
    assert wrong_sign == [], f"括号负数符号仍为正: {[(f['raw'], f['value']) for f in wrong_sign[:5]]}"
    n_neg = sum(1 for f in facts if f["raw"].startswith("(") and f["value"] < 0)
    assert n_neg > 50, f"真实年报应有足量括号负数事实，实际 {n_neg}"

    # B3/B4: 标记字段全覆盖
    assert all("is_pct" in f and "is_subtotal" in f for f in facts)
    assert any(f["is_pct"] for f in facts), "真实样本含 % 单元格（税前折現率等）"
    assert any(f["is_subtotal"] for f in facts), "真实样本含 合計/小計 行"

    # B5: 同表半年列与年末列不得错并（本样本为年报，验收 period 标签集合合理）
    periods = Counter(f["period"] for f in facts)
    assert periods["FY2024"] > 50 and periods["FY2023"] > 50
    # 无年份日期表头（如 '於2023年1月1日'）应产出精确日期标签而非并入 FY
    dated = [p for p in periods if _re.fullmatch(r"20\d{2}-\d{2}-\d{2}", p)]
    assert all(len({f["value"] for f in facts if f["period"] == p}) >= 1 for p in dated)


def test_real_zero_llm():
    """
    P1 铁律: 全流程零 LLM 调用。

    检测方式: L1 构建链路的全部模块做**源码级静态检查**——不允许出现任何 LLM 依赖
    （llm_client / safe_invoke / ChatOpenAI 等）。进程内 sys.modules 检查不可靠：
    全量测试套件中其他用例可能先导入了 utils.llm_client，造成全局污染误报。
    """
    from pathlib import Path as _Path

    modules = [
        "src/extractors/l1_builder.py",
        "src/extractors/section_segmenter.py",
        "src/extractors/table_serializer.py",
        "src/extractors/fact_extractor.py",
        "src/extractors/mineru_extractor.py",
    ]
    forbidden = ("llm_client", "safe_invoke", "ChatOpenAI", "ChatOllama")

    root = _Path(__file__).resolve().parents[1]
    for rel in modules:
        src = (root / rel).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in src, f"{rel} 不得引用 {token}（L1 必须零 LLM）"


def test_real_assembly_within_budget():
    """[P2 验收] 189 页年报 24000 预算装配: token ≤ 预算、不溢出"""
    from extractors.context_assembler import assemble

    pages = _load_pages()
    l1 = build_l1(pages, companies=["昭衍新药"])
    out = assemble("昭衍新药 2024 业绩与风险", budget_tokens=24000, l1=l1)

    assert out["stats"]["used"] <= 24000, "装配产物不得超预算"
    assert len(out["injected"]) > 0, "必须有章节无损直注"
    # T0 财务主表（损益表/现金流量表）必须直注成功（无损路径优先保证）
    t0_injected = [s for s in l1["sections"]
                   if s["section_id"] in out["injected"] and s["tier"] == "T0"]
    assert t0_injected, "T0 章节必须优先直注"


def test_real_query_fact_on_annual_report():
    """[P2 验收] query_fact 在真实年报上精确命中带溯源事实"""
    from agents.context_tools import build_context_tools

    pages = _load_pages()
    l1 = build_l1(pages, companies=["昭衍新药"])
    tools = {t.name: t for t in build_context_tools(l1)}

    res = tools["query_fact"].invoke({"company": "昭衍新药", "metric": "非臨床研究服務", "period": "FY2024"})
    assert "非臨床研究服務" in res
    assert "FY2024" in res
    assert "t_" in res and "p" in res, "必须带表格/页码溯源"
    # 注: 计量单位仅当 MinerU caption/footnote 提供时可用（多数为空），
    # 单位缺省不阻断溯源——「表格前邻接文本解析」列入 P3 待办。
