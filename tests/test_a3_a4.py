"""
A3+A4 验收测试（2026-09-03）

A3 多 agent 共用 L2 缓存:
- compress_document_l2 增量语义: 同批章节二次构建 → 新增 == 0（缓存命中跳过）
- agent 链路级: Analyst 修订第二轮不再触发 compress_section（L2 零重压）

A4 开关翻转 + 旧路径删除:
- USE_MULTILEVEL_COMPRESSION 默认 True
- src/ 零残留: page_compressor 模块、pdf_sections/pdf_summary 字段全部不复存在
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _big_sections(n=3, tokens_each=800):
    """构造 T1/T2 + 超阈值的大章节（触发 L2 候选）"""
    secs = []
    for i in range(n):
        secs.append({
            "section_id": f"s_{i:03d}",
            "title": f"章节{i}",
            "tier": "T1",
            "pages": [i],
            "text": f"第{i}章论述。" + "营收增长稳健。" * (tokens_each // 8),
            "tables": [],
        })
    return secs


# ---------------------------------------------------------------------------
# A3: L2 缓存复用（跨轮零重压）
# ---------------------------------------------------------------------------

def test_a3_l2_second_build_zero_new():
    """同一批章节二次构建: 新增 == 0，缓存条目不变"""
    from extractors.section_compressor import compress_document_l2

    sections = _big_sections()
    cache = {}

    first = compress_document_l2(sections, cache, min_text_tokens=100, max_new=8, use_llm=False)
    keys_after_first = set(cache.keys())

    second = compress_document_l2(sections, cache, min_text_tokens=100, max_new=8, use_llm=False)
    keys_after_second = set(cache.keys())

    assert len(first) == 3, "首轮应构建全部 3 个候选章节"
    assert second == [], "第二轮缓存全命中，新增必须为 0"
    assert keys_after_first == keys_after_second, "缓存条目不得变化"


def test_a3_revision_round_no_recompression(monkeypatch):
    """[P5 重写] L2 构建职责移位 ContextPreparator:
    首轮 Preparator 压缩 3 章，Analyst 零压缩；修订轮两者零新增（缓存跨轮复用）"""
    from graphs.state import create_initial_state
    import agents.financial_analyst as fa
    import agents.context_preparator as cp
    import extractors.section_compressor as sc
    from utils.config import get_settings

    monkeypatch.setattr(get_settings(), "USE_MULTILEVEL_COMPRESSION", True)
    monkeypatch.setattr(fa, "is_llm_ready", lambda: True)
    monkeypatch.setattr(fa, "safe_invoke_with_tools",
                        lambda *a, **kw: {"error": False, "content": "## 公司基本面\n稳健 [P 0]", "tool_calls": [], "rounds": 1})

    compress_calls = []

    def spy_compress_section(sec, use_llm=True):
        compress_calls.append(sec.get("section_id"))
        return {"section_id": sec.get("section_id"), "thesis": "t", "key_arguments": [], "has_llm": False}

    monkeypatch.setattr(sc, "compress_section", spy_compress_section)

    st = create_initial_state("分析复星医药", pdf_path="")
    st["pdf_l1"] = {"sections": _big_sections(), "tables": [], "facts": []}
    st["extracted_entities"] = [{"entity_type": "company", "entity_name": "复星医药"}]

    # 首轮: Preparator 构建 L2（3 章全压）+ 装配；Analyst 消费现成 pdf_context
    prep1 = cp.context_preparator_node(st)
    assert len(compress_calls) == 3, "首轮应由 Preparator 压缩 3 个章节"
    st.update({k: prep1[k] for k in ("pdf_context", "pdf_l2", "pdf_l3") if k in prep1})

    out1 = fa.financial_analyst_node(st)
    assert len(compress_calls) == 3, "Analyst 不得触发压缩（去装配化）"
    st["analysis_result"] = out1.get("analysis_result", "")

    # 修订轮: Preparator 缓存全命中 + Analyst 复用，零新增压缩
    st2 = dict(st)
    st2["prev_analysis_result"] = st["analysis_result"]
    st2["review_feedback"] = "补充现金流分析"

    prep2 = cp.context_preparator_node(st2)
    st2.update({k: prep2[k] for k in ("pdf_context", "pdf_l2", "pdf_l3") if k in prep2})
    out2 = fa.financial_analyst_node(st2)

    assert len(compress_calls) == 3, "修订轮零新增压缩（L2 缓存跨轮复用）"
    assert set(prep1["pdf_l2"].keys()) == set(prep2["pdf_l2"].keys())
    assert out2["analysis_result"], "修订轮仍须产出分析"


# ---------------------------------------------------------------------------
# A4: flag 默认开 + 旧链路零残留
# ---------------------------------------------------------------------------

def test_a4_flag_defaults_true():
    from utils.config import get_settings
    assert get_settings().USE_MULTILEVEL_COMPRESSION is True, "P4 起 flag 默认开启"


def test_a4_old_path_zero_residue():
    """旧链路零残留: page_compressor 模块已删；state 无 pdf_sections/pdf_summary 字段"""
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("extractors.page_compressor")

    from graphs.state import FinancialAnalysisState, create_initial_state

    st = create_initial_state("查询", "company", "")
    assert "pdf_sections" not in st, "state 不应再初始化 pdf_sections"
    assert "pdf_summary" not in st, "state 不应再初始化 pdf_summary"

    # TypedDict 注解层面也不得再声明
    assert "pdf_sections" not in FinancialAnalysisState.__annotations__
    assert "pdf_summary" not in FinancialAnalysisState.__annotations__


def test_a4_extractor_no_eager_compression(monkeypatch):
    """ReportExtractor 不再做 eager 逐页压缩（双轨付费关闭）：无 pdf_sections/pdf_summary 输出"""
    import json
    from graphs.state import create_initial_state
    import agents.report_extractor as re_node

    # 伪造工具返回（含 structured_pages 与 l1 —— 若仍调 page_compressor 会因模块已删而炸）
    fake = json.dumps({
        "error": False,
        "extraction": {
            "companies": ["复星医药"],
            "stock_codes": ["600196.SH"],
            "financial_metrics": [],
            "ratings": [],
            "target_prices": [],
            "report_date": "",
            "structured_pages": [{"page_idx": 0, "items": []}],
            "l1": {"sections": [{"section_id": "s_000", "title": "利润表"}], "tables": [], "facts": []},
        }
    }, ensure_ascii=False)

    class _FakeTool:
        @staticmethod
        def invoke(args):
            return fake

    monkeypatch.setattr(re_node, "extract_report_key_info", _FakeTool)

    st = create_initial_state("查询", "company", "D:/fake.pdf")
    # 文件存在性检查: monkeypatch os.path.isfile
    monkeypatch.setattr(re_node.os.path, "isfile", lambda p: True)

    out = re_node.report_extractor_node(st)

    assert "pdf_sections" not in out, "不得输出 pdf_sections"
    assert "pdf_summary" not in out, "不得输出 pdf_summary"
    assert out["pdf_l1"]["sections"][0]["section_id"] == "s_000", "L1 透传不变"
    assert any(e["entity_name"] == "复星医药" for e in out["extracted_entities"])
