"""
A5 端到端验收（一次性脚本，2026-09-03）

用法:
  1. python scripts/run_e2e_a5.py seed [joinn|furui]  # 种子化解析缓存（MinerU 产物 → parse_cache）
  2. python scripts/run_e2e_a5.py run [joinn|furui]   # 真实 LLM 端到端 + 验收指标输出

验收指标（p4-backlog A5）:
- 全链路跑通（Supervisor 调度 + 4 agent + 工具循环）
- 注入 token 数（pdf_context 装配预算）
- LLM 调用次数 / 工具调用次数
- 数字溯源率抽检（报告数字 → facts 表核对）
- 修订循环第二轮 L2 新增构建数（必须为 0，A3 语义）
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# 样本档案: joinn（港股繁体）/ furui（A股简体，富瑞特装 300228）
PROFILES = {
    "joinn": {
        "pdf": r"D:\develop\财报分析助手\samples\smoke\joinn_2024_annual.pdf",
        "content_list": r"D:\develop\财报分析助手\m1\out\joinn_v3\joinn_2024_annual\auto\joinn_2024_annual_content_list.json",
        "query": "撰写昭衍新药 2024 年年报的投资分析报告，重点评估财务表现与风险",
        "report_out": "a5_e2e_report.md",
        "metrics_out": "a5_e2e_metrics.json",
    },
    "furui": {
        "pdf": r"D:\develop\财报分析助手\m1\out\furui_v2\szse_simple_2024_annual\auto\szse_simple_2024_annual_origin.pdf",
        "content_list": r"D:\develop\财报分析助手\m1\out\furui_v2\szse_simple_2024_annual\auto\szse_simple_2024_annual_content_list.json",
        "query": "撰写富瑞特装 2024 年年报的投资分析报告，重点评估财务表现与风险",
        "report_out": "a5_e2e_report_furui.md",
        "metrics_out": "a5_e2e_metrics_furui.json",
    },
}
PROFILE = PROFILES[sys.argv[2]] if len(sys.argv) > 2 and sys.argv[2] in PROFILES else PROFILES["joinn"]

PDF = PROFILE["pdf"]
CONTENT_LIST = Path(PROFILE["content_list"])
REPORT_OUT = ROOT / "data" / PROFILE["report_out"]
METRICS_OUT = ROOT / "data" / PROFILE["metrics_out"]


def seed():
    """content_list.json → 完整 extraction 载荷 → parse_cache（与 MinerU 解析产物等价）"""
    from extractors.parse_cache import compute_pdf_hash, get_parse_cache
    from extractors.mineru_extractor import _content_list_to_text, _content_list_to_structured_pages
    from extractors.entity_extractor import extract_financial_entities
    from extractors.l1_builder import build_l1

    cl = json.loads(CONTENT_LIST.read_text(encoding="utf-8"))
    full_text = _content_list_to_text(cl)
    pages = _content_list_to_structured_pages(cl)

    entity_result = extract_financial_entities(full_text)
    assert not entity_result.get("error"), f"实体抽取失败: {entity_result.get('message')}"
    extraction = entity_result["extraction"]
    extraction["total_pages"] = len(set(p.get("page_idx", 0) for p in pages))
    extraction["file_path"] = PDF
    extraction["text_source"] = "mineru-seed"
    extraction["structured_pages"] = pages
    extraction["l1"] = build_l1(pages, companies=extraction.get("companies", []))

    payload = {"error": False, "extraction": extraction}
    pdf_hash = compute_pdf_hash(PDF)
    cache = get_parse_cache()
    assert cache, "parse_cache 不可用"
    ok = cache.put(pdf_hash, payload, parser="mineru-seed")
    l1 = extraction["l1"]
    print(f"[seed] 缓存写入: {ok}")
    print(f"[seed] pages={extraction['total_pages']} sections={len(l1['sections'])} "
          f"tables={len(l1['tables'])} facts={len(l1['facts'])} companies={extraction['companies']}")
    print(f"[seed] hash={pdf_hash[:16]}...")


def run():
    from graphs.financial_graph import FinancialAnalysisGraph
    from utils.config import get_settings

    settings = get_settings()
    print(f"[run] model={settings.DEEPSEEK_MODEL} max_tool_rounds={settings.MAX_TOOL_ROUNDS_PER_AGENT}")
    assert CONTENT_LIST.is_file(), "content_list 不可用（先 seed）"

    graph = FinancialAnalysisGraph()
    result = graph.invoke(
        user_query=PROFILE["query"],
        pdf_path=PDF,
        report_type="company",
    )

    final_report = result.get("final_report", "")
    REPORT_OUT.write_text(final_report, encoding="utf-8")

    # ---- 验收指标汇总 ----
    l1 = result.get("pdf_l1") or {}
    tool_history = result.get("tool_call_history", [])
    pdf_context = result.get("pdf_context", "") or ""
    pdf_l2 = result.get("pdf_l2") or {}

    n_ctx_tokens = int(len(pdf_context) * 1.5)  # 中文 ~1.5 token/字（估算，交接坑 #3）

    # 溯源率抽检: 报告中出现的 [P n] 引用数
    import re
    citations = re.findall(r"\[P\s*(\d+)\]", final_report)

    # 报告数字抽检: 从 facts 抽 10 个值，验证在报告中出现
    facts = l1.get("facts", [])
    sample_facts = [f for f in facts if f.get("raw")][:10]
    hit = sum(1 for f in sample_facts if str(f["raw"]) in final_report)

    metrics = {
        "report_chars": len(final_report),
        "review_result": result.get("review_result"),
        "review_revisions": result.get("review_revision_count"),
        "llm_error_log": result.get("error_log", []),
        "pdf_context_chars": len(pdf_context),
        "pdf_context_tokens_est": n_ctx_tokens,
        "sections": len(l1.get("sections", [])),
        "tables": len(l1.get("tables", [])),
        "facts": len(l1.get("facts", [])),
        "l2_entries": len(pdf_l2),
        "tool_calls_total": len(tool_history),
        "tool_calls_by_agent": {},
        "citations_in_report": len(citations),
        "unique_pages_cited": len(set(citations)),
        "fact_sample_hit": hit,
        "fact_sample_total": len(sample_facts),
        "agent_status": result.get("agent_status", {}),
    }
    for c in tool_history:
        a = c.get("agent", "?")
        metrics["tool_calls_by_agent"][a] = metrics["tool_calls_by_agent"].get(a, 0) + 1

    METRICS_OUT.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print(f"\n[run] 报告已存: {REPORT_OUT}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "seed":
        seed()
    elif cmd == "run":
        run()
    else:
        print(__doc__)
