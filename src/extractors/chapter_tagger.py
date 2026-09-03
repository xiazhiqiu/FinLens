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