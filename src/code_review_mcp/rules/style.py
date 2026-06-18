"""风格规则：跨语言的代码风格检查。

包含：
- LineLengthRule：单行过长（STYLE001）
- TodoCommentRule：注释中的 TODO/FIXME/HACK/XXX（STYLE002）
- TrailingWhitespaceRule：行末空白（STYLE003）
- PythonDebugPrintRule：Python 的 print() 残留（STYLE004，AST 精确匹配）
- JsConsoleLogRule：JS/TS 的 console.log() 残留（STYLE005，严格正则）

v0.2.1 修复：
- STYLE004 拆分为 Python（AST）和 JS（正则）两条规则
- Python 用 AST 检测 Call(func=Name(id='print'))，
  避免对字符串字面量 'print(' 误报（自审发现的真实 bug）
"""

from __future__ import annotations

import ast
import re
from typing import ClassVar

from ..models import Category, Confidence, Severity
from ..utils import detect_todo_tag
from .base import PythonAstRule, RuleContext, TextRule


class LineLengthRule(TextRule):
    """STYLE001: 单行过长。"""

    RULE_ID: ClassVar[str] = "STYLE001"
    NAME: ClassVar[str] = "line-too-long"
    DESCRIPTION: ClassVar[str] = "单行不应超过阈值字符"
    CATEGORY: ClassVar[Category] = Category.STYLE
    DEFAULT_SEVERITY: ClassVar[Severity] = Severity.WARNING

    def check_line(self, line: str, line_no: int, ctx: RuleContext) -> list:
        threshold = 120
        stripped = line.rstrip("\r\n")
        if len(stripped) > threshold:
            return [
                self.make_issue(
                    line=line_no,
                    message=f"第 {line_no} 行过长 ({len(stripped)} 字符)，建议不超过 {threshold}",
                    length=len(stripped),
                    content=stripped[:80] + ("..." if len(stripped) > 80 else ""),
                    suggestion="拆分为多行或使用更具描述性的变量名",
                )
            ]
        return []


class TodoCommentRule(TextRule):
    """STYLE002: 注释行中的 TODO/FIXME/HACK/XXX 标记。

    只检测注释行（# 或 // 或 /*），避免误匹配字符串字面量。
    """

    RULE_ID: ClassVar[str] = "STYLE002"
    NAME: ClassVar[str] = "todo-comment"
    DESCRIPTION: ClassVar[str] = "注释中包含 TODO 类标记"
    CATEGORY: ClassVar[Category] = Category.STYLE
    DEFAULT_SEVERITY: ClassVar[Severity] = Severity.INFO

    def check_line(self, line: str, line_no: int, ctx: RuleContext) -> list:
        stripped = line.strip()
        if not (stripped.startswith("#") or stripped.startswith("//") or stripped.startswith("/*")):
            return []
        tag = detect_todo_tag(stripped)
        if tag:
            return [
                self.make_issue(
                    line=line_no,
                    message=f"第 {line_no} 行包含 {tag} 标记",
                    tag=tag,
                    content=stripped,
                    suggestion="如果是临时占位，创建 issue 跟踪并尽快处理",
                )
            ]
        return []


class TrailingWhitespaceRule(TextRule):
    """STYLE003: 行末有多余空白字符。

    跨语言通用，但针对 Python/JS/TS 报告（其他语言风格不一）。
    """

    RULE_ID: ClassVar[str] = "STYLE003"
    NAME: ClassVar[str] = "trailing-whitespace"
    DESCRIPTION: ClassVar[str] = "行末不应有多余空白字符"
    CATEGORY: ClassVar[Category] = Category.STYLE
    DEFAULT_SEVERITY: ClassVar[Severity] = Severity.INFO

    def check_line(self, line: str, line_no: int, ctx: RuleContext) -> list:
        if ctx.language not in ("python", "javascript", "typescript"):
            return []
        normalized = line.rstrip("\r\n")
        if normalized != normalized.rstrip():
            return [
                self.make_issue(
                    line=line_no,
                    message=f"第 {line_no} 行末尾有多余空白字符",
                    confidence=Confidence.HIGH,
                    suggestion="编辑器开启 'trim trailing whitespace' 选项",
                )
            ]
        return []


class PythonDebugPrintRule(PythonAstRule):
    """STYLE004: Python 生产代码中的 print() 调用残留。

    v0.2.1 升级：改用 AST 精确检测 Call(func=Name(id='print'))，避免：
    - 字符串字面量 'print(' 误报（如 msg = "print this"）
    - 检查字符串是否包含 'print(' 误报（如 if "print(" in line）
    - 注释里的 print 误报（如 # use print() for debug，由 STYLE002 等处理）

    借鉴 bandit 的 AST 节点分发模式。
    """

    RULE_ID: ClassVar[str] = "STYLE004"
    NAME: ClassVar[str] = "debug-print"
    DESCRIPTION: ClassVar[str] = "可能遗留的调试 print 语句"
    CATEGORY: ClassVar[Category] = Category.DEBUG_CODE
    DEFAULT_SEVERITY: ClassVar[Severity] = Severity.INFO
    LANGUAGES: ClassVar[tuple[str, ...]] = ("python",)

    def visit(self, ctx: RuleContext) -> list:
        issues = []
        for node in ast.walk(ctx.tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # 直接的 print(...) 调用（顶层函数名）
            if isinstance(func, ast.Name) and func.id == "print":
                issues.append(
                    self.make_issue(
                        line=node.lineno,
                        message="可能遗留的调试 print 语句",
                        confidence=Confidence.MEDIUM,
                        suggestion="使用 logging 模块替代，便于控制日志级别",
                    )
                )
        return issues


class JsConsoleLogRule(TextRule):
    """STYLE005: JS/TS 生产代码中的 console.log() 调用残留。

    用更严格的正则：要求 console.log 后跟 ( ，且不在注释行里。
    """

    RULE_ID: ClassVar[str] = "STYLE005"
    NAME: ClassVar[str] = "debug-console-log"
    DESCRIPTION: ClassVar[str] = "可能遗留的调试 console.log 语句"
    CATEGORY: ClassVar[Category] = Category.DEBUG_CODE
    DEFAULT_SEVERITY: ClassVar[Severity] = Severity.INFO
    LANGUAGES: ClassVar[tuple[str, ...]] = ("javascript", "typescript")

    # 严格匹配：console.log( 后跟非空白字符（避免 console.logs 这种变量名误报）
    _PATTERN = re.compile(r"\bconsole\.log\s*\(")

    def check_line(self, line: str, line_no: int, ctx: RuleContext) -> list:
        stripped = line.strip()
        # 跳过注释行
        if stripped.startswith(("//", "/*", "*")):
            return []
        if self._PATTERN.search(stripped):
            return [
                self.make_issue(
                    line=line_no,
                    message="可能遗留的调试 console.log 语句",
                    confidence=Confidence.MEDIUM,
                    content=stripped,
                    suggestion="使用专业的 logger 或移除调试代码",
                )
            ]
        return []
