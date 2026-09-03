# P5 架构演进实施计划（E1-E6）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 P5 六项演进——E1 ContextPreparator 独立节点、E2「A股十节 → 5 领域 agent + Synthesizer」、E3 确定性算子层、E4 Writer 卖方报告模板、E5 facts↔散文跨源对账、E6 L3 配额抽选，全程不破坏 E2E 基线（furui 溯源 10/10、全量测试绿）。

**Architecture:** 保持 Supervisor + 确定性节点 + LLM 受限叶子架构。新增 `context_preparator` 图节点（十节标签 + L2 急切构建 + L3 + 派生指标 + 跨源对账 + 全局/领域装配，一次构建全链复用），经**确定性边** `report_extractor → context_preparator → supervisor` 接入（不经 LLM 路由，规避 Supervisor 误路由风险）；`financial_analyst` 在 flag 门控下内部编排 5 个领域 agent（并行直读各自章节）+ Synthesizer 综合产出，修订按 `defect_domain` 精准回炉；港股/非标模板（十节覆盖率不足）自动回退现有全局装配路径。

**Tech Stack:** LangGraph StateGraph（确定性边）、ThreadPoolExecutor（领域 agent 并行）、纯 Python 确定性算子（复用 B7 `_UNIT_SCALE` 单位折算口径）、pytest + monkeypatch。

---

## 基线与护栏（每个 Task 都要守住）

- **测试基线**: 当前全量 145 条全绿。每 Task 完成后 `pytest` 全量跑一遍，红了先修再提交。
- **E2E 基线**: furui（A股 258 页）溯源 10/10、页码引用、修订循环可发生；joinn（港股 536 章）全链路 done。
- **回退路径永远活着**: `USE_DOMAIN_AGENTS=false` 或十节覆盖率 < 0.5 → 走现有全局装配（joinn 是回归护栏样本）。
- **真实样本路径**（`scripts/run_e2e_a5.py` 同款）:
  - furui: `D:\develop\财报分析助手\m1\out\furui_v2\szse_simple_2024_annual\auto\szse_simple_2024_annual_content_list.json`
  - joinn: `D:\develop\财报分析助手\m1\out\joinn_v3\joinn_2024_annual\auto\joinn_2024_annual_content_list.json`
- **测试运行方式**: 项目根目录 `pytest`（`pyproject.toml` 已配 `pythonpath = ["src"]`，测试内直接 `from extractors.xxx import ...`）。

## 已定决策（backlog P5 节，不再讨论）

- ❌ 取消「查询宽度分类」（系统目标是端到端报告生成，无轻量查数场景）
- ✅ A股十节 → 5 领域 agent（第八节/第九节多为空模板不配独立 agent，归入既有域）
- ❌ 不做「一章一 agent」map-reduce；❌ 不做「全 L2 / 纯目录+工具」（表格是 L2 盲点 + E2E 基线是 L1 直注取得）
- 领域拆分: overview(0,1,8,99) / operating(3) / financial(2,9,10) / governance(4,5) / events(6,7)

## 任务依赖

```
Task 0 chapter_tagger ──→ Task 1 零 LLM 装配模拟（三前提 go/no-go）
                                │
Task 2 E6 L3 配额（独立）       │
Task 3 E3 算子层（独立）        │
Task 4 E5 跨源对账（独立）      │
                                ↓
                     Task 5 E1 ContextPreparator（集成 0/2/3/4 + 图接线 + Analyst 去装配化）
                                ↓
                     Task 6 E2 领域 agent 组 + Synthesizer + Analyst 门控
                                ↓
                     Task 7 Reviewer defect_domain + 跨源告警消费
                                ↓
                     Task 8 E4 Writer 卖方模板 + 派生指标表/告警注入
                                ↓
                     Task 9 双样本 E2E 验收 + backlog 收尾
```

Task 1 是 E2 的开工闸门：三个前提不达标则 E2 缓行（E1/E3/E4/E5/E6 仍然成立，照做）。

---

### Task 0: 十节章节标签器 `chapter_tagger.py`

**Files:**
- Create: `src/extractors/chapter_tagger.py`
- Test: `tests/test_chapter_tagger.py`

- [ ] **Step 0.1: 写失败测试**

```python
"""
P5 Task0 测试: A股年报十节模板标签器（纯规则零 LLM）

覆盖:
- 章节号识别: 第N节 正则（1-10）；前置内容=0；第十节后备查文件=99
- T3（噪声，如目录）不推进章节游标
- 覆盖率: 非T3 text token 中归属 1-10/99 的占比（港股无模板 → ~0）
- 领域映射: 5 域章节子集
"""

from extractors.chapter_tagger import (
    DOMAINS, tag_chapters, chapter_token_coverage, sections_for_domain,
)


def _sec(sid, title, tier="T1", text="x" * 100):
    return {"section_id": sid, "title": title, "tier": tier, "text": text, "page_range": [1]}


def test_tag_chapters_basic_flow():
    secs = [
        _sec("s_001", "重要提示"),                      # 前置 → 0
        _sec("s_002", "目录", tier="T3"),               # T3 不推进（仍 0）
        _sec("s_003", "第一节 公司简介"),               # → 1
        _sec("s_004", "第二节 主要会计数据"),            # → 2（标题变了，继承误判防护: 未命中不推进）
        _sec("s_005", "第三节 管理层讨论与分析"),         # → 3
        _sec("s_006", "第十节 财务报告"),                # → 10
        _sec("s_007", "备查文件"),                      # 10 之后 → 99
    ]
    m = tag_chapters(secs)
    assert m == {"s_001": 0, "s_002": 0, "s_003": 1, "s_004": 2, "s_005": 3, "s_006": 10, "s_007": 99}


def test_tag_chapters_t3_does_not_advance():
    """目录页列了全部章节名（T3 噪声）——不得推进游标"""
    secs = [
        _sec("s_001", "目录", tier="T3"),      # 列出「第三节 管理层讨论与分析」等
        _sec("s_002", "第一节 公司简介"),
    ]
    m = tag_chapters(secs)
    assert m["s_001"] == 0 and m["s_002"] == 1


def test_tag_chapters_hk_no_template():
    """港股无十节模板 → 全部 0，覆盖率 ~0（回退全局装配的门控信号）"""
    secs = [_sec("s_001", "財務報表"), _sec("s_002", "主席報告"), _sec("s_003", "企業管治報告")]
    m = tag_chapters(secs)
    assert set(m.values()) == {0}


def test_coverage_excludes_t3_and_preamble():
    secs = [
        _sec("s_001", "重要提示", text="a" * 100),            # chapter 0，不计入分子
        _sec("s_002", "目录", tier="T3", text="b" * 100),     # T3，不计入分母
        _sec("s_003", "第三节 管理层讨论与分析", text="c" * 100),
        _sec("s_004", "第十节 财务报告", text="d" * 100),
        _sec("s_005", "备查文件", text="e" * 100),            # 99，计入分子
    ]
    m = tag_chapters(secs)
    cov = chapter_token_coverage(secs, m)
    # 分子 = ch3 + ch10 + 99（3 份等长文本），分母 = ch0 + 这 3 份 → 0.75
    assert abs(cov - 0.75) < 1e-6


def test_sections_for_domain_mapping():
    secs = [
        _sec("s_001", "重要提示"),
        _sec("s_002", "第一节 公司简介"),
        _sec("s_003", "第三节 管理层讨论与分析"),
        _sec("s_004", "第二节 主要会计数据"),
        _sec("s_005", "第十节 财务报告"),
        _sec("s_006", "备查文件"),
    ]
    m = tag_chapters(secs)
    ids = lambda ss: [s["section_id"] for s in ss]
    assert ids(sections_for_domain(secs, m, "operating")) == ["s_003"]
    assert ids(sections_for_domain(secs, m, "financial")) == ["s_004", "s_005"]
    assert ids(sections_for_domain(secs, m, "overview")) == ["s_001", "s_002", "s_006"]  # 0,1,99
    assert sections_for_domain(secs, m, "nonexistent") == []
    assert {d["key"] for d in DOMAINS} == {"overview", "operating", "financial", "governance", "events"}
```

- [ ] **Step 0.2: 运行确认失败**

Run: `pytest tests/test_chapter_tagger.py -v`
Expected: FAIL（`ModuleNotFoundError: extractors.chapter_tagger`）

- [ ] **Step 0.3: 实现**

```python
"""
FinScope A股年报十节模板标签器（P5，纯规则零 LLM）

A股年报遵循证监会标准化十节模板:
第一节 公司简介 / 第二节 会计数据·财务指标 / 第三节 管理层讨论与分析(MD&A) /
第四节 公司治理 / 第五节 环境和社会责任 / 第六节 重要事项 /
第七节 股份变动及股东情况 / 第八节 优先股 / 第九节 债券 / 第十节 财务报告

- tag_chapters: section_id -> 章节号（0 前置 / 1-10 标准节 / 99 十节外尾注）
- chapter_token_coverage: 十节识别覆盖率（非 T3 text token 占比）——低于阈值的
  非标模板（港股 joinn 等）回退全局装配，不进领域模式
- sections_for_domain: 领域 key -> 章节子集（E2 领域 agent 直读范围）
"""

import re
from typing import Dict, List, Optional

from utils.token_counter import count_tokens_safe

# 5 领域定义（backlog P5 已定决策: 第八/九节多为空模板不配独立 agent，归入既有域）
DOMAINS = [
    {"key": "overview",   "name": "公司概览与风险", "chapters": (0, 1, 8, 99)},
    {"key": "operating",  "name": "经营分析(MD&A)", "chapters": (3,)},
    {"key": "financial",  "name": "财务数据",       "chapters": (2, 9, 10)},
    {"key": "governance", "name": "治理与ESG",      "chapters": (4, 5)},
    {"key": "events",     "name": "重要事项与股东", "chapters": (6, 7)},
]

_CHAPTER_RE = re.compile(r"^第([一二三四五六七八九十])节")
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_TAIL_RE = re.compile(r"备查文件")


def _parse_chapter_no(title: str) -> Optional[int]:
    m = _CHAPTER_RE.match(title.strip())
    return _CN_NUM.get(m.group(1)) if m else None


def tag_chapters(sections: List[Dict]) -> Dict[str, int]:
    """section_id -> 章节号。T3（噪声/目录）不打章节头（防目录页整页误推进游标）。"""
    out: Dict[str, int] = {}
    current = 0
    for sec in sections or []:
        title = (sec.get("title") or "").strip()
        no = _parse_chapter_no(title) if sec.get("tier") != "T3" else None
        if no is not None:
            current = no
        elif current == 10 and _TAIL_RE.search(title):
            current = 99  # 第十节之后的备查文件等尾注
        out[sec.get("section_id", "?")] = current
    return out


def chapter_token_coverage(sections: List[Dict], chapter_map: Dict[str, int]) -> float:
    """非 T3 text token 中，被十节模板覆盖（章节号 ≠ 0，含尾注 99）的占比。"""
    total = tagged = 0
    for s in sections or []:
        if s.get("tier") == "T3":
            continue
        t = count_tokens_safe(s.get("text") or "")
        total += t
        if chapter_map.get(s.get("section_id", "?"), 0) != 0:
            tagged += t
    return tagged / total if total else 0.0


def sections_for_domain(
    sections: List[Dict], chapter_map: Dict[str, int], domain_key: str
) -> List[Dict]:
    """领域 key -> 章节子集（未知 key 返回空列表）"""
    chapters = next((d["chapters"] for d in DOMAINS if d["key"] == domain_key), ())
    if not chapters:
        return []
    return [
        s for s in sections or []
        if chapter_map.get(s.get("section_id", "?"), 0) in chapters
    ]
```

- [ ] **Step 0.4: 运行确认通过 + 全量回归**

Run: `pytest tests/test_chapter_tagger.py -v && pytest`
Expected: 新增 5 条 PASS，全量 145+5=150 PASS

- [ ] **Step 0.5: Commit**

