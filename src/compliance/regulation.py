"""
监管规则引擎模块

提供监管规则管理：
- 证监会规则库
- 合规检查规则
- 规则执行引擎
"""

from enum import Enum
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass
from datetime import datetime

from common.enterprise_base import EnterpriseBase


class RegulationType(Enum):
    """监管规则类型"""
    CSRC = "csrc"              # 证监会
    PBOC = "pboc"              # 央行
    NFRA = "nfra"              # 金融监管总局
    SSE = "sse"                # 上交所
    SZSE = "szse"              # 深交所
    INTERNAL = "internal"      # 内部规定


class ViolationSeverity(Enum):
    """违规严重程度"""
    INFO = "info"
    WARNING = "warning"
    MAJOR = "major"
    CRITICAL = "critical"


@dataclass
class RegulationRule:
    """监管规则"""
    rule_id: str
    name: str
    description: str
    regulation_type: RegulationType
    severity: ViolationSeverity
    check_function: Optional[Callable] = None
    enabled: bool = True

    def to_dict(self) -> Dict:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "description": self.description,
            "regulation_type": self.regulation_type.value,
            "severity": self.severity.value,
            "enabled": self.enabled,
        }


@dataclass
class ComplianceCheckResult:
    """合规检查结果"""
    passed: bool
    violations: List[Dict[str, Any]]
    warnings: List[str]
    checked_at: datetime
    rule_ids_checked: List[str]

    def to_dict(self) -> Dict:
        return {
            "passed": self.passed,
            "violations": self.violations,
            "warnings": self.warnings,
            "checked_at": self.checked_at.isoformat(),
            "rules_checked": len(self.rule_ids_checked),
        }


