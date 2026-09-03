"""
解析缓存 + MinerU 服务模式回归测试

覆盖:
1. 缓存往返: put → get 返回原 payload 并带 cache_hit 元信息
2. schema 版本失效: 版本不匹配视为未命中（抽取逻辑变更后旧缓存自动失效）
3. 坏缓存自愈: 损坏条目读取返回 None 且被清除，不抛异常
4. 内容哈希: 同内容不同文件名 → 同哈希；不同内容 → 不同哈希
5. 工具级集成: extract_report_key_info 二次调用命中缓存（解析函数只被调一次）
6. MinerU API 服务模式响应解析: content_list 全路径 + md_content 降级路径
"""

import sys
import os
import json
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _make_cache(schema_version=None):
    from extractors.parse_cache import ParseCache, SCHEMA_VERSION

    tmp = tempfile.mkdtemp(prefix="finscope_pc_test_")
    return ParseCache(tmp, schema_version or SCHEMA_VERSION)


def test_cache_roundtrip():
    """put → get 必须原样取回 payload 并附带命中元信息"""
    cache = _make_cache()
    payload = {"error": False, "extraction": {"companies": ["宁德时代"], "structured_pages": [{"page_idx": 0}]}}

    assert cache.get("deadbeef") is None, "空缓存必须未命中"
    assert cache.put("deadbeef", payload, parser="mineru"), "写缓存应成功"

    got = cache.get("deadbeef")
    assert got is not None, "写后必须命中"
    assert got["extraction"]["companies"] == ["宁德时代"], "payload 内容必须一致"
    assert got["_cache_hit"] is True, "命中必须带 cache_hit 标记"
    assert got["_cached_at"], "命中必须带缓存时间"
    assert got["_cache_parser"] == "mineru", "命中必须带解析器来源"
    print("PASS: 缓存往返 + 命中元信息")


def test_schema_version_invalidates():
    """schema 版本不匹配必须视为未命中（旧缓存自动失效）"""
    cache_v1 = _make_cache(schema_version=1)
    cache_v2 = _make_cache(schema_version=2)
    cache_v1.put("cafe01", {"error": False, "extraction": {}})
    assert cache_v1.get("cafe01") is not None, "同版本必须命中"
    assert cache_v2.get("cafe01") is None, "版本不匹配必须未命中"
    print("PASS: schema 版本失效")


def test_corrupt_cache_self_heals():
    """损坏缓存读取返回 None 且自愈清除，绝不抛异常"""
    cache = _make_cache()
    entry = Path(cache.cache_dir) / "badfeed"
    entry.mkdir(parents=True)
    (entry / "meta.json").write_text("{corrupt json", encoding="utf-8")
    (entry / "payload.json").write_text("not json at all", encoding="utf-8")

    got = cache.get("badfeed")
    assert got is None, "损坏缓存必须返回未命中而非异常"
    assert cache.get("badfeed") is None, "自愈后仍应为未命中（不崩溃）"
    print("PASS: 坏缓存自愈")


def test_hash_is_content_based():
    """同内容不同文件名 → 同哈希（上传同名覆盖 bug 的根因验证）"""
    from extractors.parse_cache import compute_pdf_hash

    tmp = Path(tempfile.mkdtemp(prefix="finscope_pc_hash_"))
    content = b"%PDF-1.7 fake report content"
    (tmp / "研报A.pdf").write_bytes(content)
    (tmp / "研报B_同名.pdf").write_bytes(content)
    (tmp / "研报C_不同.pdf").write_bytes(b"%PDF-1.7 different")

    h1 = compute_pdf_hash(str(tmp / "研报A.pdf"))
    h2 = compute_pdf_hash(str(tmp / "研报B_同名.pdf"))
    h3 = compute_pdf_hash(str(tmp / "研报C_不同.pdf"))
    assert h1 == h2, "同内容不同文件名必须同哈希"
    assert h1 != h3, "不同内容必须不同哈希"
    assert compute_pdf_hash(str(tmp / "不存在.pdf")) is None
    print("PASS: 内容哈希（文件名无关）")


