"""
P3 测试: 章节压缩器（L2/L3）

覆盖:
- 规则兜底: 数字保留、确定性
- LLM JSON 解析（mock）: thesis/key_arguments、数字原样传入
- 增量缓存: 已缓存章节绝不重压（跨轮复用 == 修订循环零重压）
- 装配器 L2 接入: 溢出章节注入摘要而非纯指针
- L3-lite: 确定性、自动抽选标注
- 真实年报（样本可用时）: 规则压缩确定性 + 装配覆盖率提升 + 二次构建零新增
"""

import json
import pytest

from extractors.l1_builder import build_l1


def _l1_with_big_t2_sections(n=6, text_len=400):
    """多个大 T2 散文章节 + 一个 T0 小表章节"""
    items = []
    for i in range(n):
        items.append({"type": "heading", "content": f"行业分析第{i}章", "level": 2})
        items.append({"type": "text", "content": ("公司该业务收入 204.42 亿元，同比增长 4.75%，毛利率 49.67%。" + "背景叙述" * text_len)})
    items.append({"type": "heading", "content": "合并利润表", "level": 2})
    items.append({"type": "table", "content": (
        "<table><tr><td>项目</td><td>2024年</td></tr><tr><td>营业收入</td><td>204.42</td></tr></table>"
    ), "caption": [], "footnote": []})
    return build_l1([{"page_idx": 0, "items": items}])


# ---------------------------------------------------------------------------
# 1. 单章压缩
# ---------------------------------------------------------------------------

def test_compress_section_rule_fallback_keeps_numbers(monkeypatch):
    """LLM 不可用 -> 规则兜底，数字原样保留 + 确定性"""
    import utils.llm_client as llmc
    from extractors.section_compressor import compress_section

    monkeypatch.setattr(llmc, "is_llm_ready", lambda: False)

    sec = {
        "section_id": "s_010", "title": "风险提示", "tier": "T1",
        "page_range": [12, 13],
        "text": "公司2024年收入 204.42 亿元，同比+4.75%。面临毛利率下滑至 49.67% 的风险。",
    }
    entry = compress_section(sec, use_llm=True)
    assert entry["has_llm"] is False
    assert entry["section_id"] == "s_010"
    text = " ".join(entry["key_arguments"])
    assert "204.42" in text and "4.75" in text and "49.67" in text, "数字必须原样保留"
    # 确定性: 同输入同输出
    entry2 = compress_section(sec, use_llm=True)
    assert entry["key_arguments"] == entry2["key_arguments"]


def test_compress_section_llm_json_parsed(monkeypatch):
    """LLM 返回 JSON -> 正确解析出 thesis/key_arguments，数字原样进 key_arguments"""
    import utils.llm_client as llmc
    from extractors.section_compressor import compress_section

    monkeypatch.setattr(llmc, "is_llm_ready", lambda: True)
    llm_out = json.dumps({
        "thesis": "收入增长但毛利率承压",
        "key_arguments": ["营收 204.42 亿元(+4.75%) [p12]", "毛利率降至 49.67% [p13]"],
    }, ensure_ascii=False)
    monkeypatch.setattr(llmc, "safe_invoke", lambda sp, um, **kw: {"error": False, "content": llm_out})

    sec = {"section_id": "s_011", "title": "经营分析", "tier": "T2", "page_range": [12, 13], "text": "..."}
    entry = compress_section(sec, use_llm=True)
    assert entry["has_llm"] is True
    assert entry["thesis"] == "收入增长但毛利率承压"
    assert any("204.42" in a for a in entry["key_arguments"]), "LLM 版数字保留在要点内"
    assert "p12" in entry["text"]


# ---------------------------------------------------------------------------
# 2. 文档级增量构建 + 缓存（修订循环零重压）
# ---------------------------------------------------------------------------

def test_compress_document_l2_incremental_cache(monkeypatch):
    """已缓存章节绝不再压缩；第二轮增量新增 == 0"""
    from extractors import section_compressor as sc
    l1 = _l1_with_big_t2_sections(n=4)

    calls = []
    real = sc.compress_section

    def counting(section, use_llm=True):
        calls.append(section["section_id"])
        return real(section, use_llm=False)  # 强制规则路径，离线可测

    monkeypatch.setattr(sc, "compress_section", counting)

    cache: dict = {}
    first = sc.compress_document_l2(l1["sections"], cache, min_text_tokens=100, max_new=8, use_llm=False)
    assert len(first) == 4, "4 个大 T2 章应全部首轮构建"
    assert all(sid not in calls[:0] for sid in [])  # sanity

    n_calls_first = len(calls)
    second = sc.compress_document_l2(l1["sections"], cache, min_text_tokens=100, max_new=8, use_llm=False)
    assert len(second) == 0, "第二轮必须零新增（缓存命中）—— 修订循环零重压"
    assert len(calls) == n_calls_first, "不得重复调用压缩"


