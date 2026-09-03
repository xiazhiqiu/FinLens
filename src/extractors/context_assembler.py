"""
FinScope 上下文装配器（P2，预算驱动）

核心原则（架构文档 §六 + implementation-plan v4）:
- **能不压就不压**: 预算内贪心直注 L1 无损原文
- 溢出章节不留无声丢弃: 落一行「可回取」指针（fetch_context 工具按需调取）
- **硬约束**: 装配产物 token 必须 ≤ budget_tokens（末尾硬截断兜底并断言）

P2 定位（P3 未做 L2/L3 前）: 无 L2/L3 可用时，溢出章节只有指针——这正是 P3 要补的
（L2 章节要点自动注入，替代纯指针）。P2 若实测指针过多再启动 P3。

装配顺序: tier 优先级（T0 > T1 > T2，T3 跳过）→ 文档顺序。
查询相关性重排留给后续（当前确定性顺序更可审计）。
"""

import logging
from typing import Any, Dict, List, Optional

from utils.token_counter import count_tokens_safe

logger = logging.getLogger(__name__)

_TIER_RANK = {"T0": 0, "T1": 1, "T2": 2, "T3": 3}

# T0 散文 text-only 直注上限（token）。超过则跳过 prose、落指针——
# 硬数字在 facts[]（query_fact），大表在 fetch_context，无需预注低价值附注散文。
_T0_PROSE_TEXT_ONLY_MAX_TOKENS = 400

# 超长散文优先 L2 摘要的阈值（token）: 单章原文超过此量且已有 L2，则注摘要而非原文
_L2_PREFER_RAW_TOKENS = 1200

# [C2] L2 预留配额: 文档序直注会吃光预算，晚到的大章永远走不到 L2 分支
# （furui 实测 30 条 L2 缓存 0 注入 / joinn 62 条仅 3 条）——P3「溢出章节注摘要
# 而非纯指针」的设计意图实际未发生。预留一小块预算专供 L2，直注提前让位。
# 仅在 L2 缓存非空时生效（无缓存不空占预算）。
_L2_RESERVE_RATIO = 0.15
_L2_RESERVE_MAX_TOKENS = 4000


def _section_head(section: Dict[str, Any]) -> str:
    pr = section.get("page_range") or []
    return f"## [{section.get('section_id')} | {section.get('title')} | {section.get('tier')} | p{pr[0] if pr else '?'}-{pr[-1] if pr else '?'}]"


def render_section_text(section: Dict[str, Any]) -> str:
    """章节纯文本渲染（标题行 + 正文）"""
    text = (section.get("text") or "").strip()
    return text


def render_section_tables(
    section: Dict[str, Any],
    tables_by_id: Dict[str, Dict[str, Any]],
) -> str:
    """章节表格渲染（Markdown 分块，每块自带表头；未解析表给 HTML 提示）"""
    lines: List[str] = []
    for tid in section.get("table_ids") or []:
        tb = tables_by_id.get(tid)
        if tb is None:
            continue
        if not tb.get("headers"):
            lines.append(f"[表 {tid} | 解析失败，保留原始 HTML]")
            continue
        from extractors.table_serializer import serialize_table
        for chunk in serialize_table(tb):
            lines.append(chunk)
    return "\n".join(lines)