def test_tool_cache_integration(monkeypatch):
    """extract_report_key_info 二次调用必须命中缓存，底层解析只执行一次"""
    import extractors.mineru_extractor as me
    from utils.config import get_settings
    from extractors.parse_cache import ParseCache
    import extractors.parse_cache as pc

    settings = get_settings()
    tmp_cache_dir = tempfile.mkdtemp(prefix="finscope_pc_tool_")
    monkeypatch.setattr(settings, "PARSE_CACHE_DIR", tmp_cache_dir)
    monkeypatch.setattr(settings, "PARSE_CACHE_ENABLED", True)

    # 独立实例注册（隔离全局单例状态）
    cache_obj = ParseCache(tmp_cache_dir)
    monkeypatch.setattr(pc, "_instances", {str(Path(tmp_cache_dir).resolve()): cache_obj})

    fake_text_result = {
        "error": False,
        "full_text": "宁德时代(300750.SZ) 营业收入 4009亿元，给予买入评级，目标价 250 元。",
        "structured_pages": [{"page_idx": 0, "items": [{"type": "text", "content": "hello", "bbox": [0, 0, 0, 0]}]}],
        "total_pages": 1,
        "source": "mineru-api",
    }
    call_count = {"n": 0}

    def fake_extract(pdf_path):
        call_count["n"] += 1
        return dict(fake_text_result)

    monkeypatch.setattr(me, "extract_pdf_text", fake_extract)

    from tools.financial_tools import extract_report_key_info

    tmp = Path(tempfile.mkdtemp(prefix="finscope_pc_pdf_"))
    pdf_file = tmp / "report.pdf"
    pdf_file.write_bytes(b"%PDF-1.7 test")

    first = json.loads(extract_report_key_info.invoke({"pdf_path": str(pdf_file)}))
    second = json.loads(extract_report_key_info.invoke({"pdf_path": str(pdf_file)}))

    assert first.get("error") is False, "首次调用必须成功"
    assert first["extraction"].get("cache_hit") is False, "首次调用不应命中缓存"
    assert second.get("error") is False, "二次调用必须成功"
    assert second["extraction"].get("cache_hit") is True, "二次调用必须命中缓存"
    assert call_count["n"] == 1, f"底层解析只应执行一次，实际 {call_count['n']} 次"
    assert second["extraction"].get("parse_source") == "mineru-api"
    print("PASS: 工具级缓存集成（解析一次，二次命中）")


def test_mineru_service_response_parsing():
    """MinerU API 响应解析: 官方 results[<文档名>] 结构 + md_content 降级 + 失败语义"""
    from extractors.mineru_extractor import _parse_service_response, _extract_with_mineru_service

    content_list = [{"type": "text", "text": "营业收入 4009 亿元", "page_idx": 0}]

    # 官方响应结构（MinerU 2.1.x RESTful API）
    official = {
        "backend": "pipeline", "version": "2.1.11",
        "results": {"report.pdf": {"md_content": "# 报告", "content_list": content_list}},
    }
    cl, md = _parse_service_response(official)
    assert cl == content_list, "官方 results 结构必须提取到 content_list"
    assert md == "# 报告"

    # 仅 md_content 的降级响应
    cl2, md2 = _parse_service_response(
        {"results": {"report.pdf": {"md_content": "# 仅 markdown"}}}
    )
    assert cl2 is None and md2 == "# 仅 markdown"

    # 空响应
    assert _parse_service_response({}) == (None, None)

    # 服务不可达必须返回 error dict（不抛异常，交给上层降级本地 CLI）
    r = _extract_with_mineru_service(
        str(Path(tempfile.mkdtemp()) / "nope.pdf"), "http://127.0.0.1:1", 1,
    )
    assert r.get("error") is True, "服务不可达必须返回 error dict 而非抛异常"
    print("PASS: MinerU API 响应解析（官方结构）+ 失败降级语义")


if __name__ == "__main__":
    test_cache_roundtrip()
    test_schema_version_invalidates()
    test_corrupt_cache_self_heals()
    test_hash_is_content_based()
    test_tool_cache_integration()
    test_mineru_service_response_parsing()
    print("\n全部通过 ✓")
