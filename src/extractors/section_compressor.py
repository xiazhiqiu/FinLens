"""
FinScope 章节压缩器（P3，章节级 L2 / L3）

章节级 LLM 压缩（区别于已被移除的旧逐页无差别压缩）。核心差异:
- 压缩单元: 逐页 -> **按 Section**
- 触发: 无条件 -> **仅 T1/T2 且体积超阈值的大章节**（T0 报表类永不语义压缩）
- 数字: 保留**原值 + [pNN] 溯源**（不用 fact_id 替代——悬空指针论证见 compression-plan §5.3）
- 表格: 不压（走 table_serializer 结构化输出）

设计:
- compress_section: LLM 压缩（JSON: thesis + key_arguments），不可用/失败时
  **确定性规则兜底**（摘含数字的句子，数字原样保留）
- compress_document_l2: 增量构建 + 跨轮缓存（跳过已有缓存; max_new 熔断防单次 LLM 调用爆炸）
- build_global_summary_lite: L3 确定性亮点表（从表格事实中抽高信号行，零 LLM）

LLM 压缩强度（压多狠）由真实年报标定——代码里 L2_MIN_TEXT_TOKENS 与输出条数上限先给
保守默认值，P3 验收后按实测调。
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from utils.token_counter import count_tokens_safe

logger = logging.getLogger(__name__)

# 语义压缩候选: 只压 T1（结论）与 T2（论证），T0 报表永不压、T3 噪声不入
_COMPRESSIBLE_TIERS = ("T1", "T2")

# 高信号财务词（L3 亮点抽选用）
_HIGHLIGHT_WORDS = (
    "收入", "收益", "純利", "溢利", "毛利", "淨利", "亏损", "虧損",
    "營收", "营收", "现金", "現金", "资产", "資產", "負債", "负债", "權益",
)

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

# 句号分句（保留数字句）
_SENT_SPLIT_RE = re.compile(r"(?<=[。；;！？!?])\s*|\n+")


def _sentences_with_numbers(text: str, max_sents: int = 8) -> List[str]:
    """确定性规则兜底: 摘含数字的句子（数字原样保留），且保证输出 ≤ 原文一半 + 120 字"""
    out: List[str] = []
    budget_chars = max(120, len(text) // 2)
    used = 0
    for sent in _SENT_SPLIT_RE.split(text):
        sent = sent.strip()
        if len(sent) < 8 or len(sent) > 300:
            continue
        if not re.search(r"\d", sent):
            continue
        if used + len(sent) > budget_chars:
            break  # 摘句预算耗尽，截断
        out.append(sent)
        used += len(sent)
        if len(out) >= max_sents:
            break
    return out


def compress_section(section: Dict[str, Any], use_llm: bool = True) -> Dict[str, Any]:
    """
    单章节 -> L2 压缩条目（LLM 优先，规则兜底）。

    Returns:
        {
            "section_id", "title", "tier", "page_first",
            "thesis": str,
            "key_arguments": [str],       # 每条含数字原值；LLM 版每条 [pNN]；规则版页码在 thesis
            "text": str,                  # 可直接注入的渲染文本
            "has_llm": bool,              # 诚实标注压缩来源（血缘）
            "n_text_chars_before", "n_chars_after",
        }
    """
    section_id = section.get("section_id", "?")
    title = section.get("title", "")
    tier = section.get("tier", "")
    text = (section.get("text") or "").strip()
    page_first = (section.get("page_range") or [0])[0]

    if not text:
        return {
            "section_id": section_id, "title": title, "tier": tier,
            "thesis": "", "key_arguments": [], "text": "",
            "has_llm": False, "n_text_chars_before": 0, "n_chars_after": 0,
        }

    parsed = None
    has_llm = False

    # 1) LLM 压缩（受限叶子）
    if use_llm:
        parsed = _compress_with_llm(title, text, page_first)
        has_llm = parsed is not None

    # 2) 规则兜底（确定性，数字保留）
    if parsed is None:
        sents = _sentences_with_numbers(text)
        thesis = f"{title}（规则摘句版, {page_first} 页起）"
        key_args = sents
        parsed = {"thesis": thesis, "key_arguments": key_args}
        has_llm = False

    key_arguments = list(parsed.get("key_arguments") or [])[:10]
    # 渲染文本（装配器注入用）
    lines = [f"### 📄 {title}（要点, p{page_first}）", f"**{parsed.get('thesis', '')}**" if parsed.get('thesis') else ""]
    lines += [f"- {arg}" for arg in key_arguments]
    rendered = "\n".join(x for x in lines if x)

    logger.info(
        "[SectionCompressor] %s %s压缩: %d 字符 -> %d 字符 (LLM=%s)",
        section_id, title[:20], len(text), len(rendered), has_llm,
    )
    return {
        "section_id": section_id,
        "title": title,
        "tier": tier,
        "page_first": page_first,
        "thesis": parsed.get("thesis", ""),
        "key_arguments": key_arguments,
        "text": rendered,
        "has_llm": has_llm,
        "n_text_chars_before": len(text),
        "n_chars_after": len(rendered),
    }


def _compress_with_llm(title: str, text: str, page_first: int) -> Optional[Dict[str, Any]]:
    """LLM 章节散文压缩。铁律: 数字保留原值 + [pNN] 溯源。失败返回 None（规则兜底）"""
    try:
        from utils.llm_client import safe_invoke, is_llm_ready
        if not is_llm_ready():
            return None

        system_prompt = """你是研报章节摘要器。将给定章节的**叙述性文字**压缩为要点。

