"""复杂度规则：函数长度、圈复杂度、认知复杂度、参数个数、嵌套深度。

重点：CognitiveComplexityRule 实现了 SonarSource 的认知复杂度算法。

认知复杂度（Cognitive Complexity）vs 圈复杂度（Cyclomatic Complexity）：
- 圈复杂度：数决策点个数，扁平 5 个 if 和嵌套 5 层 if 给同样分数
- 认知复杂度：嵌套越深加越多分，更符合人脑理解难度

算法（来自 SonarSource 白皮书）：
1. 每个控制结构 +1（B1：基础增量）
2. 嵌套时额外 +嵌套深度（B2：嵌套增量）
3. break/continue/goto 跳出结构 +1（B3：控制流惩罚）
4. Boolean 操作符序列：第一个不计，后续每个 and/or 都计 +1，但同层不计嵌套
5. 递归调用自身 +1（B5：递归惩罚）
6. 多行的多个 if 用 elif 连接时不计额外嵌套（平展）

v0.3.0 修正（见 tests/test_engine_hardening.py）：
- elif 属于 if 链，只 +1、不给嵌套惩罚；旧实现把所有 if 都当新嵌套（含 elif）。
- else 分支同理：只 +1，不额外加深嵌套层级。
- BoolOp 只在 visit_BoolOp 里计一次；旧实现先在 if 条件里计一遍，
  generic_visit 时又计一遍，混合 and/or 会重复计数。
- 三元表达式作为嵌套结构计入 B2，嵌套三元才能正确累加。

参考：https://www.sonarsource.com/docs/CognitiveComplexity.pdf
"""

from __future__ import annotations

import ast
from typing import ClassVar

from ..models import Category, Confidence, Severity
from .base import PythonAstRule, RuleContext

# ==================== 共用辅助 ====================

# 触发圈复杂度 +1 的节点类型
_CYCLO_NODES = (
    ast.If,
    ast.For,
    ast.While,
    ast.ExceptHandler,
    ast.With,
    ast.Assert,
    ast.comprehension,  # 列表/字典/集合推导式的 for 子句
)
# 布尔操作符（and / or）每个决策点 +1
_BOOL_OPS = (ast.And, ast.Or)
# 条件表达式（三元）：a if cond else b 也算决策
_CONDITIONAL_EXPR = ast.IfExp if hasattr(ast, "IfExp") else None

# 参与「嵌套深度」统计的节点：仅控制流结构。
# with / async with 属于上下文管理块，不是控制流，按 compute_max_nesting_depth
# 的注释不计入；async for 与同步版本保持一致，避免漏统计。
_NESTING_NODES: tuple[type, ...] = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ExceptHandler,
    ast.Try,
    ast.Match,
)
if hasattr(ast, "TryStar"):  # pragma: no cover - 仅 Python 3.11+ 存在
    _NESTING_NODES += (ast.TryStar,)


# ==================== 认知复杂度核心算法 ====================


