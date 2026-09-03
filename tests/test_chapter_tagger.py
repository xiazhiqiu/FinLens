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
        _sec("s_001", "重要提示", text="x" * 100),            # chapter 0，不计入分子
        _sec("s_002", "目录", tier="T3", text="b" * 100),     # T3，不计入分母
        _sec("s_003", "第三节 管理层讨论与分析", text="x" * 100),
        _sec("s_004", "第十节 财务报告", text="x" * 100),
        _sec("s_005", "备查文件", text="x" * 100),            # 99，计入分子
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