## 铁律（违反即无效）
1. 只压散文，**数字必须保留原值**（如 204.42 亿、17.21、49.67% 照抄，禁止省略、取整、改写）
2. 引用原文事实时标注页码 [p{page}]
3. 不要输出免责声明类文字；不要编造原文没有的结论

## 输出格式（严格 JSON）
{{"thesis": "≤60字的一句话主旨", "key_arguments": ["要点1(≤80字)", "要点2", ...]}}

## 章节
标题: {title}
正文:
{text}""".format(title=title, text=text[:6000], page=page_first)

        result = safe_invoke(system_prompt, "请压缩为要点，严格输出 JSON。")
        if result.get("error") or not result.get("content"):
            return None

        content = result["content"]
        # 剥 markdown fence 后解析
        if "```" in content:
            content = re.sub(r"```(?:json)?", "", content).strip("`")
        obj = json.loads(content)
        if not isinstance(obj, dict):
            return None
        return {
            "thesis": str(obj.get("thesis", "")).strip(),
            "key_arguments": [str(x).strip() for x in obj.get("key_arguments", []) if str(x).strip()],
        }
    except Exception as e:
        logger.warning("[SectionCompressor] LLM 压缩失败，规则兜底: %s", str(e)[:100])
        return None


def compress_document_l2(
    sections: List[Dict[str, Any]],
    existing_cache: Dict[str, Dict[str, Any]],
    min_text_tokens: int = 600,
    max_new: int = 8,
    use_llm: bool = True,
) -> List[Dict[str, Any]]:
    """
    增量构建 L2: 只处理「T1/T2 + 体积超阈值 + 未缓存」的章节，最多 max_new 个（熔断）。

    - existing_cache: 跨 Agent / 跨修订轮缓存（pdf_l2）——已有条目绝不再压缩
    - 按体积降序处理（最大的先压，收益最高）
    - 返回本次新增条目（调用方写回 pdf_l2）
    """
    candidates = [
        s for s in sections
        if (s.get("tier") in _COMPRESSIBLE_TIERS)
        and count_tokens_safe(s.get("text") or "") >= min_text_tokens
        and s.get("section_id") not in existing_cache
    ]
    candidates.sort(key=lambda s: -count_tokens_safe(s.get("text") or ""))

    built: List[Dict[str, Any]] = []
    for sec in candidates[:max_new]:
        entry = compress_section(sec, use_llm=use_llm)
        existing_cache[sec.get("section_id")] = entry  # 就地入缓存，防同轮重复
        built.append(entry)
    if built:
        llm_n = sum(1 for b in built if b["has_llm"])
        logger.info("[L2Builder] 新增压缩 %d 章 (LLM=%d, 规则=%d), 缓存总量 %d", len(built), llm_n, len(built) - llm_n, len(existing_cache))
    return built


def build_global_summary_lite(l1: Dict[str, Any], company: str = "") -> str:
    """
    [L3-lite] 确定性全局亮点表（零 LLM）。

    从表格定位事实中抽高信号行（命中 收入/收益/純利/毛利/现金/资产 等财务词），
    每期间按指标类型分桶取配额（修「资产总计挤掉 EPS/ROE」大数霸屏）。
    诚实标注「自动抽选，人工未校核」。

    P3 先以规则版落地（可审计、离线可测）；LLM 全文摘要版接入留待调用方决策。
    """
    facts = [f for f in (l1.get("facts") or []) if f.get("confidence", 0) >= 0.9]
    highlight = [f for f in facts if any(w in (f.get("metric") or "") for w in _HIGHLIGHT_WORDS)]
    if not highlight:
        highlight = facts[:8]

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

    lines = [f"### 关键财务亮点（自动抽选·人工未校核）"]
    for period, fs in by_period.items():
        lines.append(f"**{period}**")
        for f in fs:
            unit = f.get("unit") or ""
            src = f.get("source") or {}
            lines.append(
                f"- {f.get('company') or company or '(未绑定)'} {f.get('metric')}: "
                f"{f.get('raw')}{unit} [p{src.get('page_idx', '?')}]"
            )
    return "\n".join(lines)
