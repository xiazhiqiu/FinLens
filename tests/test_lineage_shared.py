"""
数据血缘共享单例回归测试

修复背景: report_extractor / data_retriever / report_writer 此前各自 new 独立的
DataLineage 实例（纯内存、互不相通），report_writer 在空 registry 里溯源必然失败，
报告中"数据血缘（来源追踪）"段永远不出现。
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_lineage_shared_across_agents():
    """三个 Agent 模块必须拿到同一个 DataLineage 实例，且溯源链路完整可用"""
    import agents.report_extractor as extractor_mod
    import agents.data_retriever as retriever_mod
    import agents.report_writer as writer_mod
    from audit.data_lineage import get_lineage, DataSourceType, TransformationType

    # 1) 单例同一性: 三个模块的 _get_data_lineage() 必须是同一实例
    assert extractor_mod._get_data_lineage() is get_lineage()
    assert retriever_mod._get_data_lineage() is get_lineage()
    assert writer_mod._get_data_lineage() is get_lineage()

    # 2) 模拟真实链路: extractor 建源节点 → retriever 建派生节点 → writer 溯源
    lineage = get_lineage()
    source = lineage.create_source_node(
        "Tushare数据: 600196", DataSourceType.TUSHARE, metadata={"stock_code": "600196"}
    )
    derived = lineage.create_derived_node(
        "财务数据整合: 600196",
        DataSourceType.MANUAL,
        [source.node_id],
        metadata={"stock_code": "600196"},
    )
    lineage.record_transformation(
        TransformationType.AGGREGATE, "合并研报抽取 + 股票数据", [source.node_id], derived.node_id
    )

    # 3) writer 视角溯源（修复前: 自己的空实例查不到 → {"error": "节点不存在"}）
    upstream = writer_mod._get_data_lineage().trace_upstream(derived.node_id)
    assert "error" not in upstream, f"溯源失败: {upstream}"
    assert any(s["node_id"] == source.node_id for s in upstream["sources"]), "必须溯源到 PDF/数据源节点"
    assert len(upstream["ancestors"]) == 2

    print("PASS: 血缘单例跨 Agent 共享 + 溯源链路完整")


if __name__ == "__main__":
    test_lineage_shared_across_agents()
    print("\n全部通过 ✓")