class RegulationEngine:
    """监管规则引擎"""

    def __init__(self):
        self.rules: Dict[str, RegulationRule] = {}
        self._register_default_rules()

    def _register_default_rules(self):
        """注册默认监管规则"""

        # 证监会规则
        self.register_rule(RegulationRule(
            rule_id="CSRC-001",
            name="禁止承诺收益",
            description="禁止在研报中承诺或暗示投资收益",
            regulation_type=RegulationType.CSRC,
            severity=ViolationSeverity.CRITICAL,
            check_function=self._check_no_promised_returns,
        ))

        self.register_rule(RegulationRule(
            rule_id="CSRC-002",
            name="禁止内幕交易暗示",
            description="禁止暗示利用内幕信息进行投资建议",
            regulation_type=RegulationType.CSRC,
            severity=ViolationSeverity.CRITICAL,
            check_function=self._check_no_insider_trading_hints,
        ))

        self.register_rule(RegulationRule(
            rule_id="CSRC-003",
            name="利益冲突披露",
            description="必须披露与分析标的的利益冲突",
            regulation_type=RegulationType.CSRC,
            severity=ViolationSeverity.MAJOR,
            check_function=self._check_conflict_disclosure,
        ))

        self.register_rule(RegulationRule(
            rule_id="CSRC-004",
            name="数据来源标注",
            description="必须标注数据来源和引用出处",
            regulation_type=RegulationType.CSRC,
            severity=ViolationSeverity.WARNING,
            check_function=self._check_data_source_disclosure,
        ))

        self.register_rule(RegulationRule(
            rule_id="CSRC-005",
            name="免责声明",
            description="必须包含投资风险免责声明",
            regulation_type=RegulationType.CSRC,
            severity=ViolationSeverity.MAJOR,
            check_function=self._check_disclaimer,
        ))

        # 上交所规则
        self.register_rule(RegulationRule(
            rule_id="SSE-001",
            name="股票评级规范",
            description="股票评级必须使用规范用语",
            regulation_type=RegulationType.SSE,
            severity=ViolationSeverity.WARNING,
            check_function=self._check_rating_standard,
        ))

    def register_rule(self, rule: RegulationRule) -> bool:
        """注册规则"""
        self.rules[rule.rule_id] = rule
        return True

    def unregister_rule(self, rule_id: str) -> bool:
        """注销规则"""
        if rule_id in self.rules:
            del self.rules[rule_id]
            return True
        return False

    def get_rules(self, regulation_type: RegulationType = None) -> List[RegulationRule]:
        """获取规则列表"""
        rules = list(self.rules.values())
        if regulation_type:
            rules = [r for r in rules if r.regulation_type == regulation_type]
        return rules

    def check_compliance(self, content: str, context: Dict = None) -> ComplianceCheckResult:
        """
        执行合规检查

        Args:
            content: 要检查的内容
            context: 额外上下文信息

        Returns:
            合规检查结果
        """
        violations = []
        warnings = []
        checked_rules = []

        for rule_id, rule in self.rules.items():
            if not rule.enabled:
                continue

            checked_rules.append(rule_id)

            if rule.check_function:
                try:
                    result = rule.check_function(content, context)
                    if result and not result.get("passed", True):
                        violation = {
                            "rule_id": rule_id,
                            "rule_name": rule.name,
                            "severity": rule.severity.value,
                            "message": result.get("message", "违规"),
                            "details": result.get("details", {}),
                        }
                        violations.append(violation)
                    elif result and result.get("warning"):
                        warnings.append(result["warning"])
                except Exception as e:
                    warnings.append(f"规则 {rule_id} 检查异常: {str(e)[:100]}")

        passed = len(violations) == 0

        return ComplianceCheckResult(
            passed=passed,
            violations=violations,
            warnings=warnings,
            checked_at=datetime.now(),
            rule_ids_checked=checked_rules,
        )

    # ========== 默认规则检查函数 ==========

    def _check_no_promised_returns(self, content: str, context: Dict = None) -> Dict:
        """检查是否承诺收益"""
        import re

        patterns = [
            r"(?i)(保证|确保|承诺).*(收益|回报|盈利|利润)",
            r"(?i)(稳赚|必赚|肯定赚|一定赚)",
            r"(?i)(无风险|零风险|没有风险)",
            r"(?i)(100%|百分之百).*(收益|回报|准确)",
        ]

        for pattern in patterns:
            if re.search(pattern, content):
                return {
                    "passed": False,
                    "message": "检测到承诺收益表述，违反证监会规定",
                    "details": {"pattern": pattern},
                }

        return {"passed": True}

    def _check_no_insider_trading_hints(self, content: str, context: Dict = None) -> Dict:
        """检查是否暗示内幕交易"""
        import re

        patterns = [
            r"(?i)(内部消息|内幕信息|未公开信息)",
            r"(?i)(提前知道|早已得知|知情人士)",
            r"(?i)(独家消息|独家获悉|独家来源)",
        ]

        for pattern in patterns:
            if re.search(pattern, content):
                return {
                    "passed": False,
                    "message": "检测到可能暗示内幕交易的表述",
                    "details": {"pattern": pattern},
                }

        return {"passed": True}

    def _check_conflict_disclosure(self, content: str, context: Dict = None) -> Dict:
        """检查利益冲突披露"""
        import re

        disclosure_patterns = [
            r"(?i)(利益冲突|利害关系|关联关系)",
            r"(?i)(持有.*股份|持仓|投资组合)",
            r"(?i)(承销|保荐|做市)",
        ]

        has_disclosure = any(re.search(p, content) for p in disclosure_patterns)

        # 如果提及相关方但未披露利益冲突
        if context and context.get("mentions_related_parties") and not has_disclosure:
            return {
                "passed": False,
                "message": "提及相关方但未披露利益冲突",
            }

        return {"passed": True}

    def _check_data_source_disclosure(self, content: str, context: Dict = None) -> Dict:
        """检查数据来源标注"""
        import re

        source_patterns = [
            r"(?i)(数据来源|数据出处|引用来源|来源:|出处:)",
            r"(?i)(Tushare|AkShare|Wind|Bloomberg|同花顺)",
            r"(?i)(公司公告|年报|季报|研报)",
        ]

        has_source = any(re.search(p, content) for p in source_patterns)

        # 如果包含数据但未标注来源
        data_patterns = [
            r"\d+\.?\d*%",  # 百分比
            r"\d+\.?\d*亿",  # 金额
            r"增长|下降|同比|环比",  # 变化
        ]

        has_data = any(re.search(p, content) for p in data_patterns)

        if has_data and not has_source:
            return {
                "passed": False,
                "message": "包含数据但未标注数据来源",
            }

        return {"passed": True}

    def _check_disclaimer(self, content: str, context: Dict = None) -> Dict:
        """检查免责声明"""
        import re

        disclaimer_patterns = [
            r"(?i)(免责声明|风险提示|投资有风险)",
            r"(?i)(不构成.*投资建议|仅供参考)",
            r"(?i)(过往业绩.*不代表|历史表现.*不代表)",
        ]

        has_disclaimer = any(re.search(p, content) for p in disclaimer_patterns)

        if not has_disclaimer:
            return {
                "passed": False,
                "message": "缺少投资风险免责声明",
            }

        return {"passed": True}

    def _check_rating_standard(self, content: str, context: Dict = None) -> Dict:
        """检查评级规范"""
        import re

        # 规范评级用语
        standard_ratings = [
            "买入", "增持", "中性", "减持", "卖出",
            "推荐", "谨慎推荐", "优于大市", "同步大市", "弱于大市",
        ]

        # 检查是否有非规范评级
        rating_pattern = r"(?i)(评级|建议|投资建议)[：:\s]*(\S+)"
        matches = re.findall(rating_pattern, content)

        for match in matches:
            rating = match[1]
            if rating not in standard_ratings:
                return {
                    "passed": True,
                    "warning": f"使用了非规范评级用语: {rating}",
                }

        return {"passed": True}