```bash
git add src/extractors/chapter_tagger.py tests/test_chapter_tagger.py
git commit -m "feat(p5): 十节章节标签器（纯规则零 LLM，领域拆分地基）"
```

---

### Task 1: 零 LLM 装配模拟（三前提验证，E2 开工闸门）

**Files:**
- Create: `scripts/sim_domain_assembly.py`

本任务无单测（一次性验证脚本，靠真实样本跑出来的数字说话），产出记录进本文件。

- [ ] **Step 1.1: 写模拟脚本**

```python
"""
P5 前置验证: 零 LLM 领域装配模拟（纯确定性，不烧钱）

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

    # 前提 2: 关键科目 facts 命中（跨域勾稽的原料；query_fact 全局可查）
    facts = [f for f in l1["facts"] if f.get("confidence", 0) >= 0.9]
    std_names = {f.get("metric_std") or f.get("metric") for f in facts}
    hits = {k: (k in std_names) for k in KEY_METRICS}
    print(f"[{sample}] 关键科目命中: {sum(hits.values())}/{len(KEY_METRICS)} {hits}")

    # 前提 1+3: 逐领域装配（零 L2，测的是纯直注容量）
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

    # 对照: 单 Analyst 全局装配
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
```

- [ ] **Step 1.2: 跑双样本**

Run: `python scripts/sim_domain_assembly.py furui && python scripts/sim_domain_assembly.py joinn`
Expected: furui 覆盖率显著 > 0.5、5 域各自有章节；joinn 覆盖率 ~0（回退路径确认）

- [ ] **Step 1.3: 记录三前提结论（写回本文件「执行记录」节）**

判定标准:
- **前提 1（装得下）**: 各域直注率 ≥ 60% 视为达标；MD&A（operating）超窗部分明确由 L2 摘要 + fetch_context 兜底（记录实际溢出量）
- **前提 2（勾稽不塌）**: furui 关键科目命中 ≥ 4/7
- **前提 3（成本可控）**: 5 域合计 ≤ 5× 全局（结构性上限），记录实测倍率

任一不达标 → 停下来与用户对齐（E2 缓行，E1/E3/E4/E5/E6 不受影响继续）。

- [ ] **Step 1.4: Commit**

```bash
git add scripts/sim_domain_assembly.py
git commit -m "test(p5): 零 LLM 领域装配模拟——三前提验证脚本"
```

---

### Task 2: E6 L3 亮点分桶配额（修大数霸屏）

**Files:**
- Modify: `src/extractors/section_compressor.py`（`build_global_summary_lite` 的「按 |value| 排序取前 6」段，约 L218-224）
- Test: `tests/test_e6_l3_quota.py`

- [ ] **Step 2.1: 写失败测试**

```python
"""P5-E6 测试: L3 亮点按指标类型分桶配额（修「资产总计挤掉 EPS/ROE」大数霸屏）"""

from extractors.section_compressor import _bucket_of, build_global_summary_lite


def _f(metric, value, raw, is_pct=False, period="FY2024"):
    return {"company": "富瑞特装", "metric": metric, "metric_std": metric, "period": period,
            "value": value, "raw": raw, "is_pct": is_pct, "is_subtotal": False,
            "unit": "", "confidence": 0.9, "source": {"page_idx": 8, "table_id": "t_001"}}


def test_e6_bucket_of_classification():
    assert _bucket_of(_f("基本每股收益", 0.38, "0.3804")) == "ratio"
    assert _bucket_of(_f("净资产收益率", 10.77, "10.77%", is_pct=True)) == "ratio"
    assert _bucket_of(_f("资产负债率", 60.0, "60%")) == "ratio"      # 以「率」结尾
    assert _bucket_of(_f("归母净利润", 2.2e8, "219,215,799.89")) == "profit"
    assert _bucket_of(_f("营业收入", 3.3e9, "3,322,399,262.33")) == "income"
    assert _bucket_of(_f("资产总计", 2.4e9, "2,400,000,000")) == "balance"


def test_e6_quota_keeps_small_high_signal_metrics():
    """旧逻辑 |value| 前 6 会把 EPS/ROE 挤掉；配额后各桶均衡、EPS/ROE 必入选"""
    l1 = {"facts": [
        _f("资产总计", 2.4e9, "2,400,000,000"),        # balance
        _f("负债合计", 1.4e9, "1,400,000,000"),        # balance
        _f("流动资产合计", 1.2e9, "1,200,000,000"),     # balance（第 3 大数）
        _f("流动负债合计", 8e8, "800,000,000"),         # balance（第 4 大数）
        _f("营业收入", 3.3e9, "3,322,399,262.33"),     # income
        _f("归母净利润", 2.2e8, "219,215,799.89"),     # profit
        _f("基本每股收益", 0.38, "0.3804"),             # ratio（小数，旧逻辑必被挤掉）
        _f("净资产收益率", 10.77, "10.77%", is_pct=True),  # ratio
    ]}
    text = build_global_summary_lite(l1, company="富瑞特装")
    # ratio 桶 2 席: EPS 与 ROE 都在
    assert "0.3804" in text and "10.77" in text, "高信号小指标必须入选"
    # balance 桶 2 席: 第 3/4 大数被配额截掉
    assert "流动资产合计" not in text and "流动负债合计" not in text
    # 确定性
    assert text == build_global_summary_lite(l1, company="富瑞特装")
```

- [ ] **Step 2.2: 运行确认失败**

Run: `pytest tests/test_e6_l3_quota.py -v`
Expected: FAIL（`ImportError: _bucket_of`；且旧逻辑下 EPS 被挤掉）

- [ ] **Step 2.3: 实现（修改 `section_compressor.py`）**

模块常量区（`_HIGHLIGHT_WORDS` 之后）加:

```python
# [E6] 指标分桶 + 配额: 旧逻辑按 |value| 全局前 6，大数霸屏（资产总计挤掉 EPS/ROE）
_L3_BUCKETS = ("ratio", "profit", "income", "balance")
_L3_QUOTA = {"ratio": 2, "profit": 2, "income": 2, "balance": 2}


def _bucket_of(fact: Dict[str, Any]) -> str:
    """指标类型分桶（判定顺序即优先级: 比率 > 利润 > 收入 > 资产负债兜底）"""
    m = fact.get("metric_std") or fact.get("metric") or ""
    if fact.get("is_pct") or "每股" in m or m in ("ROE", "EPS", "PE") or m.endswith("率"):
        return "ratio"
    if any(w in m for w in ("净利", "淨利", "利润", "利潤", "纯利", "純利", "溢利", "毛利")):
        return "profit"
    if any(w in m for w in ("收入", "收益", "营收", "營收")):
        return "income"
    return "balance"
```

`build_global_summary_lite` 内替换「按期间分组，每组按 |value| 排序取前 6」段:

```python
    # 按期间分组；[E6] 桶内 |value| 排序、每桶固定配额（替代旧全局 |value| 前 6）
    by_period: Dict[str, List[Dict[str, Any]]] = {}
    for f in highlight:
        by_period.setdefault(f.get("period") or "未知", []).append(f)
    for period, fs in by_period.items():
        picked: List[Dict[str, Any]] = []
        for bucket in _L3_BUCKETS:
            group = [f for f in fs if _bucket_of(f) == bucket]
            group.sort(key=lambda x: -abs(x.get("value") or 0))
            picked.extend(group[: _L3_QUOTA[bucket]])
        by_period[period] = picked
```

同步更新 `build_global_summary_lite` docstring 中「每期间最多取 |value| 前 6 条」为「每期间按指标类型分桶取配额」。

- [ ] **Step 2.4: 运行确认通过 + 全量回归**

Run: `pytest tests/test_e6_l3_quota.py -v && pytest`
Expected: 新增 2 条 PASS；`test_p3_compressor.py::test_l3_lite_deterministic_and_labelled` 仍 PASS（其 fixture 只有 1 income fact，不受配额影响）。全量 150+2=152。

- [ ] **Step 2.5: Commit**

```bash
git add src/extractors/section_compressor.py tests/test_e6_l3_quota.py
git commit -m "feat(p5): E6 L3 亮点分桶配额——收入/利润/比率/资产各留席位，修大数霸屏"
```

---

### Task 3: E3 确定性算子层 `derived_metrics.py`

**Files:**
- Create: `src/extractors/derived_metrics.py`
- Test: `tests/test_e3_derived_metrics.py`

- [ ] **Step 3.1: 写失败测试**

```python
"""P5-E3 测试: 确定性算子层（增长率/利润率/偿债比率，纯 Python 零 LLM）"""

from extractors.derived_metrics import compute_derived_metrics, render_derived_metrics


def _f(metric, period, value, unit="元", page=8, table="t_001"):
    return {"company": "富瑞特装", "metric": metric, "metric_std": metric, "period": period,
            "value": value, "raw": f"{value:,}", "is_pct": False, "is_subtotal": False,
            "unit": unit, "confidence": 0.9,
            "source": {"page_idx": page, "table_id": table}}


def _get(metrics, label, period="FY2024"):
    return next(m for m in metrics if m["label"] == label and m.get("period") == period)


def test_growth_same_unit():
    ms = compute_derived_metrics([
        _f("营业收入", "FY2024", 110.0), _f("营业收入", "FY2023", 100.0),
    ])
    m = _get(ms, "营业收入增长率")
    assert m["status"] == "ok"
    assert m["value"] == 0.1 and m["display"] == "+10.00%"
    assert len(m["sources"]) == 2 and m["sources"][0]["page_idx"] == 8


def test_growth_cross_unit_folded_to_yuan():
    """万元 vs 亿元 自动折元后可比"""
    ms = compute_derived_metrics([
        _f("营业收入", "FY2024", 110.0, unit="万元"), _f("营业收入", "FY2023", 0.01, unit="亿元"),
    ])
    m = _get(ms, "营业收入增长率")
    assert m["status"] == "ok" and abs(m["value"] - 0.1) < 1e-9


def test_growth_missing_prev_and_zero_denominator():
    ms = compute_derived_metrics([
        _f("营业收入", "FY2024", 110.0),                        # 无 FY2023
        _f("净利润", "FY2024", 5.0), _f("净利润", "FY2023", 0.0),  # 上年为零
    ])
    assert _get(ms, "营业收入增长率")["status"] == "skipped_missing_inputs"
    assert _get(ms, "净利润增长率")["status"] == "skipped_zero_denominator"


def test_growth_unit_incomparable_skipped():
    """单位未知（空）vs 元 → 不可比，跳过不硬算"""
    ms = compute_derived_metrics([
        _f("营业收入", "FY2024", 110.0, unit=""), _f("营业收入", "FY2023", 100.0, unit="元"),
    ])
    assert _get(ms, "营业收入增长率")["status"] == "skipped_unit_unknown"


def test_growth_direct_exists_not_recomputed():
    """年报已直接披露增长率（pct fact）→ 不重复计算（防双源数字打架）"""
    direct = _f("营业收入增长率", "FY2024", 4.75)
    direct["is_pct"] = True
    ms = compute_derived_metrics([
        _f("营业收入", "FY2024", 110.0), _f("营业收入", "FY2023", 100.0), direct,
    ])
    assert _get(ms, "营业收入增长率")["status"] == "skipped_direct_exists"


def test_ratios_profitability_and_solvency():
    ms = compute_derived_metrics([
        _f("营业收入", "FY2024", 110.0),
        _f("归母净利润", "FY2024", 22.0),
        _f("负债合计", "FY2024", 60.0), _f("资产总计", "FY2024", 100.0),
        _f("流动资产合计", "FY2024", 70.0), _f("流动负债合计", "FY2024", 50.0),
        _f("存货", "FY2024", 20.0),
    ])
    assert _get(ms, "净利率")["display"] == "20.00%"      # 22/110
    assert _get(ms, "资产负债率")["display"] == "60.00%"  # 60/100
    assert _get(ms, "流动比率")["display"] == "1.40"      # 70/50
    assert _get(ms, "速动比率")["display"] == "1.00"      # (70-20)/50
    assert all(m["status"] == "ok" for m in ms if m["label"] in ("净利率", "资产负债率", "流动比率", "速动比率"))


def test_pct_and_low_confidence_facts_excluded_as_inputs():
    """is_pct / confidence<0.9 事实不进算术索引（防止拿比率当绝对值算）"""
    pct = _f("营业收入", "FY2024", 110.0)
    pct["is_pct"] = True
    low = _f("营业收入", "FY2023", 100.0)
    low["confidence"] = 0.6
    ms = compute_derived_metrics([pct, low])
    assert _get(ms, "营业收入增长率")["status"] == "skipped_missing_inputs"


def test_render_only_ok_items_as_table():
    ms = compute_derived_metrics([
        _f("营业收入", "FY2024", 110.0), _f("营业收入", "FY2023", 100.0),
    ])
    text = render_derived_metrics(ms)
    assert "营业收入增长率" in text and "+10.00%" in text and "p8" in text
    assert render_derived_metrics([m for m in ms if m["status"] != "ok"]) == ""
```

