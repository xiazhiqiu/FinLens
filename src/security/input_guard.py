"""
输入防护模块

提供输入安全检查：
- SQL 注入检测
- XSS 攻击检测
- Prompt 注入检测
- 输入长度限制
- 字符白名单
"""

import re
from typing import Optional, Dict, Any, Tuple
from enum import Enum

from common.enterprise_base import EnterpriseBase


class ThreatLevel(Enum):
    """威胁等级"""
    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class InputGuard:
    """输入防护器"""

    # SQL 注入模式
    SQL_INJECTION_PATTERNS = [
        r"(?i)(\b(union|select|insert|update|delete|drop|alter|create|exec|execute|truncate)\b)",
        r"(?i)(--|#|/\*|\*/)",
        r"(?i)(\b(or|and)\b\s+\d+\s*=\s*\d+)",
        r"(?i)(\b(union\b.*\bselect\b|\bselect\b.*\bfrom\b))",
        r"(?i)(\b(insert\b.*\binto\b|\bupdate\b.*\bset\b))",
        r"(?i)(\b(delete\b.*\bfrom\b|\bdrop\b.*\btable\b))",
        r"['\"].*;.*--",
        r"['\"].*\b(or|and)\b.*['\"]",
    ]

    # XSS 攻击模式
    XSS_PATTERNS = [
        r"<script[^>]*>.*?</script>",
        r"javascript:",
        r"on\w+\s*=",
        r"<iframe[^>]*>",
        r"<object[^>]*>",
        r"<embed[^>]*>",
        r"<form[^>]*>",
        r"<svg[^>]*onload",
        r"<img[^>]*onerror",
        r"<body[^>]*onload",
    ]

    # Prompt 注入模式
    PROMPT_INJECTION_PATTERNS = [
        r"(?i)(ignore|disregard|forget)\s+(all\s+)?(previous|prior|above|earlier)",
        r"(?i)(you\s+are\s+now|act\s+as|pretend\s+to\s+be|roleplay\s+as)",
        r"(?i)(system\s+prompt|initial\s+prompt|main\s+prompt)",
        r"(?i)(reveal|show|display|print)\s+(your|the)\s+(system|initial|main)\s+prompt",
        r"(?i)(override|bypass|circumvent)\s+(your|the)\s+(rules|restrictions|guidelines)",
        r"(?i)(you\s+are\s+an?\s+unrestricted|no\s+rules|no\s+restrictions)",
        r"(?i)(do\s+anything|ignore\s+all|forget\s+everything)",
        r"\[INST\].*\[/INST\]",
        r"<<SYS>>.*<</SYS>>",
    ]

    # 危险字符
    DANGEROUS_CHARS = ["<", ">", "&", "\"", "'", ";", "--", "/*", "*/"]

    def __init__(self, max_length: int = 10000, strict_mode: bool = False):
        """
        初始化输入防护器

        Args:
            max_length: 输入最大长度
            strict_mode: 严格模式（任何威胁都拒绝）
        """
        self.max_length = max_length
        self.strict_mode = strict_mode

    def check_input(self, user_input: str) -> Dict[str, Any]:
        """
        全面检查输入

        Returns:
            检查结果字典
        """
        result = {
            "is_safe": True,
            "threat_level": ThreatLevel.SAFE,
            "threats": [],
            "sanitized_input": user_input,
            "recommendations": [],
        }

        if not user_input:
            return result

        # 长度检查
        if len(user_input) > self.max_length:
            result["is_safe"] = False
            result["threats"].append({
                "type": "length_violation",
                "level": ThreatLevel.MEDIUM,
                "message": f"输入长度超过限制: {len(user_input)} > {self.max_length}",
            })

        # SQL 注入检查
        sql_threats = self._check_sql_injection(user_input)
        if sql_threats:
            result["threats"].extend(sql_threats)
            result["is_safe"] = False

        # XSS 检查
        xss_threats = self._check_xss(user_input)
        if xss_threats:
            result["threats"].extend(xss_threats)
            result["is_safe"] = False

        # Prompt 注入检查
        prompt_threats = self._check_prompt_injection(user_input)
        if prompt_threats:
            result["threats"].extend(prompt_threats)
            result["is_safe"] = False

        # 确定最高威胁等级
        if result["threats"]:
            threat_levels = [t["level"] for t in result["threats"]]
            result["threat_level"] = max(threat_levels, key=lambda x: list(ThreatLevel).index(x))

            # 严格模式下，任何威胁都拒绝
            if self.strict_mode:
                result["is_safe"] = False

        # 生成清理后的输入
        result["sanitized_input"] = self._sanitize_input(user_input)

        return result

    def _check_sql_injection(self, text: str) -> list:
        """检查 SQL 注入"""
        threats = []
        for pattern in self.SQL_INJECTION_PATTERNS:
            if re.search(pattern, text):
                threats.append({
                    "type": "sql_injection",
                    "level": ThreatLevel.HIGH,
                    "message": f"检测到潜在 SQL 注入模式: {pattern[:50]}...",
                    "pattern": pattern,
                })
        return threats

    def _check_xss(self, text: str) -> list:
        """检查 XSS 攻击"""
        threats = []
        for pattern in self.XSS_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                threats.append({
                    "type": "xss_attack",
                    "level": ThreatLevel.HIGH,
                    "message": f"检测到潜在 XSS 攻击模式: {pattern[:50]}...",
                    "pattern": pattern,
                })
        return threats

    def _check_prompt_injection(self, text: str) -> list:
        """检查 Prompt 注入"""
        threats = []
        for pattern in self.PROMPT_INJECTION_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                threats.append({
                    "type": "prompt_injection",
                    "level": ThreatLevel.CRITICAL,
                    "message": f"检测到潜在 Prompt 注入攻击: {pattern[:50]}...",
                    "pattern": pattern,
                })
        return threats

    def _sanitize_input(self, text: str) -> str:
        """清理输入"""
        # 转义 HTML 特殊字符
        sanitized = text.replace("&", "&amp;")
        sanitized = sanitized.replace("<", "&lt;")
        sanitized = sanitized.replace(">", "&gt;")
        sanitized = sanitized.replace('"', "&quot;")
        sanitized = sanitized.replace("'", "&#x27;")

        # 移除潜在危险字符
        for char in self.DANGEROUS_CHARS:
            sanitized = sanitized.replace(char, "")

        return sanitized

    def validate_stock_code(self, code: str) -> bool:
        """验证股票代码格式"""
        # A股股票代码：6位数字
        pattern = r"^[0-9]{6}$"
        return bool(re.match(pattern, code))

    def validate_pdf_path(self, path: str) -> bool:
        """验证 PDF 文件路径"""
        # 检查路径是否安全（防止路径遍历）
        if ".." in path or "~" in path:
            return False

        # 检查文件扩展名
        if not path.lower().endswith(".pdf"):
            return False

        return True

    def validate_user_query(self, query: str) -> Tuple[bool, str]:
        """验证用户查询"""
        result = self.check_input(query)

        if not result["is_safe"]:
            threat_msg = "; ".join([t["message"] for t in result["threats"][:3]])
            return False, f"输入不安全: {threat_msg}"

        if len(query.strip()) < 2:
            return False, "查询内容过短"

        return True, "验证通过"