class CognitiveComplexityVisitor(ast.NodeVisitor):
    """实现 SonarSource 认知复杂度的 AST visitor。

    算法状态：
    - self.complexity：累计复杂度
    - self._nesting：当前嵌套层级（函数顶层 = 0）
    """

    def __init__(self, func_node: ast.AST) -> None:
        self.func_node = func_node
        self.complexity = 0
        # 嵌套层级：函数顶层 = 0
        self._nesting = 0

    def _nested_score(self) -> int:
        """当前节点的得分 = 1（基础增量）+ 嵌套层级（嵌套增量）。"""
        return 1 + self._nesting

    # ---- 处理嵌套结构 ----

    def _visit_structural(self, node):
        """通用：先给当前结构加分，再进入子节点（子节点嵌套层级 +1）。"""
        self.complexity += self._nested_score()
        self._nesting += 1
        self.generic_visit(node)
        self._nesting -= 1

    def visit_If(self, node):
        """if / elif / else 链：只有 if 计嵌套，elif / else 只加基础增量。"""
        self._visit_if_node(node, is_elif=False)

    def _visit_if_node(self, node, is_elif: bool) -> None:
        """处理 if 链中的一个分支。

        elif 是 if 链的延续，只 +1 且不加深嵌套层级（SonarSource 平展规则），
        否则按 if 结构计分并让 body 的嵌套层级 +1。
        """
        if is_elif:
            self.complexity += 1
        else:
            self.complexity += self._nested_score()
            self._nesting += 1

        # 条件中的 BoolOp 由 visit_BoolOp 统一计数（此处只负责遍历）
        self.visit(node.test)
        for stmt in node.body:
            self.visit(stmt)
        self._visit_else_branch(node.orelse)

        if not is_elif:
            self._nesting -= 1

    def _visit_else_branch(self, orelse: list) -> None:
        """处理 if 的 orelse：elif 递归平展，普通 else 只 +1（无嵌套惩罚）。"""
        if not orelse:
            return
        if len(orelse) == 1 and isinstance(orelse[0], ast.If):
            # elif：同一条 if 链，继续平展处理
            self._visit_if_node(orelse[0], is_elif=True)
            return
        # 普通 else：B1 基础增量 +1，body 已在 if 的嵌套层级内，不再加深
        self.complexity += 1
        for stmt in orelse:
            self.visit(stmt)

    def visit_For(self, node):
        self._visit_structural(node)

    visit_AsyncFor = visit_For

    def visit_While(self, node):
        self._visit_structural(node)

    def visit_ExceptHandler(self, node):
        self._visit_structural(node)

    def visit_Match(self, node):
        """match 是多分支控制流：+1（嵌套增量随层级），case body 位于更深一层。"""
        self._visit_structural(node)

    def visit_With(self, node):
        # with 不增加嵌套（SonarSource 文档：with 单独算）
        # 实际上 Radon 把 with 算作 +1，这里我们也保守 +1 但不加嵌套
        self.complexity += self._nested_score()
        self.generic_visit(node)

    visit_AsyncWith = visit_With

    # ---- 条件表达式和断言 ----

    def visit_IfExp(self, node):
        """三元表达式 a if cond else b：属于 B2 嵌套结构。"""
        self.complexity += self._nested_score()
        self._nesting += 1
        self.generic_visit(node)
        self._nesting -= 1

    def visit_Assert(self, node):
        self.complexity += self._nested_score()
        self.generic_visit(node)

    # ---- break / continue / goto ----

    def visit_Break(self, node):
        # 跳出循环结构 +1
        self.complexity += 1
        self.generic_visit(node)

    def visit_Continue(self, node):
        self.complexity += 1
        self.generic_visit(node)

    # ---- 布尔操作序列 ----

    def visit_BoolOp(self, node):
        """布尔操作序列：同一节点的每个操作符 +1，不计嵌套。

        `len(node.values) - 1` 就是本层的操作符个数；嵌套的不同操作符
        （如 `a and b or c` 中的 or 里再套 and）由 generic_visit 递归到
        各自的 visit_BoolOp 计数，这里不能再递归统计，否则重复计数。
        """
        self.complexity += len(node.values) - 1
        self.generic_visit(node)

    # ---- 递归调用 ----

    def visit_Call(self, node):
        """递归调用自身 +1。"""
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        if func_name and hasattr(self.func_node, "name") and func_name == self.func_node.name:
            self.complexity += 1

        self.generic_visit(node)

    # ---- 跳过嵌套函数定义（避免把内部函数的逻辑算到外层）----

    def visit_FunctionDef(self, node):
        """遇到嵌套的函数定义，单独算（不计入当前函数）。"""
        if node is self.func_node:
            # 这是最外层的目标函数本身
            self.generic_visit(node)
        else:
            # 嵌套函数：在 SonarSource 算法中应该单独评分，跳过它的 body
            # 但简单实现下我们忽略嵌套函数的影响（只算外层控制流）
            pass

    visit_AsyncFunctionDef = visit_FunctionDef


def compute_cognitive_complexity(func_node: ast.AST) -> int:
    """计算单个函数节点的认知复杂度。"""
    visitor = CognitiveComplexityVisitor(func_node)
    # 手动遍历函数体（不进入函数定义本身，避免被 visit_FunctionDef 拦截）
    for stmt in getattr(func_node, "body", []):
        visitor.visit(stmt)
    return visitor.complexity