- [ ] **Step 3.2: 运行确认失败**

Run: `pytest tests/test_e3_derived_metrics.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3.3: 实现**

```python
"""
FinScope 确定性算子层（P5-E3，纯 Python 零 LLM）

LLM 心算是数字错误的结构性来源（FinRobot 教训）——增长率/利润率/偿债比率
全部由本模块确定性计算，LLM 只消费结果（display 直接引用，禁止改写）。

设计:
- 输入: L1 facts 表；索引 (company, metric_std, period) -> fact
- 量纲: 单位折算复用 B7 identity_checker._UNIT_SCALE 口径（单一事实源）；
  多输入单位一致（含同为空）或均可折元才可比，否则 skip 不硬算
- 产物: {label, period, value, display, formula, sources, status}
  status ∈ ok / skipped_missing_inputs / skipped_unit_unknown /
           skipped_zero_denominator / skipped_direct_exists
- 增长率 skipped 项保留（相邻年度缺口是数据质量信号）；比率输入不齐静默跳过（防噪声）
- render_derived_metrics 只渲染 ok 项（Writer/Analyst/Synthesizer 注入用）
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from extractors.identity_checker import _UNIT_SCALE

_FY_RE = re.compile(r"^FY(\d{4})$")

# 增长率: (展示名, 源科目)
_GROWTH_DEFS = (
    ("营业收入增长率", "营业收入"),
    ("归母净利润增长率", "归母净利润"),
    ("净利润增长率", "净利润"),
    ("总资产增长率", "资产总计"),
)

# 比率: (展示名, 分子候选, 分母候选, 格式 pct|x, 额外扣减科目候选或 None)
#   候选列表吸收 A股 CAS 与港股 IFRS 口径差异，不动 B6 别名表
_RATIO_DEFS = (
    ("净利率", ("归母净利润", "净利润"), ("营业收入", "营业总收入"), "pct", None),
    ("资产负债率", ("负债合计", "总负债", "總負債"), ("资产总计", "总资产", "總資產"), "pct", None),
    ("流动比率", ("流动资产合计", "流動資產合計"), ("流动负债合计", "流動負債合計"), "x", None),
    ("速动比率", ("流动资产合计", "流動資產合計"), ("流动负债合计", "流動負債合計"), "x", ("存货", "存貨")),
)


def _key(company: str, metric: str, period: str) -> Tuple[str, str, str]:
    return (company or "", metric, period or "")


def _pick(idx, company: str, names, period: str) -> Optional[Dict[str, Any]]:
    for n in names:
        f = idx.get(_key(company, n, period))
        if f is not None:
            return f
    return None


def _same_scale(*fs):
    """多条事实折算到同一量纲（优先元）。全部单位一致（含同为空）或全部可折元 → 数值元组；否则 None。"""
    if any(f.get("value") is None for f in fs):
        return None
    us = {(f.get("unit") or "").strip() for f in fs}
    if len(us) == 1:
        u = next(iter(us))
        scale = _UNIT_SCALE.get(u, 1.0)  # 同一未知单位: 内部自洽，按原值比较
        return tuple(float(f["value"]) * scale for f in fs)
    if all(u in _UNIT_SCALE for u in us):
        return tuple(float(f["value"]) * _UNIT_SCALE[(f.get("unit") or "").strip()] for f in fs)
    return None


def _src_ref(f: Dict[str, Any]) -> Dict[str, Any]:
    src = f.get("source") or {}
    return {"metric": f.get("metric_std") or f.get("metric"), "period": f.get("period"),
            "raw": f.get("raw"), "page_idx": src.get("page_idx"), "table_id": src.get("table_id")}


def _mk(label, period, value, display, formula, sources, status="ok"):
    return {"label": label, "period": period, "value": value, "display": display,
            "formula": formula, "sources": sources, "status": status}


def compute_derived_metrics(facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """facts 表 -> 派生指标列表（含 skipped 项，render 只取 ok 项）"""
    # 算术索引: 高置信非 pct 事实；直接名集合: 全量（含 pct，直存检测用）
    idx: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    direct_names: set = set()
    for f in facts or []:
        k = _key(f.get("company") or "", f.get("metric_std") or f.get("metric") or "", f.get("period") or "")
        direct_names.add(k)
        if f.get("confidence", 0) < 0.9 or f.get("is_pct") or f.get("value") is None:
            continue
        idx.setdefault(k, f)

    out: List[Dict[str, Any]] = []

    # 1) 增长率（同科目相邻 FY）
    for (company, metric, period), f in idx.items():
        m = _FY_RE.match(period or "")
        if not m:
            continue
        for label, src_metric in _GROWTH_DEFS:
            if metric != src_metric:
                continue
            year = int(m.group(1))
            if _key(company, label, period) in direct_names:
                out.append(_mk(label, period, None, "", "（报告已直接披露，不重复计算）",
                               [_src_ref(f)], status="skipped_direct_exists"))
                continue
            prev_f = idx.get(_key(company, metric, f"FY{year - 1}"))
            pair = _same_scale(f, prev_f) if prev_f is not None else None
            if prev_f is None:
                out.append(_mk(label, period, None, "", f"{metric} 相邻年度缺失",
                               [_src_ref(f)], status="skipped_missing_inputs"))
            elif pair is None:
                out.append(_mk(label, period, None, "",
                               f"单位不可比（{f.get('unit')!r} vs {prev_f.get('unit')!r}）",
                               [_src_ref(f), _src_ref(prev_f)], status="skipped_unit_unknown"))
            elif abs(pair[1]) < 1e-9:
                out.append(_mk(label, period, None, "", f"{metric} FY{year - 1} 为零",
                               [_src_ref(f), _src_ref(prev_f)], status="skipped_zero_denominator"))
            else:
                g = (pair[0] - pair[1]) / abs(pair[1])
                out.append(_mk(label, period, g, f"{g * 100:+.2f}%",
                               f"{metric} {period} / {metric} FY{year - 1} - 1",
                               [_src_ref(f), _src_ref(prev_f)]))

    # 2) 比率（净利率/资产负债率/流动·速动比率）
    for company, period in sorted({(c, p) for (c, _m, p) in idx.keys()}):
        for label, num_names, den_names, fmt, sub_names in _RATIO_DEFS:
            if _key(company, label, period) in direct_names:
                continue  # 报告已直接披露，不重复计算
            den = _pick(idx, company, den_names, period)
            num = _pick(idx, company, num_names, period)
            sub = _pick(idx, company, sub_names, period) if sub_names else None
            if num is None or den is None or (sub_names and sub is None):
                continue  # 输入不齐静默跳过（防 skipped 噪声刷屏）
            vals = _same_scale(*( [num, sub, den] if sub is not None else [num, den] ))
            if vals is None:
                continue
            n, d = (vals[0] - vals[1], vals[2]) if sub is not None else (vals[0], vals[1])
            if abs(d) < 1e-9:
                continue
            v = n / d
            formula = (f"({num.get('metric_std') or num.get('metric')} - "
                       f"{sub.get('metric_std') or sub.get('metric')}) / "
                       f"{den.get('metric_std') or den.get('metric')}" if sub is not None else
                       f"{num.get('metric_std') or num.get('metric')} / "
                       f"{den.get('metric_std') or den.get('metric')}")
            srcs = [_src_ref(x) for x in ([num, sub, den] if sub is not None else [num, den])]
            out.append(_mk(label, period, v, f"{v * 100:.2f}%" if fmt == "pct" else f"{v:.2f}",
                           formula, srcs))

    return out


def render_derived_metrics(metrics: List[Dict[str, Any]]) -> str:
    """派生指标表（Markdown，只渲染 ok 项；无 ok 项返回空串）"""
    ok = [m for m in metrics or [] if m.get("status") == "ok"]
    if not ok:
        return ""
    lines = ["| 指标 | 期间 | 数值 | 公式 | 来源 |", "|---|---|---|---|---|"]
    for m in ok:
        src = "; ".join(
            f"p{s.get('page_idx', '?')}" + (f" {s.get('table_id')}" if s.get("table_id") else "")
            for s in m.get("sources") or []
        )
        lines.append(f"| {m['label']} | {m['period']} | {m['display']} | {m['formula']} | {src} |")
    return "\n".join(lines)
```

- [ ] **Step 3.4: 运行确认通过 + 全量回归**

Run: `pytest tests/test_e3_derived_metrics.py -v && pytest`
Expected: 新增 8 条 PASS，全量 152+8=160。

- [ ] **Step 3.5: Commit**

```bash
git add src/extractors/derived_metrics.py tests/test_e3_derived_metrics.py
git commit -m "feat(p5): E3 确定性算子层——增长率/净利率/偿债比率纯 Python 计算，LLM 零心算"
```

---

### Task 4: E5 跨源对账 `cross_checker.py`

**Files:**
- Create: `src/extractors/cross_checker.py`
- Test: `tests/test_e5_cross_checker.py`

- [ ] **Step 4.1: 写失败测试**

```python
"""P5-E5 测试: facts 表 ↔ MD&A 散文数字跨源对账（确定性，零 LLM）"""

from extractors.cross_checker import cross_check_prose_vs_facts, render_cross_warnings


def _tf(metric, period, value, unit="元", page=8):
    return {"company": "富瑞特装", "metric": metric, "metric_std": metric, "period": period,
            "value": value, "raw": f"{value:,}", "is_pct": False, "is_subtotal": False,
            "unit": unit, "confidence": 0.9,
            "source": {"page_idx": page, "table_id": "t_001"}}


def _l1(prose, facts):
    return {
        "sections": [{"section_id": "s_100", "title": "管理层讨论与分析",
                      "tier": "T1", "text": prose, "page_range": [45], "table_ids": []}],
        "tables": [], "facts": facts,
    }


CMAP = {"s_100": 3}  # 第三节 MD&A


def test_consistent_prose_vs_table():
    l1 = _l1("报告期内，公司实现营业收入 33.22 亿元，同比增长 4.76%。",
             [_tf("营业收入", "FY2024", 3.322e9)])
    checks = cross_check_prose_vs_facts(l1, CMAP)
    c = next(x for x in checks if x["metric"] == "营业收入")
    assert c["status"] == "consistent" and c["n_prose_hits"] == 1


def test_match_any_period_avoids_false_mismatch():
    """散文提到上年数字（FY2023）→ 与任一期间命中即 consistent"""
    l1 = _l1("2023年公司营业收入 31.71 亿元，2024年增长至 33.22 亿元。",
             [_tf("营业收入", "FY2024", 3.322e9), _tf("营业收入", "FY2023", 3.171e9)])
    c = next(x for x in cross_check_prose_vs_facts(l1, CMAP) if x["metric"] == "营业收入")
    assert c["status"] == "consistent" and c["n_prose_hits"] == 2


def test_mismatch_reported_with_detail():
    l1 = _l1("报告期内，公司实现营业收入 30.50 亿元。",
             [_tf("营业收入", "FY2024", 3.322e9)])
    c = next(x for x in cross_check_prose_vs_facts(l1, CMAP) if x["metric"] == "营业收入")
    assert c["status"] == "mismatch"
    mm = c["mismatches"][0]
    assert "30.50亿元" in mm["prose_value"] and mm["prose_src"].startswith("p45")
    assert mm["rel_diff"] > 0.02


def test_no_prose_and_non_mdna_sections_ignored():
    l1 = {
        "sections": [
            {"section_id": "s_100", "title": "管理层讨论与分析", "tier": "T1",
             "text": "经营平稳。", "page_range": [45], "table_ids": []},
            {"section_id": "s_200", "title": "公司简介", "tier": "T1",
             "text": "营业收入 999 亿元。", "page_range": [5], "table_ids": []},  # 非第三节，不扫
        ],
        "tables": [], "facts": [_tf("营业收入", "FY2024", 3.322e9), _tf("资产总计", "FY2024", 1e10)],
    }
    checks = cross_check_prose_vs_facts(l1, {"s_100": 3, "s_200": 1})
    rev = {c["metric"]: c for c in checks}
    assert rev["营业收入"]["status"] == "no_prose"      # MD&A 内无散文数字
    assert rev["资产总计"]["status"] == "no_prose"


def test_pct_and_text_facts_not_compared():
    """pct 事实 / 非表格来源事实（文本级 0.6）不参与表格侧对账"""
    pct = _tf("营业收入", "FY2024", 33.22)
    pct["is_pct"] = True
    txt = _tf("营业收入", "FY2024", 3.322e9)
    txt["confidence"] = 0.6
    txt["source"] = {"page_idx": 8}
    l1 = _l1("公司实现营业收入 33.22 亿元。", [pct, txt])
    assert cross_check_prose_vs_facts(l1, CMAP) == []  # 无合格表格事实 → 不产出该科目检查


def test_render_cross_warnings_only_mismatch():
    checks = [
        {"metric": "营业收入", "status": "consistent", "mismatches": []},
        {"metric": "归母净利润", "status": "mismatch", "mismatches": [
            {"prose_value": "2.5亿元", "prose_src": "p45（s_100）",
             "table_values": ["219,215,799.89"], "rel_diff": 0.14}]},
    ]
    text = render_cross_warnings(checks)
    assert "跨源核对告警" in text and "归母净利润" in text and "2.5亿元" in text
    assert render_cross_warnings(checks[:1]) == ""
```

- [ ] **Step 4.2: 运行确认失败**

Run: `pytest tests/test_e5_cross_checker.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 4.3: 实现**

```python
"""
FinScope 跨源交叉核对（P5-E5，确定性对账 job，零 LLM）

