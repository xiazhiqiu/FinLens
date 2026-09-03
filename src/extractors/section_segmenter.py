"""
FinScope 章节切分器（P1 地基，纯规则零 LLM）

职责:
1. 把 mineru_extractor 归一化后的 structured_pages（item.type ∈ {heading, text, table}）
   切成逻辑章节 Section
2. 按标题关键词 + 内容构成为每章打 T0-T3 档位（确定性规则，tier_reason 可审计）

设计依据（2026-09-03 真实年报产物核实，joinn 2024, 189 页）:
- heading 由 text + text_level>0 归一化而来（559 个 lv2 + 2 个 lv1），层级扁平
- 同一逻辑节跨页时标题重复出现并带「（續）」后缀（如「綜合現金流量表」p108/109/110），
  需合并，否则章节碎片化（该样本标题数 561 > 实际逻辑节数）
- H 股年报为繁体中文，关键词表必须同时收录简繁两套

分档只决定「压不压、怎么压」，分档错误不影响数据无损性（L1 在分档前已产出）。
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# T0-T3 关键词（简体 + 繁体双套；纯规则、确定性、可审计）
# ---------------------------------------------------------------------------

T0_KEYWORDS = [
    # 简体（A 股年报/研报）
    "资产负债表", "利润表", "现金流量表", "盈利预测", "分业务", "主要财务数据",
    "财务数据", "关键比率", "财务指标", "评级", "目标价", "估值", "财务报表",
    "损益表", "利润分配", "每股收益", "募集资金", "经营成果",
    # 繁体（H 股年报）
    "資產負債表", "損益表", "現金流量表", "盈利預測", "主要財務數據", "財務數據",
    "關鍵比率", "財務指標", "評級", "目標價", "估值", "財務報表", "綜合損益",
    "綜合全面收益", "財務概要", "每股盈利", "經營成果",
]

T1_KEYWORDS = [
    # 简体
    "投资要点", "核心观点", "核心结论", "投资建议", "估值分析", "风险提示",
    "盈利拆分", "管理层讨论与分析", "董事长报告", "审计意见", "关键审计事项",
    # 繁体
    "投資要點", "核心觀點", "核心結論", "投資建議", "風險提示", "盈利拆分",
    "管理層討論及分析", "董事長報告", "核數師報告", "關鍵審計事項",
]

T2_KEYWORDS = [
    # 简体
    "行业分析", "竞争格局", "公司业务", "经营情况", "业务展望", "主营业务",
    "行业概况", "公司治理", "风险因素",
    # 繁体
    "行業分析", "競爭格局", "公司業務", "業務展望", "主營業務", "行業概況",
    "企業管治", "風險因素", "業務回顧",
]

T3_KEYWORDS = [
    # 简体
    "免责声明", "评级定义", "分析师声明", "重要声明", "目录", "附录", "释义",
    "前瞻性陈述", "股东结构",
    # 繁体
    "免責聲明", "評級定義", "分析師聲明", "重要聲明", "目錄", "附錄", "釋義",
    "前瞻性陳述", "公司資料", "技術詞彙",
]

# 跨页续节后缀:「（續）」「(续)」「（续）」等
_CONTINUATION_RE = re.compile(r"[（(]\s*[續续]\s*[)）]\s*$")

_YEAR_COL_RE = re.compile(r"20\d{2}")  # 表头含年份列 → 疑似财务表


def _strip_continuation(title: str) -> str:
    """去掉标题尾部「（續）」后缀，用于跨页同名节合并"""
    return _CONTINUATION_RE.sub("", title).strip()


def _hit_keyword(title: str, keywords: List[str]) -> Optional[str]:
    """返回命中的关键词（取最长命中，审计理由更具体）；未命中返回 None"""
    hits = [kw for kw in keywords if kw in title]
    if not hits:
        return None
    return max(hits, key=len)


def _looks_financial_table(table_bodies: List[str]) -> bool:
    """轻量启发: 表格含年份列或同比/占比字样 → 疑似财务表（不做深度解析）"""
    for body in table_bodies[:3]:  # 最多看前 3 个表，控制开销
        if _YEAR_COL_RE.search(body) or ("同比" in body) or ("YoY" in body):
            return True
    return False


def classify_tier(
    title: str,
    has_table: bool = False,
    table_bodies: Optional[List[str]] = None,
) -> Tuple[str, str]:
    """
    T0-T3 纯规则分档（if-else 链，顺序即优先级）

    Returns:
        (tier, tier_reason) —— tier_reason 写明判定依据，审计可回放
    """
    table_bodies = table_bodies or []

    # 1) T3: 噪声内容（免责/目录/释义...），后续直接丢弃
    hit = _hit_keyword(title, T3_KEYWORDS)
    if hit:
        return "T3", f"命中T3关键词:{hit}"

    # 2) T0: 财务硬数据（三大报表/盈利预测/评级目标价...），永不压缩
    hit = _hit_keyword(title, T0_KEYWORDS)
    if hit:
        return "T0", f"命中T0关键词:{hit}"

    # 3) T1: 结论类（投资要点/风险提示/核数师报告...），轻压缩
    hit = _hit_keyword(title, T1_KEYWORDS)
    if hit:
        return "T1", f"命中T1关键词:{hit}"

    # 4) T2: 论证类（行业/业务描述...），可重压缩
    hit = _hit_keyword(title, T2_KEYWORDS)
    if hit:
        return "T2", f"命中T2关键词:{hit}"

    # 5) 兜底: 有表格 → 保守 T0（结构化数据宁可不压，不可丢精度）
    if has_table:
        hint = "含表格（疑似财务表）" if _looks_financial_table(table_bodies) else "含表格"
        return "T0", f"无关键词命中但{hint}，保守T0"

    # 6) 纯散文 → 默认 T2（最保守的可压档）
    return "T2", "无关键词命中纯散文，默认T2"


def segment_sections(structured_pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    把 structured_pages 切成逻辑章节

    算法:
    1. 按页序展平 items（structured_pages 已按 page_idx 排序）
    2. 遇 heading 开新章；text 追加正文；table 挂到当前章
    3. 同名标题（忽略「（續）」后缀）的连续 heading 合并进当前章（跨页续节）
    4. 无 heading 的文档（md 降级单页）→ 整篇作单章，不崩

    Returns:
        List[Section]:
        {
            "section_id", "title", "level", "page_range": [start, end],
            "tier", "tier_reason", "text", "table_ids": [...],
            "_tables": [raw table item],   # 供 l1_builder 提取，下游不消费
        }
    """
    if not structured_pages:
        return []

    sections: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    def _close_current():
        nonlocal current
        if current is None:
            return
        # 分档（此时表格已收集齐）
        tier, reason = classify_tier(current["title"], bool(current["_tables"]), current["_bodies"])
        current["tier"] = tier
        current["tier_reason"] = reason
        del current["_bodies"]
        sections.append(current)
        current = None

    table_seq = 0
    last_text_tail = ""  # B8: 同页最近一条 text item 的尾巴（表格单位邻接解析用）

    for page in structured_pages:
        page_idx = page.get("page_idx", 0)
        last_text_tail = ""  # 单位邻接限定同页
        for item in page.get("items", []):
            item_type = item.get("type", "text")

            if item_type == "heading":
                title = str(item.get("content", "")).strip()
                if not title:
                    continue
                # 跨页续节合并: 同名（忽略续后缀）→ 不开新章
                if current is not None and _strip_continuation(title) == _strip_continuation(current["title"]):
                    current["page_range"][1] = page_idx
                    continue
                _close_current()
                current = {
                    "section_id": "",
                    "title": title,
                    "level": int(item.get("level", 2) or 2),
                    "page_range": [page_idx, page_idx],
                    "tier": "",
                    "tier_reason": "",
                    "text": "",
                    "table_ids": [],
                    "_tables": [],
                    "_bodies": [],
                }
            elif item_type == "table":
                body = str(item.get("content", ""))
                if current is None:
                    # 表格出现在第一个标题前（如封面表格）→ 建无名前置章
                    current = {
                        "section_id": "", "title": "(前置内容)", "level": 0,
                        "page_range": [page_idx, page_idx], "tier": "", "tier_reason": "",
                        "text": "", "table_ids": [], "_tables": [], "_bodies": [],
                    }
                table_seq += 1
                table_id = f"t_{table_seq:03d}"
                current["table_ids"].append(table_id)
                current["_tables"].append({
                    "table_id": table_id,
                    "page_idx": page_idx,
                    "html": body,
                    "caption": item.get("caption", []) or [],
                    "footnote": item.get("footnote", []) or [],
                    "adjacent_text": last_text_tail,  # B8: 表格前同页邻接文本
                })
                current["_bodies"].append(body)
            else:  # text
                content = str(item.get("content", "")).strip()
                if not content:
                    continue
                last_text_tail = content[-160:]  # B8: 记录尾巴供表格单位邻接解析
                if current is None:
                    # 正文出现在第一个标题前
                    current = {
                        "section_id": "", "title": "(前置内容)", "level": 0,
                        "page_range": [page_idx, page_idx], "tier": "", "tier_reason": "",
                        "text": "", "table_ids": [], "_tables": [], "_bodies": [],
                    }
                current["text"] += content + "\n"
                current["page_range"][1] = max(current["page_range"][1], page_idx)

    _close_current()

    # 编号
    for i, sec in enumerate(sections, 1):
        sec["section_id"] = f"s_{i:03d}"

    # 统计
    tier_counts: Dict[str, int] = {}
    for sec in sections:
        tier_counts[sec["tier"]] = tier_counts.get(sec["tier"], 0) + 1
    logger.info(
        "[SectionSegmenter] %d 页 -> %d 章节 (T0=%d T1=%d T2=%d T3=%d, 表格 %d 个)",
        len(structured_pages), len(sections),
        tier_counts.get("T0", 0), tier_counts.get("T1", 0),
        tier_counts.get("T2", 0), tier_counts.get("T3", 0),
        sum(len(s["table_ids"]) for s in sections),
    )
    return sections
