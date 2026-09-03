"""
数据血缘追踪模块

提供数据来源追踪功能：
- 数据血缘记录
- 影响分析
- 数据溯源
"""

from enum import Enum
from typing import Dict, List, Optional, Any, Set
from datetime import datetime
from dataclasses import dataclass
import hashlib
import threading

from common.enterprise_base import EnterpriseBase


class DataSourceType(Enum):
    """数据源类型"""
    TUSHARE = "tushare"
    AKSHARE = "akshare"
    BLOOMBERG = "bloomberg"
    WIND = "wind"
    PDF_EXTRACTION = "pdf_extraction"
    USER_INPUT = "user_input"
    API = "api"
    DATABASE = "database"
    MANUAL = "manual"


class TransformationType(Enum):
    """数据转换类型"""
    FILTER = "filter"
    AGGREGATE = "aggregate"
    JOIN = "join"
    CALCULATE = "calculate"
    FORMAT = "format"
    VALIDATE = "validate"
    ENRICH = "enrich"


@dataclass
class DataLineageNode:
    """数据血缘节点"""
    node_id: str
    name: str
    source_type: DataSourceType
    created_at: datetime
    metadata: Dict[str, Any]
    parent_ids: List[str] = None
    child_ids: List[str] = None

    def to_dict(self) -> Dict:
        return {
            "node_id": self.node_id,
            "name": self.name,
            "source_type": self.source_type.value,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
            "parent_ids": self.parent_ids or [],
            "child_ids": self.child_ids or [],
        }


@dataclass
class Transformation:
    """数据转换"""
    transform_id: str
    type: TransformationType
    description: str
    input_nodes: List[str]
    output_node: str
    parameters: Dict[str, Any]
    timestamp: datetime

    def to_dict(self) -> Dict:
        return {
            "transform_id": self.transform_id,
            "type": self.type.value,
            "description": self.description,
            "input_nodes": self.input_nodes,
            "output_node": self.output_node,
            "parameters": self.parameters,
            "timestamp": self.timestamp.isoformat(),
        }