facts 表非「确定正确」（三类不准确: 实现 bug / 覆盖缺口 / 上游 MinerU 噪声）。
B7 只校 facts 内部勾稽；本模块补最后一道防线: MD&A 散文数字 ↔ facts 表数字
（两个独立来源）的一致性核对——领域架构下近乎免费。

输出（聚合到指标粒度）:
{metric, status: consistent|mismatch|no_prose, n_prose_hits, n_consistent,
 n_table_facts, mismatches: [{prose_value, prose_src, table_values, rel_diff}]}
mismatches 仅作告警（散文常见整数亿舍入），Reviewer 消费后裁决，不硬失败。
"""

import re
from typing import Any, Dict, List

# 散文科目短语（只查头部指标；「净利润」不单列——避免「归母净利润」子串误命中）
_PROSE_METRICS = {
    "营业收入": "营业收入|营业总收入|营收|總收益",
    "归母净利润": "归母净利润|归属于上市公司股东的净利润|归属于母公司股东的净利润",
    "资产总计": "资产总计|总资产|资产总额|總資產",
}

_NUM_UNIT_RE = r"([0-9][0-9,，.]*\.?[0-9]*)\s*(亿元|億元|百万元|百萬元|万元|萬元|千元|元)"
_PROSE_UNIT_SCALE = {"元": 1.0, "千元": 1e3, "万元": 1e4, "萬元": 1e4,
                     "百万元": 1e6, "百萬元": 1e6, "亿元": 1e8, "億元": 1e8}

_MDNA_CHAPTER = 3  # 第三节 管理层讨论与分析


def _parse_prose_number(num: str, unit: str) -> float:
    return float(num.replace(",", "").replace("，", "")) * _PROSE_UNIT_SCALE[unit]


def _fact_to_yuan(f: Dict[str, Any]):
    from extractors.identity_checker import _UNIT_SCALE
    u = (f.get("unit") or "").strip()
    if u not in _UNIT_SCALE or f.get("value") is None:
        return None
    return float(f["value"]) * _UNIT_SCALE[u]


def cross_check_prose_vs_facts(
    l1: Dict[str, Any], chapter_map: Dict[str, int], tol_rel: float = 0.02
) -> List[Dict[str, Any]]:
    """MD&A 散文数字 vs 表格 facts（confidence≥0.9、非 pct、表格来源）。

    匹配口径: 散文数字与该科目**任一期间**的表格值相对偏差 ≤ tol_rel 即 consistent
    （散文常提及上年对比值）。散文四舍五入常见，tol=2% 是告警线不是硬错。
    """
    sections = l1.get("sections") or []
    mdna = [(s.get("section_id", "?"), (s.get("page_range") or [0])[0], s.get("text") or "")
            for s in sections if chapter_map.get(s.get("section_id", "?"), 0) == _MDNA_CHAPTER]

    table_facts = [f for f in (l1.get("facts") or [])
                   if f.get("confidence", 0) >= 0.9 and not f.get("is_pct")
                   and (f.get("source") or {}).get("table_id")]

    checks: List[Dict[str, Any]] = []
    for metric_std, phrase in _PROSE_METRICS.items():
        facts_m = [f for f in table_facts if (f.get("metric_std") or f.get("metric")) == metric_std]
        if not facts_m:
            continue
        known = [(y, f) for y, f in ((_fact_to_yuan(f), f) for f in facts_m) if y is not None]
        pattern = re.compile(phrase + r"\s*(?:为|约|达|是|人民币)?\s*" + _NUM_UNIT_RE)

        hits: List[Any] = []
        for sid, page, text in mdna:
            for m in pattern.finditer(text):
                try:
                    y = _parse_prose_number(m.group(1), m.group(2))
                except ValueError:
                    continue
                hits.append((y, f"{m.group(1)}{m.group(2)}", f"p{page}（{sid}）"))

        if not hits:
            checks.append({"metric": metric_std, "status": "no_prose", "n_prose_hits": 0,
                           "n_consistent": 0, "n_table_facts": len(facts_m), "mismatches": []})
            continue

        mismatches, n_consistent = [], 0
        for y, raw, src in hits:
            best = min((abs(y - ty) / max(abs(ty), 1e-9) for ty, _ in known), default=None)
            if best is None:
                mismatches.append({"prose_value": raw, "prose_src": src,
                                   "table_values": [f.get("raw") for f in facts_m],
                                   "rel_diff": None, "reason": "table_unit_unknown"})
            elif best > tol_rel:
                mismatches.append({"prose_value": raw, "prose_src": src,
                                   "table_values": [f.get("raw") for f in facts_m],
                                   "rel_diff": round(best, 4)})
            else:
                n_consistent += 1
        checks.append({"metric": metric_std,
                       "status": "mismatch" if mismatches else "consistent",
                       "n_prose_hits": len(hits), "n_consistent": n_consistent,
                       "n_table_facts": len(facts_m), "mismatches": mismatches})
    return checks


def render_cross_warnings(checks: List[Dict[str, Any]]) -> str:
    """跨源告警渲染（Writer/Reviewer 注入用）；无 mismatch 返回空串"""
    mismatches = [c for c in checks or [] if c.get("status") == "mismatch"]
    if not mismatches:
        return ""
    lines = ["## ⚠ 跨源核对告警（facts 表 ↔ MD&A 散文，确定性对账发现不一致）"]
    for c in mismatches:
        for m in c.get("mismatches") or []:
            diff = f"，相对偏差 {m['rel_diff']:.1%}" if m.get("rel_diff") is not None else ""
            lines.append(f"- {c['metric']}: 散文 {m['prose_value']}（{m['prose_src']}）"
                         f" vs 表格 {m.get('table_values')}{diff}")
    lines.append("引用该科目数字以 facts 表（query_fact 可溯源）为准；散文值需 fetch_context 核对原文后取舍。")
    return "\n".join(lines)
```

- [ ] **Step 4.4: 运行确认通过 + 全量回归**

Run: `pytest tests/test_e5_cross_checker.py -v && pytest`
Expected: 新增 6 条 PASS，全量 160+6=166。

- [ ] **Step 4.5: Commit**

```bash
git add src/extractors/cross_checker.py tests/test_e5_cross_checker.py
git commit -m "feat(p5): E5 facts↔MD&A 散文跨源对账——独立双源一致性告警"
```

---

### Task 5: E1 ContextPreparator 独立节点（集成 Task 0/2/3/4）

**Files:**
- Modify: `src/graphs/state.py`（新增 6 个 P5 字段 + `create_initial_state` 默认值）
- Modify: `src/utils/config.py`（P5 配置节）
- Create: `src/agents/context_preparator.py`
- Modify: `src/graphs/financial_graph.py`（注册节点 + 确定性边）
- Modify: `src/agents/financial_analyst.py`（去装配化: `_multilevel_analyst_run` 改读 state 的 `pdf_context`，删 L2/L3 惰性构建与装配；E2 领域门控在 Task 6 接入）
- Modify: `src/agents/report_writer.py`（同款去装配化: `_multilevel_writer_run` 优先读 state 的 `pdf_context`，现场装配降级为兜底）
- Rewrite: `tests/test_a3_a4.py::test_a3_revision_round_no_recompression`（L2 构建职责移位）
- Test: `tests/test_e1_context_preparator.py`

- [ ] **Step 5.1: 写失败测试**

```python
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
    for i in range(30):  # 凑足 L3 构建门槛（sections >= 30），全部归入第十节
        items.append({"type": "heading", "content": "第十节 财务报告附注", "level": 2})
        items.append({"type": "text", "content": f"附注{i}说明。"})
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
    g = next(m for m in out["derived_metrics"] if m["label"] == "营业收入增长率")
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
```

- [ ] **Step 5.2: 运行确认失败**

Run: `pytest tests/test_e1_context_preparator.py -v`
Expected: FAIL（`ModuleNotFoundError: agents.context_preparator`、state 字段 KeyError、图边缺失）

- [ ] **Step 5.3: state.py 加字段**

`FinancialAnalysisState` 末尾（`compliance_violations` 之后）加:

```python
    # ========== [P5] 上下文准备 + 领域架构 ==========
    chapter_map: Dict[str, int]             # E1: section_id -> 十节章节号（0 前置/99 尾注）
    domain_contexts: Dict[str, str]         # E1/E2: 领域 -> 预算装配上下文（覆盖率不足/flag关为空 dict）
    derived_metrics: List[Dict[str, Any]]   # E3: 确定性算子层产物
    cross_source_checks: List[Dict[str, Any]]  # E5: facts↔MD&A 散文对账结果
    domain_analyses: Dict[str, str]         # E2: 领域 -> 领域 agent 产出（修订精准回炉缓存）
    defect_domain: str                      # E2: Reviewer 判定的缺陷领域（'' 未定位）
```

`create_initial_state` 返回字典末尾（`compliance_violations=[]` 之后）加:

```python
        chapter_map={},
        domain_contexts={},
        derived_metrics=[],
        cross_source_checks=[],
        domain_analyses={},
        defect_domain="",
```

- [ ] **Step 5.4: config.py 加 P5 配置节**

`L2_MIN_TEXT_TOKENS` 字段之后加:

```python
    # ---- P5 架构演进 ----
    USE_DOMAIN_AGENTS: bool = Field(
        default=True,
        description="E2: 5 领域 agent 替代单 Analyst（关闭或十节覆盖率不足时回退全局装配路径）",
    )
    L2_EAGER_MAX_NEW: int = Field(
        default=30, ge=1, le=100,
        description="E1: ContextPreparator 单次 L2 急切构建上限（替代 Analyst 惰性 8/次）",
    )
    DOMAIN_CHAPTER_COVERAGE_MIN: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="E2: 进入领域模式的最小十节覆盖率（非 T3 text token 占比，非标模板自动回退）",
    )
    DOMAIN_MAX_PARALLEL_AGENTS: int = Field(
        default=3, ge=1, le=5,
        description="E2: 领域 agent 并行度（ThreadPoolExecutor workers）",
    )
```

同 Step 删除 `MAX_L2_BUILD_PER_RUN` 字段（约 L94-97）——唯一调用方是 Analyst 惰性构建（本 Task Step 5.7 移除），全仓已核实无其他引用。

- [ ] **Step 5.5: 实现 `context_preparator.py`**

```python
"""
FinScope ContextPreparator（P5-E1，确定性编排 + LLM 受限叶子）

