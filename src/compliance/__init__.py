"""
FinScope Enterprise Compliance Module

提供企业级合规功能：
- 监管规则引擎（证监会规则）
- 内容过滤（投资建议违规检测）
- 信息隔离墙（Chinese Wall）
"""

from .regulation import RegulationEngine, RegulationRule
from .content_filter import ContentFilter, FilterResult
from .chinese_wall import ChineseWall, InformationBarrier

__all__ = [
    "RegulationEngine",
    "RegulationRule",
    "ContentFilter",
    "FilterResult",
    "ChineseWall",
    "InformationBarrier",
]