def assemble(
    user_query: str,
    budget_tokens: int,
    l1: Dict[str, Any],
    l2: Optional[Dict[str, Any]] = None,
    l3: Optional[str] = None,
) -> Dict[str, Any]:
    """
    预算驱动装配。

    Args:
        user_query: 用户查询（当前用于日志；确定性排序下暂不参与重排）
        budget_tokens: 注入预算（装配结果 token 硬约束）
        l1: L1 产物 {"sections", "tables", "facts", "stats"}
        l2: L2 缓存 {section_id: 压缩条目}（P3；溢出章节优先注入摘要而非纯指针）
        l3: L3 全局摘要文本（P3；可选，有则必注入）

    Returns:
        {
            "context": str,          # 注入文本
            "used": int,             # 实际 token（含安全余量口径）
            "injected": [section_id],# 无损直注的章节
            "l2_injected": [section_id],  # 以 L2 摘要注入的章节
            "pointers": [section_id],# 溢出留指针的章节
            "stats": {...},
        }
    """
    l2 = l2 or {}
    sections = list(l1.get("sections") or [])

    # [C2] L2 预留配额: 直注上限 = 预算 - 预留（仅 L2 缓存非空时让位；
    # L2 注入与指针行可用全额预算）
    if l2:
        l2_reserve = min(int(budget_tokens * _L2_RESERVE_RATIO), _L2_RESERVE_MAX_TOKENS)
    else:
        l2_reserve = 0
    direct_cap = budget_tokens - l2_reserve
    tables_by_id = {t.get("table_id"): t for t in (l1.get("tables") or [])}

    # 1) L3 便签（若有）必注入
    parts: List[str] = []
    used = 0
    if l3 and str(l3).strip():
        head = "# 📌 报告摘要"
        parts.append(f"{head}\n{str(l3).strip()}")
        used += count_tokens_safe(parts[-1])

    # 2) L1 贪心直注（T3 跳过）。排序 = **文档顺序优先 + tier 次级**:
    #    [P3 真实年报修正] 年报按阅读顺序写作（概要→管理层讨论→董事会报告→
    #    主表→附注），价值与页序一致；纯 tier 优先会把 p124+ 财务附注 prose 排到
    #    p26 管理层讨论前面，预算被低价值附注吃光、叙述章节 L2 无处注入。
    ordered = sorted(
        [s for s in sections if s.get("tier") != "T3"],
        key=lambda s: (
            (s.get("page_range") or [0])[0],
            _TIER_RANK.get(s.get("tier"), 9),
        ),
    )

    injected: List[str] = []
    l2_injected: List[str] = []
    pointers: List[str] = []

    # 逐条指针行封顶: 溢出章节可能数百个，逐条列会吃掉预算（挤掉 L2 摘要与正文）。
    # 超过 cap 后只记入 pointers（审计/回取），正文转为一条汇总脚注。
    _POINTER_LINE_CAP = 12

    for sec in ordered:
        sid = sec.get("section_id", "?")
        head_line = _section_head(sec)
        text_part = render_section_text(sec)
        table_part = render_section_tables(sec, tables_by_id)
        raw_text_tokens = count_tokens_safe(text_part)
        l2_entry = l2.get(sid)

        # 尝试整章（文本 + 表格）
        full = f"{head_line}\n{text_part}\n\n{table_part}".strip() if text_part or table_part else ""
        if full:
            sz = count_tokens_safe(full)
            if used + sz <= direct_cap:
                # [P3] 超长散文优先 L2 摘要: 一条 1500 token 原文 ≈ 6 条摘要预算，
                # 覆盖更优且降低阅读负担；原文随时 fetch_context 调取（无损可达）。
                prefer_l2 = (
                    l2_entry and l2_entry.get("text")
                    and raw_text_tokens > _L2_PREFER_RAW_TOKENS
                )
                if prefer_l2:
                    l2_text = f"{head_line}\n{l2_entry['text']}".strip()
                    sz_l2 = count_tokens_safe(l2_text)
                    if used + sz_l2 <= budget_tokens:
                        parts.append(l2_text)
                        used += sz_l2
                        l2_injected.append(sid)
                        continue
                parts.append(full)
                used += sz
                injected.append(sid)
                continue

        # 整章放不下 → 注入该章 L2 摘要（有缓存时；数字保留 + [pNN]，见 section_compressor）
        if l2_entry and l2_entry.get("text"):
            l2_text = f"{head_line}\n{l2_entry['text']}".strip()
            sz_l2 = count_tokens_safe(l2_text)
            if used + sz_l2 <= budget_tokens:
                parts.append(l2_text)
                used += sz_l2
                l2_injected.append(sid)
                if table_part.strip():
                    note = f"> 本章表格未预注，可用 fetch_context(\"{sid}\") 调取完整表格"
                    parts.append(note)
                    used += count_tokens_safe(note)
                continue

        # 退化只注文本（表格指针化）—— 表格仍是无损可用，只是不预注
        if text_part.strip():
            # [P3 真实数据修正] T0 大段散文不做 text-only 兜底:
            #   T0 的硬数字已在 facts[]（query_fact 可取），报表大表在 fetch_context；
            #   附注散文体量大、分析价值低，预注只会挤掉 T1/T2 叙述章节的 L2。
            #   小 T0（短说明）仍允许 text-only 直注。
            t0_prose_too_big = (
                sec.get("tier") == "T0"
                and count_tokens_safe(text_part) > _T0_PROSE_TEXT_ONLY_MAX_TOKENS
            )
            if not t0_prose_too_big:
                text_only = f"{head_line}\n{text_part}".strip()
                sz_t = count_tokens_safe(text_only)
                if used + sz_t <= direct_cap:
                    parts.append(text_only)
                    used += sz_t
                    injected.append(sid)
                    if table_part.strip():
                        note = f"> 本章表格过大未预注，可用 fetch_context(\"{sid}\") 调取"
                        parts.append(note)
                        used += count_tokens_safe(note)
                        pointers.append(sid)
                    continue

        # 仍放不下: 记入 pointers；正文只落前 cap 条指针行，其余并入汇总脚注
        pointers.append(sid)
        if len(pointers) <= _POINTER_LINE_CAP:
            pointer_line = f"- [{sid} | {sec.get('title')} | p{(sec.get('page_range') or [0])[0]}]（fetch_context 调取）"
            if used + count_tokens_safe(pointer_line) <= budget_tokens:
                parts.append(pointer_line)
                used += count_tokens_safe(pointer_line)

    # 汇总脚注: 超过 cap 的溢出章节一条带过（释放预算，供 L2 摘要/正文使用）
    if len(pointers) > _POINTER_LINE_CAP:
        tier_map = {s.get("section_id"): s.get("tier") for s in sections}
        counts: Dict[str, int] = {}
        for pid in pointers[_POINTER_LINE_CAP:]:
            t = tier_map.get(pid, "?")
            counts[t] = counts.get(t, 0) + 1
        tier_desc = "、".join(f"{t}:{c}" for t, c in sorted(counts.items()))
        footer = (f"- …其余 {len(pointers) - _POINTER_LINE_CAP} 章未注入（{tier_desc}），"
                  "可用 fetch_context(章节ID) / search_section(关键词) 按需调取")
        if used + count_tokens_safe(footer) <= budget_tokens:
            parts.append(footer)
            used += count_tokens_safe(footer)

    # 3) 硬上限兜底: 逐段计数与整串 join（含分隔符）存在漂移，弹出至预算内
    context = "\n\n".join(parts)
    used_final = count_tokens_safe(context)
    guard = 0
    while used_final > budget_tokens and len(parts) > 1 and guard < 200:
        parts.pop()
        context = "\n\n".join(parts)
        used_final = count_tokens_safe(context)
        guard += 1
    if used_final > budget_tokens and context:
        # 极端兜底: 半截迭代（中文 ~1.5 token/字，字符截断必须按该密度迭代收敛）
        while used_final > budget_tokens and len(context) > 64:
            context = context[: max(1, len(context) // 2)]
            used_final = count_tokens_safe(context)
    assert used_final <= budget_tokens, f"装配硬约束被破坏: {used_final} > {budget_tokens}"

    stats = {
        "budget": budget_tokens,
        "used": used_final,
        "l2_reserve": l2_reserve,
        "n_injected": len(injected),
        "n_l2_injected": len(l2_injected),
        "n_pointers": len(pointers),
        "n_total_sections": len(ordered),
    }
    logger.info(
        "[Assembler] 预算 %d, 使用 %d, 直注 %d / L2摘要 %d / 指针 %d",
        budget_tokens, used_final, len(injected), len(l2_injected), len(pointers),
    )
    return {
        "context": context,
        "used": used_final,
        "injected": injected,
        "l2_injected": l2_injected,
        "pointers": pointers,
        "stats": stats,
    }