def compute_cyclomatic_complexity(func_node: ast.AST) -> int:
    """计算单个函数的圈复杂度（基础 1，每个决策点 +1）。

    算法来自 Radon：
    - if / elif: +1
    - for / while: +1
    - except: +1
    - with / assert: +1
    - 推导式: +1
    - and / or: +1
    - 条件表达式: +1
    """
    complexity = 1
    for node in ast.walk(func_node):
        # 跳过嵌套函数（圈复杂度也算独立的）
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node is not func_node:
            continue

        if isinstance(node, _CYCLO_NODES):
            complexity += 1
        elif isinstance(node, ast.BoolOp) and isinstance(node.op, _BOOL_OPS):
            complexity += len(node.values) - 1
        elif _CONDITIONAL_EXPR and isinstance(node, ast.IfExp):
            complexity += 1
    return complexity


def compute_max_nesting_depth(func_node: ast.AST) -> int:
    """计算函数体的最大嵌套深度（仅控制流嵌套）。

    语义（v0.3.0 按本注释修正）：
    - 函数体顶层语句深度为 0；每进入一层控制流结构（if/for/while/try/except）
      深度 +1，即「3 层嵌套 if」= 3、无嵌套函数 = 0。
    - `with` / `async with` 是上下文管理块而非控制流，不计入深度。
    - `async for` 与 `for` 一致统计。
    - 嵌套函数定义单独评分，不并入外层函数。
    - 单次深度优先遍历取最大值：旧实现对同一子节点递归两次
      （`depth(child, current)` 与 `depth(child, current + 1)`），
      深度 n 时调用数达 2^n，大文件会指数级变慢。
    """

    def depth(node, current: int) -> int:
        # 嵌套函数单独评分，不把它的控制流算到外层
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current
        next_depth = current + 1 if isinstance(node, _NESTING_NODES) else current
        max_d = next_depth
        for child in ast.iter_child_nodes(node):
            child_d = depth(child, next_depth)
            if child_d > max_d:
                max_d = child_d
        return max_d

    # 从函数 body 出发：顶层语句深度为 0
    max_depth = 0
    for stmt in getattr(func_node, "body", []):
        d = depth(stmt, 0)
        if d > max_depth:
            max_depth = d
    return max_depth


# ==================== 规则类 ====================