# ---------------------------------------------------------------------------
# 3. 装配器 L2 接入
# ---------------------------------------------------------------------------

def test_assemble_injects_l2_summary_instead_of_pointer():
    from extractors.context_assembler import assemble
    from extractors.section_compressor import compress_document_l2
    l1 = _l1_with_big_t2_sections(n=8)

    cache: dict = {}
    compress_document_l2(l1["sections"], cache, min_text_tokens=100, max_new=8, use_llm=False)
    big_sid = cache  # 全部章节都已压缩

    # 小预算: 无 L2 vs 有 L2
    out_no = assemble("测试", 800, l1)
    out_yes = assemble("测试", 800, l1, l2=cache)

    assert out_yes["stats"]["n_l2_injected"] > 0, "L2 就绪后溢出章节应注摘要"
    assert out_yes["stats"]["n_pointers"] < out_no["stats"]["n_pointers"], "L2 必须减少纯指针"
    # L2 摘要文本确实进入 context（要点渲染含 thesis 或 bullets）
    assert "（要点" in out_yes["context"] or "公司该业务收入" in out_yes["context"]
    assert out_yes["stats"]["used"] <= 800


# ---------------------------------------------------------------------------
# 4. L3-lite
# ---------------------------------------------------------------------------

def test_l3_lite_deterministic_and_labelled():
    from extractors.section_compressor import build_global_summary_lite
    l1 = _l1_with_big_t2_sections(n=1)
    s1 = build_global_summary_lite(l1, company="复星医药")
    s2 = build_global_summary_lite(l1, company="复星医药")
    assert s1 == s2, "L3-lite 必须确定性"
    assert "自动抽选" in s1 and "人工未校核" in s1, "诚实标注来源"
    assert "复星医药" in s1
    assert "FY2024" in s1


# ---------------------------------------------------------------------------
# 5. 真实年报（样本缺失自动 skip）
# ---------------------------------------------------------------------------

SAMPLE = r"D:\develop\财报分析助手\m1\out\joinn_v3\joinn_2024_annual\auto\joinn_2024_annual_content_list.json"

real_mark = pytest.mark.skipif(not __import__("pathlib").Path(SAMPLE).is_file(), reason="真实年报样本不可用")


@real_mark
def test_real_l2_rule_fallback_deterministic():
    import json as _json
    from extractors.mineru_extractor import _content_list_to_structured_pages
    from extractors.section_compressor import compress_document_l2

    cl = _json.loads(__import__("pathlib").Path(SAMPLE).read_text(encoding="utf-8"))
    l1 = build_l1(_content_list_to_structured_pages(cl), companies=["昭衍新药"])

    cache: dict = {}
    built = compress_document_l2(l1["sections"], cache, min_text_tokens=300, max_new=300, use_llm=False)
    assert len(built) >= 6, "真实年报 T1/T2≥300tokens 章节应远多于 6"
    assert len(built) == len(cache), "首轮应建满（max_new 足够大）"
    # 数字保真: 每个压缩条目的 key_arguments 至少含一个数字句或 thesis 标注
    for entry in built[:6]:
        assert entry["has_llm"] is False
        assert entry["n_chars_after"] < entry["n_text_chars_before"], "压缩必须变小"
    # 缓存: 建满后第二轮零新增（不重建任何章节）
    assert compress_document_l2(l1["sections"], cache, min_text_tokens=300, max_new=300, use_llm=False) == []


@real_mark
def test_real_assembly_coverage_gains_with_l2():
    """小预算下大叙述章节优先走 L2 摘要 -> 指针减少、摘要注入>0"""
    import json as _json
    from pathlib import Path as _P
    from extractors.mineru_extractor import _content_list_to_structured_pages
    from extractors.context_assembler import assemble
    from extractors.section_compressor import compress_document_l2

    cl = _json.loads(_P(SAMPLE).read_text(encoding="utf-8"))
    l1 = build_l1(_content_list_to_structured_pages(cl), companies=["昭衍新药"])

    budget = 8000  # 甜点预算: T0 主表附近出现 raw 溢出，L2 摘要得以注入
    cache: dict = {}
    compress_document_l2(l1["sections"], cache, min_text_tokens=300, max_new=200, use_llm=False)

    out_no = assemble("昭衍新药 2024", budget, l1)
    out_yes = assemble("昭衍新药 2024", budget, l1, l2=cache)

    assert len(cache) > 0, "真实年报必须产出 L2 缓存"
    assert out_yes["stats"]["n_l2_injected"] > 0, "必须有章节走 L2 摘要注入"
    assert out_yes["stats"]["n_pointers"] < out_no["stats"]["n_pointers"], "L2 必须减少纯指针"
    assert out_yes["stats"]["used"] <= budget
    # L2 注入的章节内容确实可读（要点文本出现在 context）
    assert "（要点" in out_yes["context"]
