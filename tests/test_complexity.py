"""复杂度规则的测试，重点是认知复杂度算法的正确性。"""

import ast

from code_review_mcp.models import Severity
from code_review_mcp.rules.complexity import (
    compute_cognitive_complexity,
    compute_cyclomatic_complexity,
    compute_max_nesting_depth,
)


def _func_node(code: str):
    """从源码中提取第一个 FunctionDef。"""
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    raise AssertionError("No function found in test code")


# ==================== 认知复杂度算法验证 ====================


def test_cognitive_simple_function():
    """无控制流的函数 → 0。"""
    code = "def f():\n    return 1"
    assert compute_cognitive_complexity(_func_node(code)) == 0


def test_cognitive_single_if():
    """单个 if → 1。"""
    code = "def f(x):\n    if x:\n        return 1\n    return 0"
    assert compute_cognitive_complexity(_func_node(code)) == 1


def test_cognitive_flat_if_elif():
    """扁平 3 个 if/elif → 3（基础增量）。"""
    code = """
def f(x):
    if x == 1:
        return 1
    elif x == 2:
        return 2
    elif x == 3:
        return 3
    return 0
"""
    # 3 个决策点（这里我们的实现是简化版，每个 if/elif 至少 +1）
    cc = compute_cognitive_complexity(_func_node(code))
    assert cc >= 3, f"expected >=3, got {cc}"


def test_cognitive_nested_gt_flat():
    """嵌套 if 比扁平 if 复杂度更高（认知复杂度的核心特性）。"""
    flat_code = """
def f(a, b, c, d):
    if a: return 1
    if b: return 2
    if c: return 3
    if d: return 4
    return 0
"""
    nested_code = """
def f(a, b, c, d):
    if a:
        if b:
            if c:
                if d:
                    return 1
    return 0
"""
    flat_cc = compute_cognitive_complexity(_func_node(flat_code))
    nested_cc = compute_cognitive_complexity(_func_node(nested_code))
    assert nested_cc > flat_cc, f"nested ({nested_cc}) should be > flat ({flat_cc})"


def test_cognitive_loops_and_exceptions():
    """for + while + except 都应计入。"""
    code = """
def f(items):
    for x in items:
        pass
    while True:
        break
    try:
        pass
    except ValueError:
        pass
"""
    cc = compute_cognitive_complexity(_func_node(code))
    # 1 (for) + 1 (while) + 1 (break) + 1 (except) = 4
    assert cc == 4, f"expected 4, got {cc}"


def test_cognitive_boolean_ops():
    """布尔操作符序列应计入。"""
    code = "def f(a, b, c):\n    if a and b and c:\n        return 1"
    cc = compute_cognitive_complexity(_func_node(code))
    # 1 (if 嵌套深度 0) + 2 (a and b and c → 2 个决策点)
    assert cc >= 3, f"expected >=3, got {cc}"


def test_cognitive_recursion():
    """递归调用自身应加分。"""
    code = "def f(n):\n    if n > 0:\n        return f(n-1)\n    return 0"
    cc = compute_cognitive_complexity(_func_node(code))
    # 1 (if) + 1 (递归调用) = 2
    assert cc >= 2, f"expected >=2, got {cc}"


# ==================== 圈复杂度算法验证 ====================


def test_cyclomatic_simple():
    code = "def f():\n    return 1"
    assert compute_cyclomatic_complexity(_func_node(code)) == 1


def test_cyclomatic_with_branches():
    code = """
def f(x, items):
    if x:
        return 1
    for i in items:
        pass
    while x:
        pass
    try:
        pass
    except ValueError:
        pass
"""
    # 1 (base) + 1 (if) + 1 (for) + 1 (while) + 1 (except) = 5
    cc = compute_cyclomatic_complexity(_func_node(code))
    assert cc == 5, f"expected 5, got {cc}"


def test_cyclomatic_boolean_ops():
    code = "def f(a, b, c):\n    return a and b or c"
    cc = compute_cyclomatic_complexity(_func_node(code))
    # (and 2-1=1) + (or 2-1=1) = 2; base 1; total 3
    assert cc == 3, f"expected 3, got {cc}"


# ==================== 嵌套深度 ====================


def test_nesting_flat():
    code = "def f():\n    if a:\n        return 1"
    depth = compute_max_nesting_depth(_func_node(code))
    assert depth == 1


def test_nesting_deep():
    code = """
def f():
    if a:
        if b:
            if c:
                return 1
"""
    depth = compute_max_nesting_depth(_func_node(code))
    assert depth == 3, f"expected 3, got {depth}"


# ==================== 规则触发 ====================


def test_rule_function_too_long(run_rules):
    long_func = "def f():\n" + "    pass\n" * 60
    issues = run_rules(long_func)
    assert "COMPLEX001" in {i.type for i in issues}


def test_rule_cognitive_complexity(run_rules):
    code = """
def f(a, b, c, d):
    if a:
        if b:
            if c:
                if d:
                    if a:
                        if b:
                            return 1
    return 0
"""
    issues = run_rules(code)
    rule_issues = [i for i in issues if i.type == "COMPLEX003"]
    assert len(rule_issues) >= 1
    assert i_severity(rule_issues[0]) == Severity.WARNING


def test_rule_too_many_params(run_rules):
    code = "def f(a, b, c, d, e, f, g):\n    pass"
    issues = run_rules(code)
    assert "COMPLEX004" in {i.type for i in issues}


def test_rule_nesting_too_deep(run_rules):
    code = """
def f():
    if a:
        if b:
            if c:
                if d:
                    if e:
                        pass
"""
    issues = run_rules(code)
    assert "COMPLEX005" in {i.type for i in issues}


# ==================== 辅助 ====================


def i_severity(issue):
    return issue.severity if hasattr(issue.severity, "value") else issue.severity