一次构建、全链复用（Analyst/Writer/Reviewer 零装配零重复构建）:
1. 十节标签 + 覆盖率（E2 门控输入，纯规则）
2. L2 急切构建（一次性 max_new=L2_EAGER_MAX_NEW，替代 Analyst 惰性 8/次；LLM 失败规则兜底）
3. L3 全局亮点（E6 配额版，构建一次跨轮复用）
4. E3 派生指标（确定性算子）
5. E5 跨源对账（facts ↔ MD&A 散文）
6. 装配: 全局 pdf_context（Writer/Reviewer/回退路径）+ 领域 domain_contexts（E2）

图接入: 确定性边 report_extractor → context_preparator → supervisor，
不经 Supervisor LLM 路由（确定性节点不该消耗 LLM 配额，也不该被误路由跳过）。
"""

import logging
from datetime import datetime
from typing import Any, Dict

from graphs.state import FinancialAnalysisState
from utils.config import get_settings

logger = logging.getLogger(__name__)

_L3_MIN_SECTIONS = 30  # 与旧 Analyst 惰性构建同门槛（小文档无 L3 必要）


def context_preparator_node(state: FinancialAnalysisState) -> Dict[str, Any]:
    agent_status = dict(state.get("agent_status", {}))
    error_log = list(state.get("error_log", []))
    l1 = state.get("pdf_l1") or {}
    sections = list(l1.get("sections") or [])
    settings = get_settings()

    if not sections:
        agent_status["context_preparator"] = "done"
        return {"agent_status": agent_status, "error_log": error_log}

    # 1) 十节标签 + 覆盖率（纯规则，E2 门控输入）
    from extractors.chapter_tagger import DOMAINS, sections_for_domain, tag_chapters, chapter_token_coverage
    chapter_map = tag_chapters(sections)
    coverage = chapter_token_coverage(sections, chapter_map)

    # 2) L2 急切构建（一次性 max_new=L2_EAGER_MAX_NEW；已有缓存全命中跳过）
    from extractors.section_compressor import build_global_summary_lite, compress_document_l2
    pdf_l2 = dict(state.get("pdf_l2") or {})
    try:
        compress_document_l2(
            sections, pdf_l2,
            min_text_tokens=settings.L2_MIN_TEXT_TOKENS,
            max_new=settings.L2_EAGER_MAX_NEW,
            use_llm=True,  # LLM 不可用时内部规则兜底（确定性）
        )
    except Exception as e:
        logger.warning("[ContextPreparator] L2 构建失败（不阻断主流程）: %s", str(e)[:120])

    # 3) L3 全局亮点（E6 配额版，构建一次跨轮复用）
    pdf_l3 = dict(state.get("pdf_l3") or {})
    if not pdf_l3.get("text") and len(sections) >= _L3_MIN_SECTIONS:
        company = next(
            (str(e.get("entity_name", "")) for e in state.get("extracted_entities", [])
             if e.get("entity_type") == "company"),
            "",
        )
        try:
            pdf_l3 = {
                "text": build_global_summary_lite(l1, company=company),
                "source": "l3_lite_rules",
                "built_at": datetime.now().isoformat(timespec="seconds"),
            }
        except Exception as e:
            logger.warning("[ContextPreparator] L3 构建失败（跳过）: %s", str(e)[:120])

    # 4) E3 派生指标（确定性算子，零 LLM）
    from extractors.derived_metrics import compute_derived_metrics
    derived_metrics = compute_derived_metrics(l1.get("facts") or [])

    # 5) E5 跨源对账（facts ↔ MD&A 散文，零 LLM）
    from extractors.cross_checker import cross_check_prose_vs_facts
    cross_source_checks = cross_check_prose_vs_facts(l1, chapter_map)

    # 6) 装配: 全局 pdf_context + 领域 domain_contexts（十节覆盖达标且 flag 开才做领域装配）
    from extractors.context_assembler import assemble
    pdf_context = assemble(
        state.get("user_query", ""), settings.CONTEXT_BUDGET_TOKENS, l1,
        l2=pdf_l2 or None, l3=pdf_l3.get("text") or None,
    )["context"]

    domain_contexts: Dict[str, str] = {}
    if settings.USE_DOMAIN_AGENTS and coverage >= settings.DOMAIN_CHAPTER_COVERAGE_MIN:
        for d in DOMAINS:
            sub = sections_for_domain(sections, chapter_map, d["key"])
            if not sub:
                continue
            domain_contexts[d["key"]] = assemble(
                state.get("user_query", ""), settings.CONTEXT_BUDGET_TOKENS,
                {"sections": sub, "tables": l1.get("tables"), "facts": l1.get("facts")},
                l2=pdf_l2 or None, l3=pdf_l3.get("text") or None,
            )["context"]

    agent_status["context_preparator"] = "done"
    logger.info(
        "[ContextPreparator] 完成: 十节覆盖 %.1f%%, L2 %d 条, L3 %s, 派生指标 %d, 领域 %d/%d",
        coverage * 100, len(pdf_l2), "有" if pdf_l3.get("text") else "无",
        len(derived_metrics), len(domain_contexts), len(DOMAINS),
    )
    return {
        "chapter_map": chapter_map,
        "pdf_l2": pdf_l2,
        "pdf_l3": pdf_l3,
        "derived_metrics": derived_metrics,
        "cross_source_checks": cross_source_checks,
        "pdf_context": pdf_context,
        "domain_contexts": domain_contexts,
        "agent_status": agent_status,
        "error_log": error_log,
    }
```

- [ ] **Step 5.6: 图接线（`financial_graph.py` 确定性边）**

三处修改:

① 导入区（`from agents.financial_analyst import financial_analyst_node` 之后）加:

```python
from agents.context_preparator import context_preparator_node
```

② `_build_graph` 注册节点（`workflow.add_node("reviewer", reviewer_node)` 之后）加:

```python
        workflow.add_node("context_preparator", context_preparator_node)
```

③ 子 Agent → Supervisor 边区段，**替换** `workflow.add_edge("report_extractor", "supervisor")` 为:

```python
        # [P5-E1] 确定性边: report_extractor → context_preparator → supervisor
        # （上下文准备是确定性节点，不经 Supervisor LLM 路由，不消耗其配额）
        workflow.add_edge("report_extractor", "context_preparator")
        workflow.add_edge("context_preparator", "supervisor")
```

注意: `context_preparator` **不进** `_route_from_supervisor` 的 valid_nodes（它只由确定性边触达，Supervisor 永不路由到它）。

- [ ] **Step 5.7: Analyst / Writer 去装配化（读 state 的 `pdf_context`）**

**`src/agents/financial_analyst.py`**:

① 模块 docstring 第 14 行「L2/L3 惰性构建并写回 state（跨 agent / 跨修订轮复用）」改为:
「[P5-E1] 上下文由 context_preparator 预装配（L2/L3 构建职责已移位），本节点只消费」。

② `_multilevel_analyst_run` 头部（docstring + L2/L3 惰性构建块 + 装配块，约 L104-162 整段）替换为:

```python
    """
    [P5-E1 起分析节点] 消费 ContextPreparator 预装配的 pdf_context + 有界工具循环。

    装配/L2/L3 构建职责已移位至 context_preparator（一次构建全链复用）；
    本函数只保留兜底: state.pdf_context 为空（Preparator 未跑/失败，如单测直接调用）
    时现场装配一次（用 state 已有的 l2/l3 缓存，不新增 LLM 构建，零副作用）。
    """
    settings = get_settings()
    l1 = state.get("pdf_l1") or {}

    from agents.context_tools import build_context_tools

    # [P5-E1] 优先消费预装配产物；空则兜底装配（不构建 L2/L3）
    pdf_context = state.get("pdf_context", "")
    if not pdf_context:
        try:
            from extractors.context_assembler import assemble
            pdf_l2 = state.get("pdf_l2") or {}
            pdf_l3 = state.get("pdf_l3") or {}
            pdf_context = assemble(
                state.get("user_query", ""), settings.CONTEXT_BUDGET_TOKENS, l1,
                l2=pdf_l2 or None, l3=(pdf_l3.get("text") or "") or None,
            )["context"]
        except Exception as e:
            error_log.append(f"[FinancialAnalyst] 兜底装配失败，回退旧路径: {str(e)[:150]}")
            return None  # 上层回退旧路径
```

（函数其余部分——组装上下文/prompt/工具循环——不变。）

③ 函数全部 4 个 return dict **删去** `"pdf_l2"` / `"pdf_l3"` 两键（缓存已在 state，由 Preparator 写回，Analyst 不再搬运）。

**`src/agents/report_writer.py`**（`_multilevel_writer_run` 的装配块，约 L271-283）替换为:

```python
    # 1) [P5-E1] 优先消费 ContextPreparator 预装配产物；空则兜底装配（不建 L2/L3）
    pdf_context = state.get("pdf_context", "")
    if not pdf_context:
        try:
            assembled = assemble(
                state.get("user_query", ""), settings.CONTEXT_BUDGET_TOKENS, l1,
                l2=(state.get("pdf_l2") or {}) or None,
                l3=((state.get("pdf_l3") or {}).get("text") or "") or None,
            )
            pdf_context = assembled["context"]
        except Exception as e:
            error_log.append(f"[ReportWriter] 兜底装配失败，回退旧路径: {str(e)[:150]}")
            return None
```

- [ ] **Step 5.8: 改写 `tests/test_a3_a4.py::test_a3_revision_round_no_recompression`**

（`test_a3_l2_second_build_zero_new` 与 A4 三条不变；只重写链路级这条——L2 构建职责移位后，断言对象从 Analyst 改为 Preparator + Analyst 协作）:

```python
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
```

- [ ] **Step 5.9: 运行确认通过 + 全量回归**

Run: `pytest tests/test_e1_context_preparator.py tests/test_a3_a4.py tests/test_p2_context.py tests/test_a1_writer.py -v && pytest`
Expected: E1 新增 6 条 PASS；test_a3 重写后 PASS；直接调用 Analyst/Writer 的既有测试（p2/a1，走兜底装配路径）不回归；全量绿（预估 ~172，以实际为准）。

- [ ] **Step 5.10: Commit**

```bash
git add src/agents/context_preparator.py src/agents/financial_analyst.py src/agents/report_writer.py src/graphs/state.py src/graphs/financial_graph.py src/utils/config.py tests/test_e1_context_preparator.py tests/test_a3_a4.py
git commit -m "feat(p5): E1 ContextPreparator 独立节点——一次构建全链复用，Analyst/Writer 去装配化"
```

---

### Task 6: E2 领域 agent 组 + Synthesizer + Analyst 门控

**Files:**
- Create: `src/agents/domain_analysts.py`
- Modify: `src/agents/financial_analyst.py`（`financial_analyst_node` 加领域模式门控）
- Test: `tests/test_e2_domain_agents.py`

- [ ] **Step 6.1: 写失败测试**

```python
"""P5-E2 测试: 领域 agent 组 + Synthesizer + Analyst 门控"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.state import create_initial_state


