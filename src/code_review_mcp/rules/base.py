"""规则引擎基类：所有规则的统一抽象。

设计参考：
- bandit 的 AST 节点类型分发（@checks("Call")）
- ESLint 的 visitor 模式
- ruff 的 Rule 类 + fix 建议

每条规则都是 PythonAstRule 或 TextRule 的子类，注册到 registry 后会被 analyzer 自动调用。
"""

from __future__ import annotations

import ast
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from ..models import Category, Confidence, Issue, Severity


@dataclass
class RuleContext:
    """规则运行时的上下文，避免每条规则都重复解析。"""

    source: str  # 原始源码
    source_lines: list[str]  # 按行切分（source.splitlines 已处理跨平台）
    tree: ast.AST | None  # Python AST（仅在语言为 python 且解析成功时）
    language: str  # 语言名（小写）
    file_path: str  # 文件路径（用于报告）


class Rule(ABC):
    """所有规则的基类。

    子类必须：
    1. 设置类属性 RULE_ID（唯一标识，如 SEC001）、CATEGORY、DEFAULT_SEVERITY
    2. 实现 check(context) -> list[Issue]
    """

    # 类属性（子类覆盖）
    RULE_ID: ClassVar[str] = ""
    NAME: ClassVar[str] = ""  # 规则简短名称
    DESCRIPTION: ClassVar[str] = ""  # 一句话描述
    CATEGORY: ClassVar[Category] = Category.STYLE
    DEFAULT_SEVERITY: ClassVar[Severity] = Severity.INFO
    LANGUAGES: ClassVar[tuple[str, ...]] = ()  # 空表示所有语言都跑

    @abstractmethod
    def check(self, ctx: RuleContext) -> list[Issue]:
        """执行检查，返回 issue 列表（空列表表示无问题）。"""

    # ---- 工具方法 ----

    def make_issue(
        self,
        *,
        line: int,
        message: str,
        confidence: Confidence = Confidence.HIGH,
        **extra,
    ) -> Issue:
        """构造本规则对应的 Issue（自动填入 RULE_ID/CATEGORY/severity）。"""
        return Issue(
            type=self.RULE_ID,
            message=message,
            line=line,
            severity=self.DEFAULT_SEVERITY,
            category=self.CATEGORY,
            confidence=confidence,
            **extra,
        )


class PythonAstRule(Rule):
    """Python AST 规则基类：自动跳过非 Python 文件和 AST 解析失败的文件。

    子类实现 visit(ctx) 直接使用 ctx.tree，无需重复样板代码。
    """

    LANGUAGES: ClassVar[tuple[str, ...]] = ("python",)

    def check(self, ctx: RuleContext) -> list[Issue]:
        if ctx.language != "python" or ctx.tree is None:
            return []
        return self.visit(ctx)

    @abstractmethod
    def visit(self, ctx: RuleContext) -> list[Issue]:
        """在已确认是 Python 且 tree 存在时执行。"""


class TextRule(Rule):
    """文本规则基类：对源码逐行扫描，不依赖 AST。

    适用于跨语言的风格检查（行长、TODO、末尾空白、调试 print 等）。
    """

    def check(self, ctx: RuleContext) -> list[Issue]:
        issues: list[Issue] = []
        for i, line in enumerate(ctx.source_lines, 1):
            issues.extend(self.check_line(line, i, ctx))
        return issues

    def check_line(self, line: str, line_no: int, ctx: RuleContext) -> list[Issue]:
        """单行检查，默认返回空。子类按需覆盖。"""
        return []


# ==================== AST 遍历辅助 ====================


def get_call_name(node: ast.Call) -> str:
    """提取 Call 节点的完整函数名（如 subprocess.Popen / hashlib.md5 / eval）。

    - `subprocess.Popen(...)` → "subprocess.Popen"
    - `hashlib.md5(...)` → "hashlib.md5"
    - `eval(...)` → "eval"
    - `obj.method(...)` → "obj.method"
    """
    func = node.func
    parts: list[str] = []

    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value

    if isinstance(func, ast.Name):
        parts.append(func.id)
    else:
        # 例如 obj.method().method2() 这种链式调用，前面已经收集了 attr，再补一个占位
        parts.append("<expr>")

    parts.reverse()
    return ".".join(parts)


def get_keyword_value(call: ast.Call, kwarg_name: str):
    """从 Call 节点提取关键字参数的值（ast 节点形式）。

    返回 None 表示未找到或不是简单字面量。
    """
    for kw in call.keywords:
        if kw.arg == kwarg_name:
            return kw.value
    return None


def is_const_true(node: ast.AST) -> bool:
    """判断 AST 节点是否是 True 字面量。"""
    return isinstance(node, ast.Constant) and node.value is True


def is_const_str(node: ast.AST) -> bool:
    """判断 AST 节点是否是字符串字面量。"""
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def unwrap_string(value) -> str | None:
    """从 AST 节点或字符串解出字符串值。"""
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    if isinstance(value, str):
        return value
    return None
