"""规则注册表：集中管理所有规则的元信息与实例。

设计目标：
- analyzer 不直接 import 具体规则，而是从 registry 取已注册规则
- 支持"按类别禁用"和"按规则 ID 禁用"两种粒度（来自 Config）
- 单元测试可临时禁用某条规则，无需改业务代码
"""

from __future__ import annotations

from ..config import Config
from .base import Rule, RuleContext


class RuleRegistry:
    """规则注册表单例。

    使用方式：
        from .registry import registry
        registry.register(MyRule())

    注册的规则会按 (category, rule_id) 索引。
    """

    def __init__(self) -> None:
        self._rules: list[Rule] = []
        self._by_id: dict[str, Rule] = {}

    def register(self, rule: Rule) -> None:
        """注册一条规则。重复 ID 会覆盖并打印警告（开发期防错）。"""
        if rule.RULE_ID in self._by_id:
            # 静默覆盖（在 reload 场景下是正常的）
            old = self._by_id[rule.RULE_ID]
            self._rules.remove(old)
        self._rules.append(rule)
        self._by_id[rule.RULE_ID] = rule

    def all_rules(self) -> list[Rule]:
        """返回所有已注册规则的列表（拷贝）。"""
        return list(self._rules)

    def get(self, rule_id: str) -> Rule | None:
        """按 ID 查找规则。"""
        return self._by_id.get(rule_id.upper())

    def reset(self) -> None:
        """清空注册表（仅用于测试）。"""
        self._rules.clear()
        self._by_id.clear()

    # ====== 运行入口 ======

    def run_all(self, ctx: RuleContext, config: Config | None = None) -> list:
        """运行所有适用规则的过滤流程，返回聚合后的 Issue 列表。

        过滤顺序：
        1. 语言过滤（rule.LANGUAGES 为空或包含 ctx.language）
        2. 类别开关（config.is_category_enabled）
        3. 单条规则运行
        4. 行级豁免（# codereview: ignore）
        """
        from ..config import is_issue_suppressed

        config = config or Config()
        all_issues = []

        for rule in self._rules:
            # 1. 语言过滤
            if rule.LANGUAGES and ctx.language not in rule.LANGUAGES:
                continue

            # 2. 类别开关
            category_name = (
                rule.CATEGORY.value if hasattr(rule.CATEGORY, "value") else str(rule.CATEGORY)
            )
            if not config.is_category_enabled(category_name):
                continue

            # 3. 运行规则
            try:
                issues = rule.check(ctx) or []
            except Exception:
                # 单条规则异常不应阻塞其他规则
                continue

            # 4. 行级豁免过滤
            for issue in issues:
                if is_issue_suppressed(ctx.source_lines, issue.line, issue.type):
                    continue
                all_issues.append(issue)

        return all_issues


# 全局单例
registry = RuleRegistry()


def register_all_default_rules() -> None:
    """注册所有内置规则（在 import 时调用一次）。

    放在独立函数里是为了：
    1. 避免 import 时副作用，便于测试控制
    2. 调用方可决定何时注册（如自定义规则需要插入到中间）
    """
    # 延迟 import 避免循环依赖
    from . import complexity, security, style  # noqa: F401

    # 复杂度规则
    registry.register(complexity.FunctionLengthRule())
    registry.register(complexity.CyclomaticComplexityRule())
    registry.register(complexity.CognitiveComplexityRule())
    registry.register(complexity.ParameterCountRule())
    registry.register(complexity.NestingDepthRule())

    # 安全规则
    for rule_cls in security.SECURITY_RULES:
        registry.register(rule_cls())

    # 风格规则
    registry.register(style.LineLengthRule())
    registry.register(style.TodoCommentRule())
    registry.register(style.TrailingWhitespaceRule())
    registry.register(style.PythonDebugPrintRule())
    registry.register(style.JsConsoleLogRule())


# 自动注册：当 import 本模块时即完成注册
register_all_default_rules()