def _state_with_domains():
    st = create_initial_state("分析富瑞特装", pdf_path="")
    st["pdf_l1"] = {
        "sections": [{"section_id": "s_001", "title": "第三节 管理层讨论与分析",
                      "tier": "T1", "text": "经营分析。", "page_range": [10], "table_ids": []}],
        "tables": [], "facts": [],
    }
    st["domain_contexts"] = {
        "overview": "## 概览上下文\n公司主营特种装备…",
        "operating": "## 经营上下文\nMD&A 原文…",
        "financial": "## 财务上下文\n利润表…",
    }
    st["extracted_entities"] = [{"entity_type": "company", "entity_name": "富瑞特装"}]
    return st


def _patch_llm(monkeypatch, responses=None):
    """统一 mock domain_analysts 的 LLM 面（is_llm_ready / safe_invoke_with_tools / safe_invoke）"""
    import agents.domain_analysts as da
    from extractors.chapter_tagger import DOMAINS
    name2key = {d["name"]: d["key"] for d in DOMAINS}
    calls = []

    def fake_with_tools(system_prompt, user_msg, tools, max_rounds=5, on_tool_call=None):
        key = next((k for nm, k in name2key.items() if nm in system_prompt), "?")
        calls.append(key)
        if responses and key in responses:
            return responses[key]()
        return {"error": False, "content": f"[{key} 领域结论] 营收增长 [P 10]",
                "tool_calls": [], "rounds": 1}

    monkeypatch.setattr(da, "is_llm_ready", lambda: True)
    monkeypatch.setattr(da, "safe_invoke_with_tools", fake_with_tools)
    monkeypatch.setattr(da, "safe_invoke",
                        lambda sp, um: {"error": False, "content": "## 综合结论\n已合并", "tool_calls": []})
    return da, calls


def test_e2_domain_agents_run_all(monkeypatch):
    da, calls = _patch_llm(monkeypatch)
    out = da.run_domain_agents(_state_with_domains(), {}, [])
    assert set(out["analyses"]) == {"overview", "operating", "financial"}
    assert sorted(calls) == ["financial", "overview", "operating"]
    assert all("[P 10]" in t for t in out["analyses"].values()), "领域产出必须带页码引用"


def test_e2_domain_failure_isolated(monkeypatch):
    """单领域炸掉不阻断其余"""
    def boom():
        raise RuntimeError("boom")

    da, _ = _patch_llm(monkeypatch, responses={"operating": boom})
    error_log = []
    out = da.run_domain_agents(_state_with_domains(), {}, error_log)
    assert set(out["analyses"]) == {"overview", "financial"}
    assert any("operating" in e for e in error_log)


def test_e2_synthesize_llm_and_fallback(monkeypatch):
    da, _ = _patch_llm(monkeypatch)
    st = _state_with_domains()
    analyses = {"operating": "经营结论A", "financial": "财务结论B"}

    text = da.synthesize_analyses(analyses, st)
    assert "## 综合结论" in text and "已合并" in text, "LLM 可用走合并"

    monkeypatch.setattr(da, "is_llm_ready", lambda: False)
    text2 = da.synthesize_analyses(analyses, st)
    assert "经营结论A" in text2 and "财务结论B" in text2, "兜底拼接不丢内容"
    assert "拼接" in text2 and "经营结论A" in text2.split("财务结论B")[0], "固定领域序"


def test_e2_analyst_domain_mode_routing(monkeypatch):
    """domain_contexts 非空 + flag 开 → Analyst 走领域模式（产出 = Synthesizer 结果）"""
    import agents.financial_analyst as fa
    import agents.domain_analysts as da
    from utils.config import get_settings

    monkeypatch.setattr(get_settings(), "USE_MULTILEVEL_COMPRESSION", True)
    monkeypatch.setattr(get_settings(), "USE_DOMAIN_AGENTS", True)
    monkeypatch.setattr(da, "run_domain_agents",
                        lambda st, a, e: {"analyses": {"operating": "经营结论"}, "tool_history": []})
    monkeypatch.setattr(da, "synthesize_analyses", lambda ans, st: "## 综合分析\n经营结论")

    out = fa.financial_analyst_node(_state_with_domains())
    assert out["analysis_result"] == "## 综合分析\n经营结论"
    assert out["domain_analyses"] == {"operating": "经营结论"}, "领域产出写回 state（修订回炉缓存）"


def test_e2_analyst_fallback_when_no_domain_contexts(monkeypatch):
    """domain_contexts 空（joinn/flag 关）→ 回退全局装配路径"""
    import agents.financial_analyst as fa
    from utils.config import get_settings

    monkeypatch.setattr(get_settings(), "USE_MULTILEVEL_COMPRESSION", True)
    monkeypatch.setattr(fa, "is_llm_ready", lambda: True)
    monkeypatch.setattr(fa, "safe_invoke_with_tools",
                        lambda *a, **kw: {"error": False, "content": "## 公司基本面\n全局路径 [P 0]",
                                          "tool_calls": [], "rounds": 1})
    st = _state_with_domains()
    st["domain_contexts"] = {}
    out = fa.financial_analyst_node(st)
    assert "全局路径" in out["analysis_result"]


def test_e2_revision_reruns_only_defect_domain(monkeypatch):
    """defect_domain=operating → 只回炉 operating，其余从 state.domain_analyses 携带复用"""
    da, calls = _patch_llm(monkeypatch)
    st = _state_with_domains()
    st["domain_analyses"] = {"overview": "旧概览", "operating": "旧经营", "financial": "旧财务"}
    st["defect_domain"] = "operating"
    st["review_feedback"] = "经营分析缺少毛利率讨论"
    st["prev_analysis_result"] = "上一版综合"

    out = da.run_domain_agents(st, {}, [])
    assert calls == ["operating"], "只回炉 operating"
    assert out["analyses"]["overview"] == "旧概览" and out["analyses"]["financial"] == "旧财务"
    assert "领域结论" in out["analyses"]["operating"], "operating 必须重跑出新产出"
```

- [ ] **Step 6.2: 运行确认失败**

Run: `pytest tests/test_e2_domain_agents.py -v`
Expected: FAIL（`ModuleNotFoundError: agents.domain_analysts`）

- [ ] **Step 6.3: 实现 `domain_analysts.py`**

```python
"""
FinScope 领域分析 Agent 组 + Synthesizer（P5-E2）

5 领域按 A 股年报十节模板分工（key 与 chapter_tagger.DOMAINS 对齐）:
overview(0,1,8,99) / operating(3) / financial(2,9,10) / governance(4,5) / events(6,7)

- 领域上下文: ContextPreparator 预装配的 domain_contexts（各自独立预算，直读专属章节）
- facts 全局可查: 每个领域 agent 挂 fetch_context/query_fact/search_section 三工具
  （跨领域勾稽不塌——任何领域查任何科目都走 query_fact 全局事实表）
- 并行: ThreadPoolExecutor(DOMAIN_MAX_PARALLEL_AGENTS)；单领域失败不阻断其余
- Synthesizer: 合并领域产出为统一 analysis_result（跨域矛盾显式标注）；LLM 失败确定性拼接
- 修订: defect_domain 非空 → 只回炉该领域，其余产出从 state.domain_analyses 携带复用

注意: financial_analyst 必须在**函数内**延迟导入本模块（本模块模块级引用其
_TOOL_GUIDE，若两侧都模块级导入会循环）。
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List

from graphs.state import FinancialAnalysisState
from utils.config import get_settings
from utils.llm_client import is_llm_ready, safe_invoke, safe_invoke_with_tools

from agents.financial_analyst import _TOOL_GUIDE  # 工具指南复用同一份口径（导入约定见模块 docstring）
from extractors.chapter_tagger import DOMAINS

logger = logging.getLogger(__name__)

_DOMAIN_NAMES = {d["key"]: d["name"] for d in DOMAINS}

# 领域分析要点（prompt 片段）
_DOMAIN_BRIEFS = {
    "overview": "主营业务与商业模式、行业定位、报告期重要风险因素（含重要提示与风险章节）。",
    "operating": "报告期经营情况回顾、收入构成与变动原因、核心竞争力、未来展望（MD&A）。",
    "financial": "主要会计数据与财务指标、资产负债结构、利润表与现金流量关键科目、审计意见。",
    "governance": "公司治理运作、董监高情况、环境与社会责任、员工与合规。",
    "events": "重大诉讼仲裁、承诺履行、关联交易、股份变动与股东结构。",
}

_SYNTH_ORDER = ("overview", "operating", "financial", "governance", "events")


def _run_one_domain(key: str, domain_context: str, state: FinancialAnalysisState) -> Dict[str, Any]:
    """单领域 agent 完整运行（有界工具循环）。返回 {text, error, tool_history}。"""
    settings = get_settings()
    l1 = state.get("pdf_l1") or {}
    name = _DOMAIN_NAMES.get(key, key)
    tool_history: List[Dict[str, Any]] = []

    def _audit(tool_name: str, args: Dict[str, Any], result: str) -> None:
        tool_history.append({
            "agent": f"domain_{key}",
            "tool": tool_name,
            "args": args,
            "ts": datetime.now().isoformat(timespec="seconds"),
            "result_len": len(result or ""),
        })

    from agents.context_tools import build_context_tools
    tools = build_context_tools(l1, on_tool_call=_audit)

    revision = bool(state.get("review_feedback") and (state.get("domain_analyses") or {}).get(key))
    prev_domain = (state.get("domain_analyses") or {}).get(key, "")

    if revision:
        system_prompt = f"""你是资深金融行业研究员（{name} 领域·修订模式）。

## 审查反馈（只针对本领域，逐条处理）
{state.get("review_feedback", "")}

## 上一版本领域分析（修订基础，只改反馈涉及的问题）
{prev_domain[:4000]}

## 领域专属上下文（供核对，严禁编造新数据；可继续用工具核验数字）
{domain_context}

{_TOOL_GUIDE}

## 输出修订后的完整领域分析（Markdown，保留 [P 页码] 引用）
"""
    else:
        system_prompt = f"""你是资深金融行业研究员，负责「{name}」领域的深度分析。

## 领域职责
{_DOMAIN_BRIEFS.get(key, "")}

## 领域专属上下文（预算装配，直读本领域章节；装不下的用工具调取）
{domain_context}

## 强制约束与引用规范
- 所有结论必须基于上下文或工具返回结果；数据缺失标注「数据不足，暂不评价」
- 严禁编造数据、指标具体数值；严禁「买入/卖出」等确定性投资建议
- 引用 PDF 原文数据的结论必须标注页码 [P 页码]

{_TOOL_GUIDE}

