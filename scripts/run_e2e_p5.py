"""
P5 端到端验收（一次性脚本，2026-09-04）

用法:
  1. python scripts/run_e2e_a5.py seed furui   # seed 复用 a5（parse_cache 按 pdf hash 共享）
  2. python scripts/run_e2e_a5.py seed joinn
  3. python scripts/run_e2e_p5.py run furui    # A股主验收样本（应进领域模式）
  4. python scripts/run_e2e_p5.py run joinn    # 港股回归护栏（应回退全局装配）

P5 验收断言:
- furui: domain_contexts >= 3 域 / domain_analyses 有产出 / derived_metrics ok >= 1 /
  cross_source_checks 产出 / 报告 [P 页码] 引用 >= 1 / facts 抽样溯源 10/10
- joinn: domain_contexts == {}（十节覆盖不足自动回退全局装配）/ 全链路 done / 报告非空
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PROFILES = {
    "joinn": {
        "pdf": r"D:\develop\财报分析助手\samples\smoke\joinn_2024_annual.pdf",
        "query": "撰写昭衍新药 2024 年年报的投资分析报告，重点评估财务表现与风险",
        "report_out": "p5_e2e_report_joinn.md",
        "metrics_out": "p5_e2e_metrics_joinn.json",
    },
    "furui": {
        "pdf": r"D:\develop\财报分析助手\m1\out\furui_v2\szse_simple_2024_annual\auto\szse_simple_2024_annual_origin.pdf",
        "query": "撰写富瑞特装 2024 年年报的投资分析报告，重点评估财务表现与风险",
        "report_out": "p5_e2e_report_furui.md",
        "metrics_out": "p5_e2e_metrics_furui.json",
    },
}


def run():
    sample = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] in PROFILES else "furui"
    profile = PROFILES[sample]
    report_out = ROOT / "data" / profile["report_out"]
    metrics_out = ROOT / "data" / profile["metrics_out"]

    from graphs.financial_graph import FinancialAnalysisGraph

    result = FinancialAnalysisGraph().invoke(
        user_query=profile["query"],
        pdf_path=profile["pdf"],
        report_type="company",
    )

    final_report = result.get("final_report", "")
    report_out.write_text(final_report, encoding="utf-8")

    l1 = result.get("pdf_l1") or {}
    facts = l1.get("facts", [])
    sample_facts = [f for f in facts if f.get("raw")][:10]
    hit = sum(1 for f in sample_facts if str(f["raw"]) in final_report)
    citations = re.findall(r"\[P\s*(\d+)\]", final_report)
    derived = result.get("derived_metrics") or []
    cross = result.get("cross_source_checks") or []
    domain_ctx = result.get("domain_contexts") or {}
    domain_out = result.get("domain_analyses") or {}

    metrics = {
        "report_chars": len(final_report),
        "review_result": result.get("review_result"),
        "review_revisions": result.get("review_revision_count"),
        "defect_domain": result.get("defect_domain", ""),
        "llm_error_log": result.get("error_log", []),
        "sections": len(l1.get("sections", [])),
        "facts": len(facts),
        "l2_entries": len(result.get("pdf_l2") or {}),
        "domain_contexts": sorted(domain_ctx.keys()),
        "domain_analyses": sorted(domain_out.keys()),
        "derived_metrics_ok": sum(1 for m in derived if m.get("status") == "ok"),
        "derived_metrics_skipped": sum(1 for m in derived if m.get("status") != "ok"),
        "cross_source_checks": {c.get("metric"): c.get("status") for c in cross},
        "citations_in_report": len(citations),
        "unique_pages_cited": len(set(citations)),
        "fact_sample_hit": hit,
        "fact_sample_total": len(sample_facts),
        "tool_calls_by_agent": {},
        "agent_status": result.get("agent_status", {}),
    }
    for c in result.get("tool_call_history", []):
        a = c.get("agent", "?")
        metrics["tool_calls_by_agent"][a] = metrics["tool_calls_by_agent"].get(a, 0) + 1

    # ---- P5 断言 ----
    failures = []
    if sample == "furui":
        if len(domain_ctx) < 3:
            failures.append(f"领域模式未生效: domain_contexts={sorted(domain_ctx)}")
        if not domain_out:
            failures.append("domain_analyses 无产出（领域 agent 组全灭？）")
        if metrics["derived_metrics_ok"] < 1:
            failures.append("派生指标 ok 数为 0")
        if not cross:
            failures.append("cross_source_checks 为空")
        if hit < len(sample_facts):
            failures.append(f"facts 抽样溯源 {hit}/{len(sample_facts)}")
    elif sample == "joinn":
        if domain_ctx:
            failures.append(f"joinn 应回退全局装配，实际 domain_contexts={sorted(domain_ctx)}")
        if not final_report:
            failures.append("final_report 为空")
    if len(citations) < 1:
        failures.append("报告无 [P 页码] 引用")

    metrics_out.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    if failures:
        print(f"\n[P5-E2E] FAIL ({sample}):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print(f"\n[P5-E2E] PASS ({sample}). 报告已存: {report_out}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        run()
    else:
        print(__doc__)