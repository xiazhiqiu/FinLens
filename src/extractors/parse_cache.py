"""
FinScope PDF 解析缓存层

为什么存在:
- 200 页研报全文解析耗时数分钟（MinerU 官方推荐整本全文解析，不可拆页加速），
  同一 PDF 反复分析（换问题/换分析类型/修订重跑）不应重复付出解析成本。
- 缓存键 = PDF 内容 SHA-256。文件名/路径不可靠——历史上传按原文件名存储，
  同名文件互相覆盖，路径无法唯一标识内容。

设计要点:
- schema 版本进缓存校验: 抽取/解析产物结构变更时递增 SCHEMA_VERSION，旧缓存自动失效
- 原子写（tmp + rename），坏缓存自愈（读失败即删，不阻断主链路）
- 同哈希 in-flight 去重: 防止并发请求重复解析同一 PDF（双倍 GPU/CPU 时间）
- 缓存任何故障都不抛异常、不阻断解析（never-throw，与项目错误处理模式一致）
"""

import hashlib
import json
import logging
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

logger = logging.getLogger(__name__)

# 产物结构版本: entity_extractor / mineru_extractor / structured_pages 结构变更时必须递增
# v2 (2026-09-03): heading 归一化重构（type=header 页眉噪声剔除、text_level>0 为真标题）
#                  + 新增 L1 产物（sections/tables/facts）。旧 v1 缓存自动失效
SCHEMA_VERSION = 2


def compute_pdf_hash(pdf_path: str) -> Optional[str]:
    """计算 PDF 文件内容的 SHA-256（分块读取，支持大文件）

    Returns:
        64 位十六进制哈希；文件不存在/读取失败返回 None
    """
    try:
        p = Path(pdf_path)
        if not p.is_file():
            return None
        sha = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                sha.update(chunk)
        return sha.hexdigest()
    except Exception as e:
        logger.warning("[ParseCache] 计算哈希失败: %s", e)
        return None


class ParseCache:
    """基于文件系统的 PDF 解析缓存（内容哈希键控）"""

    def __init__(self, cache_dir: str, schema_version: int = SCHEMA_VERSION):
        self.cache_dir = Path(cache_dir)
        self.schema_version = schema_version
        # 同哈希并发去重锁（进程内；单机 Streamlit/FastAPI 进程模型下足够）
        self._inflight_locks: Dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _entry_dir(self, pdf_hash: str) -> Path:
        return self.cache_dir / pdf_hash

    def get(self, pdf_hash: str) -> Optional[Dict[str, Any]]:
        """读取缓存。命中返回 payload（附带 _cache_hit/_cached_at 元信息）；
        未命中/schema 不匹配/缓存损坏返回 None（损坏时自愈删除）"""
        entry = self._entry_dir(pdf_hash)
        meta_path = entry / "meta.json"
        payload_path = entry / "payload.json"
        if not meta_path.is_file() or not payload_path.is_file():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("schema_version") != self.schema_version:
                logger.info(
                    "[ParseCache] schema 版本不匹配 (缓存=%s 当前=%s)，视为未命中",
                    meta.get("schema_version"), self.schema_version,
                )
                return None
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
            payload["_cache_hit"] = True
            payload["_cached_at"] = meta.get("created_at", "")
            payload["_cache_parser"] = meta.get("parser", "unknown")
            return payload
        except Exception as e:
            # 坏缓存自愈: 删除损坏条目，返回未命中
            logger.warning("[ParseCache] 缓存条目损坏，已清除: %s (%s)", pdf_hash[:12], e)
            self._delete_entry(pdf_hash)
            return None

    def put(self, pdf_hash: str, payload: Dict[str, Any], parser: str = "unknown") -> bool:
        """写入缓存（原子写）。任何失败静默返回 False，不阻断主链路"""
        try:
            entry = self._entry_dir(pdf_hash)
            entry.mkdir(parents=True, exist_ok=True)
            meta = {
                "schema_version": self.schema_version,
                "parser": parser,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "pdf_hash": pdf_hash,
            }
            tmp_meta, tmp_payload = entry / "meta.json.tmp", entry / "payload.json.tmp"
            tmp_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_payload.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            # 原子替换（Windows 上 rename 目标存在会失败，先删再换）
            final_meta, final_payload = entry / "meta.json", entry / "payload.json"
            for tmp, final in ((tmp_meta, final_meta), (tmp_payload, final_payload)):
                if final.exists():
                    final.unlink()
                tmp.rename(final)
            return True
        except Exception as e:
            logger.warning("[ParseCache] 写缓存失败（不阻断解析）: %s", e)
            return False

    def _delete_entry(self, pdf_hash: str) -> None:
        try:
            entry = self._entry_dir(pdf_hash)
            if entry.is_dir():
                for f in entry.iterdir():
                    f.unlink(missing_ok=True)
                entry.rmdir()
        except Exception:
            pass

    @contextmanager
    def inflight(self, pdf_hash: str) -> Iterator[None]:
        """同哈希并发去重: 第二个请求阻塞等待首个解析完成后直接读缓存"""
        with self._locks_guard:
            lock = self._inflight_locks.setdefault(pdf_hash, threading.Lock())
        with lock:
            yield
        with self._locks_guard:
            # 无人排队时清理，防锁字典泄漏
            if lock.acquire(blocking=False):
                self._inflight_locks.pop(pdf_hash, None)
                lock.release()


_instances: Dict[str, ParseCache] = {}
_instances_guard = threading.Lock()


def get_parse_cache() -> Optional[ParseCache]:
    """按当前配置获取解析缓存（同目录共享实例以保持 in-flight 锁）；
    PARSE_CACHE_ENABLED=false 或初始化异常时返回 None（调用方走无缓存路径）"""
    try:
        from utils.config import get_settings
        settings = get_settings()
        if not settings.PARSE_CACHE_ENABLED:
            return None
        cache_dir = str(Path(settings.PARSE_CACHE_DIR).resolve())
        with _instances_guard:
            if cache_dir not in _instances:
                _instances[cache_dir] = ParseCache(cache_dir)
            return _instances[cache_dir]
    except Exception as e:
        logger.warning("[ParseCache] 缓存不可用，走无缓存路径: %s", e)
        return None