## 输出格式
Markdown，直接以领域内容开头（不要重复大标题），关键结论后标注 [P 页码]。
"""

    result = safe_invoke_with_tools(
        system_prompt,
        "请基于提供的数据进行领域分析；数据不足时调用工具补取后再回答。",
        tools,
        max_rounds=settings.MAX_TOOL_ROUNDS_PER_AGENT,
        on_tool_call=_audit,
    )
    if result.get("error") or not result.get("content", "").strip():
        return {"text": "", "error": True, "tool_history": tool_history}
    return {"text": result["content"], "error": False, "tool_history": tool_history}


def run_domain_agents(
    state: FinancialAnalysisState,
    agent_status: Dict[str, str],
    error_log: list,
) -> Dict[str, Any]:
    """
    并行运行领域 agent 组。

    修订语义: defect_domain 非空 → 只重跑该领域（其余从 state.domain_analyses 携带）；
    修订但 defect_domain 为空 → 全量重跑（无法定位就保守都看一遍）。

    Returns: {"analyses": {domain_key: 分析文本}, "tool_history": [...]}
             （应跑领域全灭且无携带 → analyses 为空 dict，调用方回退全局路径）
    """
    settings = get_settings()
    domain_contexts = state.get("domain_contexts") or {}
    if not domain_contexts:
        return {"analyses": {}, "tool_history": []}

    prev = {k: v for k, v in (state.get("domain_analyses") or {}).items()
            if k in domain_contexts and v}
    only = state.get("defect_domain") or ""
    if only and only in domain_contexts:
        todo = [only]
        carried = {k: v for k, v in prev.items() if k != only}
    else:
        todo = list(domain_contexts.keys())
        carried = {}

    def _work(key: str) -> Dict[str, Any]:
        try:
            return _run_one_domain(key, domain_contexts[key], state)
        except Exception as e:  # 单领域失败不阻断其余
            logger.warning("[DomainAgents] %s 运行异常: %s", key, str(e)[:120])
            return {"text": "", "error": True, "tool_history": []}

    analyses: Dict[str, str] = dict(carried)
    merged_history: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(settings.DOMAIN_MAX_PARALLEL_AGENTS, len(todo))) as pool:
        for key, res in zip(todo, pool.map(_work, todo)):
            if res["error"] or not res["text"]:
                error_log.append(f"[DomainAgents] 领域 {key} 分析失败（跳过）")
                continue
            analyses[key] = res["text"]
            merged_history.extend(res["tool_history"])

    logger.info("[DomainAgents] 完成: %d/%d 领域新产出（携带复用 %d）",
                len(analyses) - len(carried), len(todo), len(carried))
    return {"analyses": analyses, "tool_history": merged_history}


def synthesize_analyses(domain_analyses: Dict[str, str], state: FinancialAnalysisState) -> str:
    """Synthesizer: 合并领域产出 → 统一 analysis_result。LLM 失败 → 确定性拼接（固定领域序）。"""
    if not domain_analyses:
        return ""
    ordered = [(k, domain_analyses[k]) for k in _SYNTH_ORDER if k in domain_analyses]

    if is_llm_ready():
        sections_text = "\n\n".join(
            f"### {_DOMAIN_NAMES.get(k, k)}\n{t[:4000]}" for k, t in ordered
        )
        system_prompt = f"""你是资深金融行业研究员（综合分析），把各领域分析合并为一份统一的分析结论。

## 规则
1. 保留各领域关键结论与 [P 页码] 引用，不得丢失数字
2. 跨领域矛盾（如经营叙述的收入与财务报表收入不一致）必须显式标注「⚠ 跨域不一致」，不得静默取舍
3. 输出结构: ## 公司概览与风险 / ## 经营分析 / ## 财务分析 / ## 治理与ESG / ## 重要事项与股东 / ## 综合结论
4. 严禁编造各领域分析中不存在的数据

## 领域分析
{sections_text}
"""
        result = safe_invoke(system_prompt, "请合并领域分析为统一结论。")
        if not result.get("error") and result.get("content", "").strip():
            return result["content"]

    # 确定性兜底: 固定领域序拼接（LLM 不可用/失败时保底可用）
    lines = ["> （Synthesizer 规则拼接版: LLM 不可用，按领域原文拼接）"]
    for k, t in ordered:
        lines.append(f"## {_DOMAIN_NAMES.get(k, k)}\n\n{t}")
    return "\n\n".join(lines)
```

- [ ] **Step 6.4: Analyst 门控（`financial_analyst.py::financial_analyst_node`）**

`if use_multilevel:` 块内、`_multilevel_analyst_run` 调用**之前**插入:

```python
    if use_multilevel:
        # [P5-E2] 领域模式: Preparator 判定十节覆盖达标（domain_contexts 非空）→ 领域 agent 组
        # （延迟导入: domain_analysts 模块级引用本模块 _TOOL_GUIDE，防循环导入）
        if settings.USE_DOMAIN_AGENTS and state.get("domain_contexts"):
            from agents.domain_analysts import run_domain_agents, synthesize_analyses
            dom = run_domain_agents(state, agent_status, error_log)
            if dom["analyses"]:
                analysis_text = synthesize_analyses(dom["analyses"], state)
                agent_status["financial_analyst"] = "done"
                return {
                    "analysis_result": analysis_text,
                    "domain_analyses": dom["analyses"],
                    "tool_call_history": list(state.get("tool_call_history", [])) + dom["tool_history"],
                    "agent_status": agent_status,
                    "error_log": error_log,
                }
            # 领域组全灭 → 回退全局路径（error_log 已留痕）
        new_result = _multilevel_analyst_run(state, agent_status, error_log, revision_mode)
        ...（原逻辑不变）
```

- [ ] **Step 6.5: 运行确认通过 + 全量回归**

Run: `pytest tests/test_e2_domain_agents.py -v && pytest`
Expected: 新增 6 条 PASS；全量绿（预估 ~178，以实际为准）。

- [ ] **Step 6.6: Commit**

```bash
git add src/agents/domain_analysts.py src/agents/financial_analyst.py tests/test_e2_domain_agents.py
git commit -m "feat(p5): E2 领域 agent 组 + Synthesizer——5 域并行直读 + 跨域矛盾显式标注 + defect_domain 精准回炉"
```

---

### Task 7: Reviewer defect_domain 判定 + 跨源告警消费

**Files:**
- Modify: `src/agents/reviewer.py`
- Test: `tests/test_e7_reviewer_domain.py`

- [ ] **Step 7.1: 写失败测试**

```python
"""P5-E7 测试: Reviewer defect_domain 判定 + 跨源告警消费"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.state import create_initial_state

_CROSS_MISMATCH = [{
    "metric": "营业收入", "status": "mismatch", "n_prose_hits": 1, "n_consistent": 0,
    "n_table_facts": 1,
    "mismatches": [{"prose_value": "30.50亿元", "prose_src": "p45（s_100）",
                    "table_values": ["3,322,399,262.33"], "rel_diff": 0.08}],
}]


def test_e7_schema_has_defect_domain():
    from agents.reviewer import ReviewVerdict
    fields = ReviewVerdict.model_fields
    assert "defect_domain" in fields
    assert fields["defect_domain"].default == ""


def test_e7_review_input_includes_cross_warnings():
    from agents.reviewer import _build_review_input
    st = create_initial_state("q")
    st["final_report"] = "# 报告"
    st["cross_source_checks"] = _CROSS_MISMATCH
    text = _build_review_input(st)
    assert "跨源核对告警" in text and "营业收入" in text and "30.50亿元" in text


def test_e7_reviewer_returns_defect_domain(monkeypatch):
    import agents.reviewer as rev

    monkeypatch.setattr(rev, "is_llm_ready", lambda: True)
    verdict_json = ('{"verdict": "revise", "defect_locus": "analysis", "defect_domain": "operating", '
                    '"issues_found": ["经营分析缺少毛利率"], "feedback": "补充毛利率"}')
    monkeypatch.setattr(rev, "safe_invoke",
                        lambda sp, um: {"error": False, "content": verdict_json, "tool_calls": []})

    st = create_initial_state("q")
    st["final_report"] = "# 富瑞特装投资分析报告\n营业收入增长 [P 8]"
    out = rev.reviewer_node(st)
    assert out["review_result"] == "revise"
    assert out["defect_domain"] == "operating", "缺陷领域必须随判定透传"


def test_e7_defect_domain_empty_on_degrade(monkeypatch):
    """输出不可解析且 schema 重试失败 → 降级 pass，defect_domain 必须为空"""
    import agents.reviewer as rev

    monkeypatch.setattr(rev, "is_llm_ready", lambda: True)
    monkeypatch.setattr(rev, "safe_invoke",
                        lambda sp, um: {"error": False, "content": "不是 JSON", "tool_calls": []})
    monkeypatch.setattr(rev, "_schema_retry", lambda sp, um: None)

    st = create_initial_state("q")
    st["final_report"] = "# 报告\n结论 [P 8]"
    out = rev.reviewer_node(st)
    assert out["review_result"] == "pass"
    assert out["defect_domain"] == ""
```

- [ ] **Step 7.2: 运行确认失败**

Run: `pytest tests/test_e7_reviewer_domain.py -v`
Expected: FAIL（schema 无 defect_domain 字段、输入无跨源告警、返回无 defect_domain）

- [ ] **Step 7.3: 实现（修改 `reviewer.py`）**

① `ReviewVerdict` 加字段（`defect_locus` 之后）:

```python
    defect_domain: Literal["", "overview", "operating", "financial", "governance", "events"] = Field(
        default="",
        description="缺陷领域（E2 领域模式下 Analyst 精准回炉；非领域模式或无法定位为空字符串）",
    )
```

② `_schema_retry` 返回 dict 加一行: `"defect_domain": obj.defect_domain,`

③ `_build_review_input` 在「待审查最终报告」块之后、「上一轮审查反馈」之前加:

```python
    # [P5-E5] 跨源核对告警（ContextPreparator 确定性对账产出，供审查裁决）
    cross_checks = state.get("cross_source_checks") or []
    if cross_checks:
        from extractors.cross_checker import render_cross_warnings
        warn_text = render_cross_warnings(cross_checks)
        if warn_text:
            parts.append(warn_text)
```

④ system_prompt「缺陷归属判定」段之后加判定指南，且 JSON 模板加 `defect_domain` 行:

```
## 缺陷领域判定（defect_domain，领域架构下供分析组精准回炉）
报告问题所属领域: 财务数据/指标错误 → "financial"；经营/业务分析缺陷 → "operating"；
治理/ESG 相关 → "governance"；重要事项/股东信息 → "events"；公司概况/风险提示 → "overview"；
跨领域或无法定位 → ""
```

JSON 模板内加: `"defect_domain": "financial 或 operating 或 governance 或 events 或 overview 或 空字符串",`

⑤ 解析段读取 + 归一化（替换现有「归一化 defect_locus」块）:

```python
    if review is not None:
        verdict = review.get("verdict", "pass")
        feedback = review.get("feedback", "")
        issues_found = review.get("issues_found", [])
        defect_locus = review.get("defect_locus", "both")
        defect_domain = review.get("defect_domain", "")
    else:
        ...（降级分支不变）
        defect_domain = ""

    # [企业级] CRITICAL 强制 revise 分支不变（defect_locus="report" 由下方归一化清空 defect_domain）

    # 归一化 defect_locus / defect_domain
    if verdict == "revise":
        if defect_locus not in ("analysis", "report", "both"):
            defect_locus = "both"
        if defect_domain not in ("overview", "operating", "financial", "governance", "events"):
            defect_domain = ""
        if defect_locus == "report":
            defect_domain = ""  # 报告呈现层问题不回炉领域 agent
    else:
        defect_locus = ""
        defect_domain = ""
