"""
防篡改存储模块

提供审计日志的防篡改存储：
- 哈希链机制
- 追加写入
- 完整性验证
"""

import json
import hashlib
from typing import Dict, List, Optional, Any
from datetime import datetime
from dataclasses import dataclass
import os

from common.enterprise_base import EnterpriseBase


@dataclass
class HashChainBlock:
    """哈希链区块"""
    index: int
    timestamp: datetime
    data: Dict[str, Any]
    previous_hash: str
    hash: str
    nonce: int = 0

    def to_dict(self) -> Dict:
        return {
            "index": self.index,
            "timestamp": self.timestamp.isoformat(),
            "data": self.data,
            "previous_hash": self.previous_hash,
            "hash": self.hash,
            "nonce": self.nonce,
        }

    def compute_hash(self) -> str:
        """计算区块哈希"""
        block_string = json.dumps({
            "index": self.index,
            "timestamp": self.timestamp.isoformat(),
            "data": self.data,
            "previous_hash": self.previous_hash,
            "nonce": self.nonce,
        }, sort_keys=True, default=str)
        return hashlib.sha256(block_string.encode()).hexdigest()


class HashChain:
    """哈希链"""

    def __init__(self):
        self.chain: List[HashChainBlock] = []
        self._create_genesis_block()

    def _create_genesis_block(self):
        """创建创世区块"""
        genesis_block = HashChainBlock(
            index=0,
            timestamp=datetime.now(),
            data={"message": "Genesis Block"},
            previous_hash="0" * 64,
            hash="",
        )
        genesis_block.hash = genesis_block.compute_hash()
        self.chain.append(genesis_block)

    def add_block(self, data: Dict[str, Any]) -> HashChainBlock:
        """添加新区块"""
        previous_block = self.chain[-1]

        new_block = HashChainBlock(
            index=previous_block.index + 1,
            timestamp=datetime.now(),
            data=data,
            previous_hash=previous_block.hash,
            hash="",
        )
        new_block.hash = new_block.compute_hash()

        self.chain.append(new_block)
        return new_block

    def verify_integrity(self) -> Dict[str, Any]:
        """验证链完整性"""
        results = {
            "is_valid": True,
            "errors": [],
            "blocks_checked": 0,
        }

        for i in range(1, len(self.chain)):
            current_block = self.chain[i]
            previous_block = self.chain[i - 1]

            # 验证当前区块哈希
            if current_block.hash != current_block.compute_hash():
                results["is_valid"] = False
                results["errors"].append(f"区块 {i} 哈希无效")

            # 验证前向链接
            if current_block.previous_hash != previous_block.hash:
                results["is_valid"] = False
                results["errors"].append(f"区块 {i} 前向链接断裂")

            results["blocks_checked"] += 1

        return results

    def get_block(self, index: int) -> Optional[HashChainBlock]:
        """获取指定区块"""
        if 0 <= index < len(self.chain):
            return self.chain[index]
        return None

    def get_latest_block(self) -> HashChainBlock:
        """获取最新区块"""
        return self.chain[-1]

    def get_chain_length(self) -> int:
        """获取链长度"""
        return len(self.chain)


class ImmutableStore:
    """防篡改存储"""

    def __init__(self, storage_path: str = None):
        """
        初始化防篡改存储

        Args:
            storage_path: 存储路径
        """
        self.storage_path = storage_path or "./data/audit_logs"
        self.hash_chain = HashChain()
        self.records: List[Dict] = []

        # 确保存储目录存在
        os.makedirs(self.storage_path, exist_ok=True)

    def append(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """
        追加记录

        Args:
            record: 要存储的记录

        Returns:
            包含哈希信息的记录
        """
        # 添加元数据
        enriched_record = {
            "data": record,
            "metadata": {
                "timestamp": datetime.now().isoformat(),
                "record_index": len(self.records),
            }
        }

        # 添加到哈希链
        block = self.hash_chain.add_block(enriched_record)

        # 保存记录
        record_with_hash = {
            "record": record,
            "block_index": block.index,
            "block_hash": block.hash,
            "previous_hash": block.previous_hash,
            "timestamp": block.timestamp.isoformat(),
        }

        self.records.append(record_with_hash)

        # 持久化存储
        self._persist_record(record_with_hash)

        return record_with_hash

    def _persist_record(self, record: Dict):
        """持久化记录"""
        # 简化实现：追加写入文件
        record_file = os.path.join(self.storage_path, "audit_chain.jsonl")
        with open(record_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def verify_record(self, record: Dict) -> Dict[str, Any]:
        """验证记录完整性"""
        block_index = record.get("block_index")
        block_hash = record.get("block_hash")
        previous_hash = record.get("previous_hash")

        # 获取对应区块
        block = self.hash_chain.get_block(block_index)
        if not block:
            return {"valid": False, "error": "区块不存在"}

        # 验证哈希
        if block.hash != block_hash:
            return {"valid": False, "error": "区块哈希不匹配"}

        # 验证前向链接
        if block.previous_hash != previous_hash:
            return {"valid": False, "error": "前向链接断裂"}

        return {"valid": True, "block_index": block_index}

    def verify_all(self) -> Dict[str, Any]:
        """验证所有记录"""
        chain_valid = self.hash_chain.verify_integrity()

        return {
            "chain_valid": chain_valid["is_valid"],
            "total_records": len(self.records),
            "blocks_checked": chain_valid["blocks_checked"],
            "errors": chain_valid["errors"],
        }

    def query_records(
        self,
        start_time: datetime = None,
        end_time: datetime = None,
        limit: int = 100,
    ) -> List[Dict]:
        """查询记录"""
        results = self.records

        if start_time:
            results = [r for r in results if datetime.fromisoformat(r["timestamp"]) >= start_time]

        if end_time:
            results = [r for r in results if datetime.fromisoformat(r["timestamp"]) <= end_time]

        return results[-limit:]

    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "total_records": len(self.records),
            "chain_length": self.hash_chain.get_chain_length(),
            "storage_path": self.storage_path,
            "integrity_status": self.verify_all()["chain_valid"],
        }
