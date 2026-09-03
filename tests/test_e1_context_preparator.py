"""P5-E1 测试: ContextPreparator 独立节点（一次构建全链复用）+ 图确定性边"""

from graphs.state import create_initial_state
from extractors.l1_builder import build_l1


def _l1_for_prep():
    items = [
        {"type": "heading", "content": "第一节 公司简介", "level": 2},
        {"type": "text", "content": "公司主营特种装备。" * 30},
        {"type": "heading", "content": "第三节 管理层讨论与分析", "level": 2},
        {"type": "text", "content": "报告期内公司实现营业收入 1,100,000,000 元，上年同期 1,000,000,000 元。" + "经营回顾" * 400},
        {"type": "heading", "content": "合并利润表", "level": 2},
        {"type": "table", "content": "<table><tr><td>项目</td><td>2024年</td><td>2023年</td></tr>"
                "<tr><td>营业收入</td><td>1,100,000,000</td><td>1,000,000,000</td></tr></table>",
         "caption": ["单位：元"], "footnote": []},
    ]
    for i in range(30):  # 凑足 L3 构建门槛（sections >= 30）；标题各异防切分器同名合并
        items.append({"type": "heading", "content": f"补充专论 {i}", "level": 2})
        items.append({"type": "text", "content": f"附注{i}说明。"})
    items.append({"type": "heading", "content": "第十节 财务报告附注", "level": 2})
    items.append({"type": "text", "content": "财务报告附注正文。"})
    return build_l1([{"page_idx": 0, "items": items}])


def _state_with_l1():
    st = create_initial_state("分析富瑞特装", pdf_path="")
    st["pdf_l1"] = _l1_for_prep()
    st["extracted_entities"] = [{"entity_type": "company", "entity_name": "富瑞特装"}]
    return st


def test_e1_no_sections_early_return():
    from agents.context_preparator import context_preparator_node
    out = context_preparator_node(create_initial_state("q"))
    assert "chapter_map" not in out and "pdf_context" not in out  # 无章节零产出，不炸


def test_e1_builds_all_layers(monkeypatch):
    import utils.llm_client as llmc
    monkeypatch.setattr(llmc, "is_llm_ready", lambda: False)  # L2 走规则兜底
    from agents.context_preparator import context_preparator_node

    out = context_preparator_node(_state_with_l1())
    assert any(v == 3 for v in out["chapter_map"].values())          # 十节标签
    assert out["pdf_l2"], "规则兜底应建出 L2"                          # L2 急切构建
    assert "关键财务亮点" in out["pdf_l3"]["text"]                     # L3（E6 配额版）
    g = next(m for m in out["derived_metrics"] if m["label"] == "营业收入增长率" and m.get("period") == "FY2024")
    assert g["status"] == "ok" and g["display"] == "+10.00%"          # E3 算子
    c = next(x for x in out["cross_source_checks"] if x["metric"] == "营业收入")
    assert c["status"] == "consistent"                                # E5 对账
    assert out["pdf_context"]                                         # 全局装配
    assert "operating" in out["domain_contexts"] and "financial" in out["domain_contexts"]  # 领域装配


def test_e1_l2_second_call_zero_new(monkeypatch):
    """修订轮二次进入: L2 缓存零新增（跨轮复用）"""
    import utils.llm_client as llmc
    import extractors.section_compressor as sc
    monkeypatch.setattr(llmc, "is_llm_ready", lambda: False)
    from agents.context_preparator import context_preparator_node

    calls = []
    real = sc.compress_section
    monkeypatch.setattr(sc, "compress_section",
                        lambda sec, use_llm=True: (calls.append(sec["section_id"]),
                                                   real(sec, use_llm=False))[1])
    st = _state_with_l1()
    out1 = context_preparator_node(st)
    n1 = len(calls)
    assert n1 > 0
    st2 = dict(st); st2.update({k: out1[k] for k in ("pdf_l2", "pdf_l3")})
    context_preparator_node(st2)
    assert len(calls) == n1, "二次进入零新增压缩"


def test_e1_domain_gate_by_coverage_and_flag(monkeypatch):
    import utils.llm_client as llmc
    from utils.config import get_settings
    monkeypatch.setattr(llmc, "is_llm_ready", lambda: False)
    from agents.context_preparator import context_preparator_node

    st = _state_with_l1()
    monkeypatch.setattr(get_settings(), "DOMAIN_CHAPTER_COVERAGE_MIN", 1.1)  # 覆盖率不可达标
    assert context_preparator_node(st)["domain_contexts"] == {}
    monkeypatch.setattr(get_settings(), "DOMAIN_CHAPTER_COVERAGE_MIN", 0.5)
    monkeypatch.setattr(get_settings(), "USE_DOMAIN_AGENTS", False)          # flag 关
    assert context_preparator_node(st)["domain_contexts"] == {}


def test_e1_graph_deterministic_edges():
    """确定性边: report_extractor → context_preparator → supervisor（不经 Supervisor LLM 路由）"""
    from graphs.financial_graph import FinancialAnalysisGraph
    wf = FinancialAnalysisGraph()._build_graph()
    edges = set(wf.edges)
    assert ("report_extractor", "context_preparator") in edges
    assert ("context_preparator", "supervisor") in edges
    assert ("report_extractor", "supervisor") not in edges


def test_e1_state_defaults():
    st = create_initial_state("q")
    assert st["chapter_map"] == {} and st["domain_contexts"] == {}
    assert st["derived_metrics"] == [] and st["cross_source_checks"] == []
    assert st["domain_analyses"] == {} and st["defect_domain"] == ""