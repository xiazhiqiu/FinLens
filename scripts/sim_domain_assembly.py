"""P5 前置验证: 零 LLM 领域装配模拟（纯确定性，不烧钱）

验证 backlog P5 三个开工前提:
1. 各领域章节子集 token 实测装得下（对照 24k 预算；超窗部分由 L2/工具兜底）
2. 跨领域勾稽不塌（facts 全局可查: 关键科目命中数）
3. 总成本可控（5 域装配总量 vs 单 Analyst 全局装配）

用法: python scripts/sim_domain_assembly.py [furui|joinn]
产物: 控制台报告 + data/sim_domain_assembly_<sample>.json
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

BUDGET = 24000

PROFILES = {
    "furui": r"D:\develop\财报分析助手\m1\out\furui_v2\szse_simple_2024_annual\auto\szse_simple_2024_annual_content_list.json",
    "joinn": r"D:\develop\财报分析助手\m1\out\joinn_v3\joinn_2024_annual\auto\joinn_2024_annual_content_list.json",
}

KEY_METRICS = ["营业收入", "归母净利润", "净利润", "资产总计", "负债合计", "股东权益合计", "基本每股收益"]


def main():
    sample = sys.argv[1] if len(sys.argv) > 1 else "furui"
    from extractors.mineru_extractor import _content_list_to_structured_pages
    from extractors.l1_builder import build_l1
    from extractors.chapter_tagger import DOMAINS, tag_chapters, chapter_token_coverage, sections_for_domain
    from extractors.context_assembler import assemble
    from utils.token_counter import count_tokens_safe

    cl = json.loads(Path(PROFILES[sample]).read_text(encoding="utf-8"))
    pages = _content_list_to_structured_pages(cl)
    l1 = build_l1(pages)
    sections = l1["sections"]
    tables_by_id = {t["table_id"]: t for t in l1["tables"]}

    cmap = tag_chapters(sections)
    coverage = chapter_token_coverage(sections, cmap)
    print(f"[{sample}] 章节 {len(sections)} / 表格 {len(l1['tables'])} / facts {len(l1['facts'])}")
    print(f"[{sample}] 十节覆盖率（非 T3 text token）: {coverage:.1%}  （阈值 0.5，不足回退全局装配）")

    facts = [f for f in l1["facts"] if f.get("confidence", 0) >= 0.9]
    std_names = {f.get("metric_std") or f.get("metric") for f in facts}
    hits = {k: (k in std_names) for k in KEY_METRICS}
    print(f"[{sample}] 关键科目命中: {sum(hits.values())}/{len(KEY_METRICS)} {hits}")

    report = {"sample": sample, "coverage": coverage, "n_facts": len(l1["facts"]),
              "key_metric_hits": hits, "domains": {}}
    total_used = 0
    for d in DOMAINS:
        sub = sections_for_domain(sections, cmap, d["key"])
        if not sub:
            report["domains"][d["key"]] = {"n_sections": 0}
            print(f"  {d['key']:12s} （无章节，领域 agent 跳过）")
            continue
        raw_tokens = sum(
            count_tokens_safe(s.get("text") or "")
            + sum(count_tokens_safe(t.get("html") or "") for t in
                  (tables_by_id.get(tid) for tid in s.get("table_ids") or []) if t)
            for s in sub
        )
        a = assemble("", BUDGET, {"sections": sub, "tables": l1["tables"]})
        stats = a["stats"]
        ratio = stats["n_injected"] / max(1, len(sub))
        total_used += stats["used"]
        report["domains"][d["key"]] = {"n_sections": len(sub), "raw_tokens": raw_tokens, **stats}
        print(f"  {d['key']:12s} 章节 {len(sub):3d} 原文 {raw_tokens:6d}tok | "
              f"装配 {stats['used']:5d}/{BUDGET} 直注 {stats['n_injected']:3d} "
              f"指针 {stats['n_pointers']:3d} 直注率 {ratio:.0%}")

    g = assemble("", BUDGET, l1)
    report["global"] = g["stats"]
    report["domains_total_used"] = total_used
    print(f"  {'(全局对照)':12s} 装配 {g['stats']['used']:5d}/{BUDGET} 直注 {g['stats']['n_injected']:3d}")
    print(f"[{sample}] 5 域合计 {total_used} tok vs 全局 {g['stats']['used']} tok"
          f"（倍率 {total_used / max(1, g['stats']['used']):.1f}x）")

    out = Path("data") / f"sim_domain_assembly_{sample}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告已写入 {out}")


if __name__ == "__main__":
    main()