```

⑥ 主 return dict 加: `"defect_domain": defect_domain,`

- [ ] **Step 7.4: 运行确认通过 + 全量回归**

Run: `pytest tests/test_e7_reviewer_domain.py tests/test_a2_reviewer.py -v && pytest`
Expected: 新增 4 条 PASS；A2 Reviewer 既有测试不回归；全量绿。

- [ ] **Step 7.5: Commit**

```bash
git add src/agents/reviewer.py tests/test_e7_reviewer_domain.py
git commit -m "feat(p5): Reviewer defect_domain 判定 + E5 跨源告警消费进审查输入"
```

---

### Task 8: E4 Writer 卖方报告模板 + 派生指标表/告警注入

**Files:**
- Modify: `src/agents/report_writer.py`
- Test: `tests/test_e4_writer_template.py`

- [ ] **Step 8.1: 写失败测试**

```python
"""P5-E4 测试: Writer 卖方模板 + 派生指标表/跨源告警注入"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.state import create_initial_state


def _fact(metric, period, value):
    return {"company": "富瑞特装", "metric": metric, "metric_std": metric, "period": period,
            "value": value, "raw": f"{value:,}", "is_pct": False, "is_subtotal": False,
            "unit": "元", "confidence": 0.9,
            "source": {"page_idx": 8, "table_id": "t_001"}}


def _state_for_writer(with_derived=True, with_cross=True):
    st = create_initial_state("撰写富瑞特装年报分析", pdf_path="")
    st["pdf_l1"] = {"sections": [{"section_id": "s_001", "title": "第三节 管理层讨论与分析",
                                  "tier": "T1", "text": "经营分析。", "page_range": [10],
                                  "table_ids": []}],
                    "tables": [], "facts": []}
    st["extracted_entities"] = [{"entity_type": "company", "entity_name": "富瑞特装"}]
    st["analysis_result"] = "## 综合分析\n结论 [P 10]"
    if with_derived:
        from extractors.derived_metrics import compute_derived_metrics
        st["derived_metrics"] = compute_derived_metrics([
            _fact("营业收入", "FY2024", 110.0), _fact("营业收入", "FY2023", 100.0),
        ])
    if with_cross:
        st["cross_source_checks"] = [{
            "metric": "营业收入", "status": "mismatch",
            "mismatches": [{"prose_value": "30.50亿元", "prose_src": "p45（s_100）",
                            "table_values": ["110"], "rel_diff": 0.7}],
        }]
    return st


def _capture_prompt(monkeypatch):
    import agents.report_writer as wr
    captured = {}

    def fake_with_tools(system_prompt, user_msg, tools, max_rounds=5, on_tool_call=None):
        captured["prompt"] = system_prompt
        return {"error": False, "content": "# 富瑞特装投资分析报告\n结论 [P 8]",
                "tool_calls": [], "rounds": 1}

    monkeypatch.setattr(wr, "is_llm_ready", lambda: True)
    monkeypatch.setattr(wr, "safe_invoke_with_tools", fake_with_tools)
    return captured


def test_e4_sellside_structure_and_injections(monkeypatch):
    captured = _capture_prompt(monkeypatch)
    import agents.report_writer as wr
    from utils.config import get_settings

    monkeypatch.setattr(get_settings(), "USE_MULTILEVEL_COMPRESSION", True)
    out = wr.report_writer_node(_state_for_writer())
    prompt = captured["prompt"]
    assert "投资要点" in prompt and "论点前置" in prompt, "卖方模板结构"
    assert "派生指标铁律" in prompt
    assert "营业收入增长率" in prompt and "+10.00%" in prompt, "派生指标表注入"
    assert "跨源核对告警" in prompt and "30.50亿元" in prompt, "跨源告警注入"
    assert out["final_report"], "报告正常产出"


def test_e4_no_injection_when_empty(monkeypatch):
    """无派生指标/无告警 → prompt 不含对应段（空段不注入）"""
    captured = _capture_prompt(monkeypatch)
    import agents.report_writer as wr
    from utils.config import get_settings

    monkeypatch.setattr(get_settings(), "USE_MULTILEVEL_COMPRESSION", True)
    wr.report_writer_node(_state_for_writer(with_derived=False, with_cross=False))
    assert "派生指标铁律" not in captured["prompt"]
    assert "跨源核对告警" not in captured["prompt"]


def test_e4_old_path_unchanged(monkeypatch):
    """无 PDF（旧路径）保持六大章节，不注入派生指标表"""
    import agents.report_writer as wr
    from utils.config import get_settings

    monkeypatch.setattr(get_settings(), "USE_MULTILEVEL_COMPRESSION", True)
    captured = {}

    def fake_invoke(system_prompt, user_msg):
        captured["prompt"] = system_prompt
        return {"error": False, "content": "# 报告\n内容"}

    monkeypatch.setattr(wr, "is_llm_ready", lambda: True)
    monkeypatch.setattr(wr, "safe_invoke", fake_invoke)

    st = create_initial_state("查询")
    st["analysis_result"] = "分析"
    wr.report_writer_node(st)
    assert "核心结论与投资摘要" in captured["prompt"], "旧路径六大章节不变"
    assert "派生指标铁律" not in captured["prompt"], "旧路径不读 P5 字段"
```

- [ ] **Step 8.2: 运行确认失败**

Run: `pytest tests/test_e4_writer_template.py -v`
Expected: FAIL（prompt 无卖方结构/派生指标表/告警）

- [ ] **Step 8.3: 实现（修改 `report_writer.py::_multilevel_writer_run`）**

① 组装上下文（「深度分析结论」块之后、「PDF 年报内容」块之前）插入:

```python
    # [P5-E4] 派生指标表（确定性算子产物；LLM 只引用 display 列，禁止重算改写）
    from extractors.derived_metrics import render_derived_metrics
    derived_table = render_derived_metrics(state.get("derived_metrics") or [])
    if derived_table:
        parts.append(
            "## 派生财务指标（确定性算子产物，引用「数值」列，禁止重算/取整/改写）\n" + derived_table
        )

    # [P5-E5] 跨源核对告警（mismatch 科目引用需谨慎，以 facts 表为准）
    from extractors.cross_checker import render_cross_warnings
    cross_warn = render_cross_warnings(state.get("cross_source_checks") or [])
    if cross_warn:
        parts.append(cross_warn)
```

② 新链路 system_prompt 的「报告结构」段替换为卖方七段模板，并追加铁律段:

```
## 报告结构（卖方研报模板，必须严格遵循）
### 一、投资要点（论点前置: 3-5 条核心结论，每条含关键数字与 [P 页码]）
### 二、公司概况（主营业务、股权结构、行业定位）
### 三、财务分析（增长性/盈利能力/偿债能力/现金流——优先引用「派生财务指标」表数值）
### 四、经营分析与行业格局
### 五、治理与ESG
### 六、重要事项与风险提示
### 七、投资建议（仅供参考）
- 必须包含免责声明

## 派生指标铁律（必须遵守）
- 「派生财务指标」表的数值由确定性算子计算（公式与来源列可审计），直接引用，禁止自行重算
```

③ 新链路修订 prompt 中「保留六大章节结构」改为「保留报告章节结构（卖方七段模板）」。

（`_collect_full_context` 旧路径与 `_build_fallback_report` 降级模板**不动**。）

- [ ] **Step 8.4: 运行确认通过 + 全量回归**

Run: `pytest tests/test_e4_writer_template.py tests/test_a1_writer.py -v && pytest`
Expected: 新增 3 条 PASS；A1 Writer 既有测试不回归；全量绿。

- [ ] **Step 8.5: Commit**

```bash
git add src/agents/report_writer.py tests/test_e4_writer_template.py
git commit -m "feat(p5): E4 Writer 卖方七段模板 + 派生指标表/跨源告警注入（LLM 零心算）"
```

---

### Task 9: 双样本 E2E 验收 + backlog 收尾

**Files:**
- Create: `scripts/run_e2e_p5.py`
- Modify: `docs/p4-backlog.md`（P5 节条目标注状态）

- [x] **Step 9.1: 写验收脚本（seed 复用 a5，parse_cache 按 pdf hash 共享）**

```python
"""
P5 端到端验收（一次性脚本，2026-09-04）

用法:
  1. python scripts/run_e2e_a5.py seed furui   # seed 复用 a5（parse_cache 按 pdf hash 共享）
  2. python scripts/run_e2e_a5.py seed joinn
  3. python scripts/run_e2e_p5.py run furui    # A股主验收样本（应进领域模式）
  4. python scripts/run_e2e_p5.py run joinn    # 港股回归护栏（应回退全局装配）

P5 验收断言:
- furui: domain_contexts ≥ 3 域 / domain_analyses 有产出 / derived_metrics ok ≥ 1 /
  cross_source_checks 产出 / 报告 [P 页码] 引用 ≥ 1 / facts 抽样溯源 10/10
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
```

- [x] **Step 9.2: seed 双样本（parse_cache 已有可跳过）**

Run: `python scripts/run_e2e_a5.py seed furui && python scripts/run_e2e_a5.py seed joinn`
Expected: 两样本缓存写入成功（sections/tables/facts 数与 A5 验收时一致）

- [x] **Step 9.3: furui 主验收（A股，领域模式）**

Run: `python scripts/run_e2e_p5.py run furui`
Expected: PASS——domain_contexts ≥ 3 域、domain_analyses 有产出、派生指标 ok ≥ 1、
跨源对账产出、溯源 10/10、报告含 [P 页码]。指标数字记入本文件「执行记录」。

- [x] **Step 9.4: joinn 回归护栏（港股，回退路径）**

Run: `python scripts/run_e2e_p5.py run joinn`
Expected: PASS——domain_contexts == []（十节覆盖不足自动回退全局装配）、全链路 done、报告非空。

- [x] **Step 9.5: 回退开关手测 + backlog 收尾**
 - 手测: 通过（furui 强制回退全局装配 domain_contexts=[]，全链路 done；测后恢复默认 true）
 - backlog 收尾: `docs/p4-backlog.md` P5 节 E1–E6 已全部标注 ✅ 完成状态

Run: 在 `.env` 或环境变量临时设 `USE_DOMAIN_AGENTS=false`，重跑 `python scripts/run_e2e_p5.py run furui`
Expected: domain_contexts == []，走全局装配路径，全链路 PASS（回退开关活着）。测完恢复默认 true。

然后: `docs/p4-backlog.md` P5 节逐条标注完成状态（E1-E6 对应 Task 5/6/8/4/2/3）。

- [ ] **Step 9.6: Commit**

```bash
git add scripts/run_e2e_p5.py docs/p4-backlog.md docs/p5-plan.md
git commit -m "test(p5): 双样本 E2E 验收——furui 领域模式 + joinn 回退护栏全通过"
```

---

## 总验收清单（全部勾完才算 P5 完成）

- [x] Task 1 三前提 GO 结论已写入「执行记录」
- [x] furui E2E: 领域模式生效（domain_contexts ≥ 3 域）+ 溯源 10/10 + 派生指标 ok ≥ 1
- [x] joinn E2E: 回退全局装配（domain_contexts == []）+ 全链路 done
- [x] `USE_DOMAIN_AGENTS=false` 回退开关手测通过
- [x] `docs/p4-backlog.md` P5 节条目全部标注状态
- [x] 全量 pytest 绿（Task 0-8 新增测试全数通过，无回归）

## 执行记录（执行时填写）

### Task 1 三前提结论（E2 开工闸门）

> 2026-09-04 双样本 E2E 回填：三前提由零 LLM 模拟 + 真实 E2E 双重实证，均达成。
- 前提 1（装得下）: **GO**（furui E2E 5 域全部构建并产出，domain_analyses=5：[overview/operating/financial/governance/events]；A股十节正则切分生效，joinn 无模板回退全局装配。MD&A超窗部分由 L2 + fetch_context 兜底，见 L2_entries=30）
- 前提 2（勾稽不塌）: **GO**（query_fact 全局事实表接住跨域勾稽；furui E2E Reviewer 以利害维度（defect_domain=financial）抓到缺陷并修订 1 轮；cross_source_checks 3 项产出 [营业收入: mismatch, 归母净利润: no_prose, 资产总计: mismatch]）
- 前提 3（成本可控）: **GO**（公共前缀单版装配拆 5 域，furui 5 域工具调用合计 ~120 次 [overview 26 / operating 20 / financial 24 / governance 28 / events 22] + writer 28 + reviewer 48，量级可控且可用）
- 结论: **GO**（E2/E3/E4/E5/E6 全数解锁并执行完毕）

### Task 9 双样本 E2E 结果

- furui: **PASS（2026-09-04，A股领域模式）**：溯源 10/10 / 领域 5 域 [overview/operating/financial/governance/events] / 派生指标 ok 15 (skip 3) / 跨源对账 3 项 / 修订 1 轮 (defect_domain=financial) / 工具调用 196（5 域 120 + writer 28 + reviewer 48）/ 报告 8042 字符，引用 71 处 / 22 页
- joinn: **PASS（2026-09-04，港股回退护栏）**：回退全局装配 ✅（domain_contexts=[]）/ 全链路 done ✅（8 agent 全 done）/ 报告 7457 字符
- `USE_DOMAIN_AGENTS=false` 回退开关手测: **通过**（furui 强制走全局装配 domain_contexts=[]，全链路 done，报告 8845 字符，溯源 10/10；测后恢复默认 true）
- 全量测试: **185 PASS**（链接到总验收清单）