class FunctionLengthRule(PythonAstRule):
    """COMPLEX001: 函数过长。"""

    RULE_ID: ClassVar[str] = "COMPLEX001"
    NAME: ClassVar[str] = "function-too-long"
    DESCRIPTION: ClassVar[str] = "函数不应超过阈值行数"
    CATEGORY: ClassVar[Category] = Category.COMPLEXITY
    DEFAULT_SEVERITY: ClassVar[Severity] = Severity.WARNING

    def visit(self, ctx: RuleContext) -> list:
        # 阈值来自配置（默认 50 行）
        threshold = ctx.threshold("max_function_length", 50)
        issues = []
        for node in ast.walk(ctx.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end_line = node.end_lineno or node.lineno
                func_length = end_line - node.lineno + 1
                if func_length > threshold:
                    issues.append(
                        self.make_issue(
                            line=node.lineno,
                            message=f"函数 `{node.name}` 过长 ({func_length} 行)，建议不超过 {threshold} 行",
                            name=node.name,
                            length=func_length,
                            suggestion="拆分为多个职责单一的小函数",
                        )
                    )
        return issues


class CyclomaticComplexityRule(PythonAstRule):
    """COMPLEX002: 圈复杂度过高（决策点过多）。"""

    RULE_ID: ClassVar[str] = "COMPLEX002"
    NAME: ClassVar[str] = "cyclomatic-complexity"
    DESCRIPTION: ClassVar[str] = "圈复杂度（决策点数）不应过高"
    CATEGORY: ClassVar[Category] = Category.COMPLEXITY
    DEFAULT_SEVERITY: ClassVar[Severity] = Severity.WARNING

    def visit(self, ctx: RuleContext) -> list:
        threshold = ctx.threshold("max_cyclomatic_complexity", 10)
        issues = []
        for node in ast.walk(ctx.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            cc = compute_cyclomatic_complexity(node)
            if cc > threshold:
                issues.append(
                    self.make_issue(
                        line=node.lineno,
                        message=f"函数 `{node.name}` 圈复杂度过高 ({cc})，建议不超过 {threshold}",
                        name=node.name,
                        complexity=cc,
                        complexity_kind="cyclomatic",
                        suggestion="拆分函数或用早返回（early return）减少嵌套",
                    )
                )
        return issues


class CognitiveComplexityRule(PythonAstRule):
    """COMPLEX003: 认知复杂度过高（人脑理解难度）。

    业界已普遍采用认知复杂度取代圈复杂度作为可读性指标。
    SonarSource 默认阈值：15。
    """

    RULE_ID: ClassVar[str] = "COMPLEX003"
    NAME: ClassVar[str] = "cognitive-complexity"
    DESCRIPTION: ClassVar[str] = "认知复杂度（嵌套加权）不应过高"
    CATEGORY: ClassVar[Category] = Category.COMPLEXITY
    DEFAULT_SEVERITY: ClassVar[Severity] = Severity.WARNING

    def visit(self, ctx: RuleContext) -> list:
        threshold = ctx.threshold("max_cognitive_complexity", 15)
        issues = []
        for node in ast.walk(ctx.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            cc = compute_cognitive_complexity(node)
            if cc > threshold:
                issues.append(
                    self.make_issue(
                        line=node.lineno,
                        message=f"函数 `{node.name}` 认知复杂度过高 ({cc})，建议不超过 {threshold}",
                        name=node.name,
                        complexity=cc,
                        complexity_kind="cognitive",
                        confidence=Confidence.HIGH,
                        suggestion="减少嵌套层级、抽取辅助函数、用早返回（early return）",
                    )
                )
        return issues


class ParameterCountRule(PythonAstRule):
    """COMPLEX004: 函数参数过多。"""

    RULE_ID: ClassVar[str] = "COMPLEX004"
    NAME: ClassVar[str] = "too-many-params"
    DESCRIPTION: ClassVar[str] = "函数参数过多"
    CATEGORY: ClassVar[Category] = Category.COMPLEXITY
    DEFAULT_SEVERITY: ClassVar[Severity] = Severity.INFO

    def visit(self, ctx: RuleContext) -> list:
        threshold = ctx.threshold("max_param_count", 5)
        issues = []
        for node in ast.walk(ctx.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # args + posonlyargs + kwonlyargs
            args = list(node.args.args) + list(getattr(node.args, "posonlyargs", []))
            arg_count = len(args) + len(node.args.kwonlyargs)
            if arg_count > threshold:
                issues.append(
                    self.make_issue(
                        line=node.lineno,
                        message=f"函数 `{node.name}` 参数过多 ({arg_count} 个)，建议不超过 {threshold} 个",
                        name=node.name,
                        param_count=arg_count,
                        suggestion="使用数据类、TypedDict 或参数对象封装多个参数",
                    )
                )
        return issues


class NestingDepthRule(PythonAstRule):
    """COMPLEX005: 控制流嵌套过深。"""

    RULE_ID: ClassVar[str] = "COMPLEX005"
    NAME: ClassVar[str] = "nesting-too-deep"
    DESCRIPTION: ClassVar[str] = "控制流嵌套层数过深"
    CATEGORY: ClassVar[Category] = Category.COMPLEXITY
    DEFAULT_SEVERITY: ClassVar[Severity] = Severity.WARNING

    def visit(self, ctx: RuleContext) -> list:
        threshold = ctx.threshold("max_nesting_depth", 4)
        issues = []
        for node in ast.walk(ctx.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            depth = compute_max_nesting_depth(node)
            if depth > threshold:
                issues.append(
                    self.make_issue(
                        line=node.lineno,
                        message=f"函数 `{node.name}` 嵌套过深 ({depth} 层)，建议不超过 {threshold} 层",
                        name=node.name,
                        length=depth,
                        suggestion="用早返回（early return）或抽取嵌套逻辑为独立函数",
                    )
                )
        return issues