class DataLineage:
    """数据血缘管理器"""

    def __init__(self):
        self.nodes: Dict[str, DataLineageNode] = {}
        self.transformations: List[Transformation] = []
        self.node_counter = 0
        self.transform_counter = 0

    def _generate_node_id(self) -> str:
        """生成节点ID"""
        self.node_counter += 1
        return f"DN-{datetime.now().strftime('%Y%m%d')}-{self.node_counter:06d}"

    def _generate_transform_id(self) -> str:
        """生成转换ID"""
        self.transform_counter += 1
        return f"DT-{datetime.now().strftime('%Y%m%d')}-{self.transform_counter:06d}"

    def create_source_node(
        self,
        name: str,
        source_type: DataSourceType,
        metadata: Dict = None,
    ) -> DataLineageNode:
        """创建数据源节点"""
        node = DataLineageNode(
            node_id=self._generate_node_id(),
            name=name,
            source_type=source_type,
            created_at=datetime.now(),
            metadata=metadata or {},
            parent_ids=[],
            child_ids=[],
        )
        self.nodes[node.node_id] = node
        return node

    def create_derived_node(
        self,
        name: str,
        source_type: DataSourceType,
        parent_ids: List[str],
        metadata: Dict = None,
    ) -> DataLineageNode:
        """创建派生数据节点"""
        node = DataLineageNode(
            node_id=self._generate_node_id(),
            name=name,
            source_type=source_type,
            created_at=datetime.now(),
            metadata=metadata or {},
            parent_ids=parent_ids,
            child_ids=[],
        )

        # 更新父节点的子节点列表
        for parent_id in parent_ids:
            if parent_id in self.nodes:
                parent = self.nodes[parent_id]
                if parent.child_ids is None:
                    parent.child_ids = []
                parent.child_ids.append(node.node_id)

        self.nodes[node.node_id] = node
        return node

    def record_transformation(
        self,
        transform_type: TransformationType,
        description: str,
        input_node_ids: List[str],
        output_node_id: str,
        parameters: Dict = None,
    ) -> Transformation:
        """记录数据转换"""
        transform = Transformation(
            transform_id=self._generate_transform_id(),
            type=transform_type,
            description=description,
            input_nodes=input_node_ids,
            output_node=output_node_id,
            parameters=parameters or {},
            timestamp=datetime.now(),
        )
        self.transformations.append(transform)
        return transform

    def trace_upstream(self, node_id: str, max_depth: int = 10) -> Dict[str, Any]:
        """向上溯源"""
        if node_id not in self.nodes:
            return {"error": "节点不存在"}

        upstream = {
            "target_node": node_id,
            "ancestors": [],
            "sources": [],
            "transformations": [],
        }

        visited = set()
        queue = [(node_id, 0)]

        while queue:
            current_id, depth = queue.pop(0)

            if current_id in visited or depth > max_depth:
                continue

            visited.add(current_id)
            node = self.nodes.get(current_id)

            if not node:
                continue

            upstream["ancestors"].append({
                "node_id": current_id,
                "name": node.name,
                "depth": depth,
                "source_type": node.source_type.value,
            })

            # 如果是源节点，记录数据源
            if not node.parent_ids:
                upstream["sources"].append({
                    "node_id": current_id,
                    "name": node.name,
                    "source_type": node.source_type.value,
                })

            # 继续向上追溯
            for parent_id in (node.parent_ids or []):
                if parent_id not in visited:
                    queue.append((parent_id, depth + 1))

        return upstream

    def trace_downstream(self, node_id: str, max_depth: int = 10) -> Dict[str, Any]:
        """向下追踪影响"""
        if node_id not in self.nodes:
            return {"error": "节点不存在"}

        downstream = {
            "source_node": node_id,
            "descendants": [],
            "affected_outputs": [],
            "transformations": [],
        }

        visited = set()
        queue = [(node_id, 0)]

        while queue:
            current_id, depth = queue.pop(0)

            if current_id in visited or depth > max_depth:
                continue

            visited.add(current_id)
            node = self.nodes.get(current_id)

            if not node:
                continue

            downstream["descendants"].append({
                "node_id": current_id,
                "name": node.name,
                "depth": depth,
                "source_type": node.source_type.value,
            })

            # 如果是叶子节点，记录受影响的输出
            if not node.child_ids:
                downstream["affected_outputs"].append({
                    "node_id": current_id,
                    "name": node.name,
                    "source_type": node.source_type.value,
                })

            # 继续向下追踪
            for child_id in (node.child_ids or []):
                if child_id not in visited:
                    queue.append((child_id, depth + 1))

        return downstream

    def get_lineage_graph(self) -> Dict[str, Any]:
        """获取血缘图"""
        nodes = [node.to_dict() for node in self.nodes.values()]
        edges = []

        for transform in self.transformations:
            for input_id in transform.input_nodes:
                edges.append({
                    "source": input_id,
                    "target": transform.output_node,
                    "transformation": transform.to_dict(),
                })

        return {
            "nodes": nodes,
            "edges": edges,
            "statistics": {
                "total_nodes": len(self.nodes),
                "total_transformations": len(self.transformations),
                "source_nodes": len([n for n in self.nodes.values() if not n.parent_ids]),
                "output_nodes": len([n for n in self.nodes.values() if not n.child_ids]),
            }
        }

    def get_data_freshness(self) -> Dict[str, Any]:
        """获取数据新鲜度"""
        now = datetime.now()
        freshness = {}

        for node_id, node in self.nodes.items():
            age_hours = (now - node.created_at).total_seconds() / 3600

            freshness[node_id] = {
                "name": node.name,
                "source_type": node.source_type.value,
                "created_at": node.created_at.isoformat(),
                "age_hours": round(age_hours, 2),
                "is_stale": age_hours > 24,  # 超过24小时认为过期
            }

        return freshness

    def verify_lineage_integrity(self) -> Dict[str, Any]:
        """验证血缘完整性"""
        issues = []

        # 检查孤立节点
        for node_id, node in self.nodes.items():
            # 检查父节点是否存在
            for parent_id in (node.parent_ids or []):
                if parent_id not in self.nodes:
                    issues.append({
                        "type": "missing_parent",
                        "node_id": node_id,
                        "parent_id": parent_id,
                    })

            # 检查子节点是否存在
            for child_id in (node.child_ids or []):
                if child_id not in self.nodes:
                    issues.append({
                        "type": "missing_child",
                        "node_id": node_id,
                        "child_id": child_id,
                    })

        return {
            "is_valid": len(issues) == 0,
            "issues": issues,
            "total_nodes": len(self.nodes),
            "issues_found": len(issues),
        }


# ============================================================
# 模块级共享单例
#
# 修复: 此前 report_extractor / data_retriever / report_writer
# 各自 new 独立的 DataLineage 实例（纯内存、互不相通），导致
# report_writer 在自己的空 registry 里 trace_upstream 必然查不到，
# 报告中的"数据血缘（来源追踪）"段永远不出现。
# ============================================================

_lineage_instance: Optional["DataLineage"] = None
_lineage_lock = threading.Lock()


def get_lineage() -> "DataLineage":
    """获取全进程共享的 DataLineage 单例（线程安全）"""
    global _lineage_instance
    if _lineage_instance is None:
        with _lineage_lock:
            if _lineage_instance is None:
                _lineage_instance = DataLineage()
    return _lineage_instance
