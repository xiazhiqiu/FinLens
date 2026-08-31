"""
内容过滤模块

提供内容安全过滤：
- 投资建议违规检测
- 敏感信息脱敏
- 输出内容审查
"""

import re
from enum import Enum
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime

from common.enterprise_base import EnterpriseBase


class FilterType(Enum):
    """过滤类型"""
    INVESTMENT_ADVICE = "investment_advice"  # 投资建议
    SENSITIVE_INFO = "sensitive_info"        # 敏感信息
    COMPLIANCE_VIOLATION = "compliance"      # 合规违规
    PERSONAL_INFO = "personal_info"          # 个人信息
    FINANCIAL_DATA = "financial_data"        # 财务数据


@dataclass
class FilterResult:
    """过滤结果"""
    passed: bool
    filtered_content: str
    original_content: str
    filter_type: FilterType
    violations: List[Dict[str, Any]]
    masked_count: int
    filtered_at: datetime

    def to_dict(self) -> Dict:
        return {
            "passed": self.passed,
            "filter_type": self.filter_type.value,
            "violations": self.violations,
            "masked_count": self.masked_count,
            "filtered_at": self.filtered_at.isoformat(),
            "content_length": len(self.filtered_content),
        }


class ContentFilter:
    """内容过滤器"""

    # 投资建议违规模式
    INVESTMENT_ADVICE_PATTERNS = [
        (r"(?i)(必须|一定要|肯定|必然).*(买入|卖出|持有|加仓|减仓)", "强制性投资建议"),
        (r"(?i)(明天|下周|近期).*(涨停|跌停|大涨|大跌)", "预测性投资建议"),
        (r"(?i)( guaranteed | guaranteed return )", "承诺收益"),
        (r"(?i)(get rich quick|快速致富|一夜暴富)", "不当宣传"),
    ]

    # 敏感信息模式
    SENSITIVE_INFO_PATTERNS = [
        (r"\b[1-9]\d{5}(18|19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b", "身份证号"),
        (r"\b[1-9]\d{9,14}\b", "银行卡号"),
        (r"\b1[3-9]\d{9}\b", "手机号"),
    ]

    # 个人信息模式
    PERSONAL_INFO_PATTERNS = [
        (r"(?i)(患者|病人|医疗|病历)", "医疗信息"),
        (r"(?i)(判决书|案号|被告人)", "司法信息"),
        (r"(?i)(薪资|工资|收入|财产)", "财务隐私"),
    ]

    def __init__(self, strict_mode: bool = False):
        """
        初始化内容过滤器

        Args:
            strict_mode: 严格模式（任何违规都拒绝）
        """
        self.strict_mode = strict_mode

    def filter_content(
        self,
        content: str,
        filter_types: List[FilterType] = None,
        context: Dict = None,
    ) -> FilterResult:
        """
        过滤内容

        Args:
            content: 要过滤的内容
            filter_types: 要执行的过滤类型列表
            context: 额外上下文信息

        Returns:
            过滤结果
        """
        if filter_types is None:
            filter_types = list(FilterType)

        violations = []
        filtered_content = content
        masked_count = 0

        # 执行各类过滤
        for filter_type in filter_types:
            if filter_type == FilterType.INVESTMENT_ADVICE:
                result = self._filter_investment_advice(filtered_content)
            elif filter_type == FilterType.SENSITIVE_INFO:
                result = self._filter_sensitive_info(filtered_content)
            elif filter_type == FilterType.COMPLIANCE_VIOLATION:
                result = self._filter_compliance_violation(filtered_content)
            elif filter_type == FilterType.PERSONAL_INFO:
                result = self._filter_personal_info(filtered_content)
            elif filter_type == FilterType.FINANCIAL_DATA:
                result = self._filter_financial_data(filtered_content)
            else:
                continue

            if result["violations"]:
                violations.extend(result["violations"])

            if result.get("filtered_content"):
                filtered_content = result["filtered_content"]

            masked_count += result.get("masked_count", 0)

        passed = len(violations) == 0 if not self.strict_mode else masked_count == 0

        return FilterResult(
            passed=passed,
            filtered_content=filtered_content,
            original_content=content,
            filter_type=FilterType.COMPLIANCE_VIOLATION,  # 主类型
            violations=violations,
            masked_count=masked_count,
            filtered_at=datetime.now(),
        )

    def _filter_investment_advice(self, content: str) -> Dict:
        """过滤投资建议违规"""
        violations = []
        filtered = content

        for pattern, desc in self.INVESTMENT_ADVICE_PATTERNS:
            matches = re.finditer(pattern, content)
            for match in matches:
                violations.append({
                    "type": "investment_advice_violation",
                    "description": desc,
                    "position": match.span(),
                    "matched_text": match.group(),
                })

                # 替换违规内容
                masked_text = f"[已过滤:{desc}]"
                filtered = filtered.replace(match.group(), masked_text, 1)

        return {
            "violations": violations,
            "filtered_content": filtered,
            "masked_count": len(violations),
        }

    def _filter_sensitive_info(self, content: str) -> Dict:
        """过滤敏感信息"""
        violations = []
        filtered = content
        masked_count = 0

        # 身份证号
        for match in re.finditer(r"\b[1-9]\d{5}(18|19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b", content):
            violations.append({
                "type": "sensitive_info",
                "description": "身份证号",
                "position": match.span(),
            })
            id_card = match.group()
            masked = id_card[:3] + "***********" + id_card[-4:]
            filtered = filtered.replace(id_card, masked, 1)
            masked_count += 1

        # 手机号
        for match in re.finditer(r"\b1[3-9]\d{9}\b", content):
            violations.append({
                "type": "sensitive_info",
                "description": "手机号",
                "position": match.span(),
            })
            phone = match.group()
            masked = phone[:3] + "****" + phone[-4:]
            filtered = filtered.replace(phone, masked, 1)
            masked_count += 1

        # 银行卡号
        for match in re.finditer(r"\b[1-9]\d{9,14}\b", content):
            violations.append({
                "type": "sensitive_info",
                "description": "银行卡号",
                "position": match.span(),
            })
            card = match.group()
            masked = card[:4] + "****" + card[-4:]
            filtered = filtered.replace(card, masked, 1)
            masked_count += 1

        return {
            "violations": violations,
            "filtered_content": filtered,
            "masked_count": masked_count,
        }

    def _filter_compliance_violation(self, content: str) -> Dict:
        """过滤合规违规"""
        violations = []

        # 检查是否有过于绝对的表述
        absolute_patterns = [
            (r"(?i)(一定|必然|肯定|绝对).*(涨|跌|赚|亏)", "绝对化表述"),
            (r"(?i)(100%|百分之百).*(准确|有效|成功)", "夸大宣传"),
        ]

        for pattern, desc in absolute_patterns:
            matches = re.finditer(pattern, content)
            for match in matches:
                violations.append({
                    "type": "compliance_violation",
                    "description": desc,
                    "position": match.span(),
                    "matched_text": match.group(),
                })

        return {
            "violations": violations,
            "filtered_content": content,
            "masked_count": 0,
        }

    def _filter_personal_info(self, content: str) -> Dict:
        """过滤个人信息"""
        violations = []

        for pattern, desc in self.PERSONAL_INFO_PATTERNS:
            matches = re.finditer(pattern, content)
            for match in matches:
                violations.append({
                    "type": "personal_info",
                    "description": desc,
                    "position": match.span(),
                    "matched_text": match.group(),
                })

        return {
            "violations": violations,
            "filtered_content": content,
            "masked_count": 0,
        }

    def _filter_financial_data(self, content: str) -> Dict:
        """过滤财务数据（可选）"""
        violations = []

        # 这里可以根据需要过滤特定财务数据
        # 例如：未公开的财务数据、敏感财务指标等

        return {
            "violations": violations,
            "filtered_content": content,
            "masked_count": 0,
        }

    def validate_report_content(self, report: str) -> Dict[str, Any]:
        """
        验证报告内容

        Returns:
            验证结果
        """
        result = self.filter_content(
            report,
            filter_types=[FilterType.INVESTMENT_ADVICE, FilterType.COMPLIANCE_VIOLATION],
        )

        return {
            "is_valid": result.passed,
            "violations": result.violations,
            "recommendations": self._generate_recommendations(result.violations),
        }

    def _generate_recommendations(self, violations: List[Dict]) -> List[str]:
        """生成修改建议"""
        recommendations = []

        for violation in violations:
            v_type = violation.get("type")
            desc = violation.get("description", "")

            if v_type == "investment_advice_violation":
                recommendations.append(f"请修改 '{desc}' 相关表述，避免使用强制性或预测性投资建议")
            elif v_type == "sensitive_info":
                recommendations.append(f"检测到 {desc}，请进行脱敏处理")
            elif v_type == "compliance_violation":
                recommendations.append(f"请修改 '{desc}' 相关表述，确保合规性")

        return list(set(recommendations))  # 去重
