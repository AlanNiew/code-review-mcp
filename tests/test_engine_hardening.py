"""引擎硬化（v0.3.0）回归测试。

覆盖契约 §2 的六项改造：
1. RuleContext.config / threshold，以及配置阈值真正生效（含 CODE_REVIEW_CONFIG 链路）
2. 认知复杂度：elif 不嵌套只 +1、else 增量、BoolOp 不重复计数
3. 嵌套深度：单次遍历、with/async with 不计、async for 统计
4. SEC005：支持 AnnAssign 与调用关键字参数
5. registry.run_all：按 (line, rule_id) 去重与稳定排序，行级豁免仍生效
6. utils 与 config 的行级豁免解析收敛为单一实现
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from code_review_mcp import server
from code_review_mcp.analyzers import context as context_module
from code_review_mcp.analyzers.context import analyze_file_content, build_context
from code_review_mcp.config import Config, get_line_ignored_rules, is_issue_suppressed
from code_review_mcp.models import Category, Issue, Severity
from code_review_mcp.rules.base import Rule, RuleContext
from code_review_mcp.rules.complexity import (
    compute_cognitive_complexity,
    compute_max_nesting_depth,
)
from code_review_mcp.rules.registry import RuleRegistry
from code_review_mcp.utils import get_ignored_rule_ids, is_ignored_line

# ==================== 公共辅助 ====================


def _analyze(source: str, config: Config | None = None) -> dict:
    """用指定配置分析 Python 源码，返回 analyze_file_content 的结果。"""
    return analyze_file_content(source, "test.py", config=config)


def _issue_ids(result: dict) -> set[str]:
    """从 analyze_file_content 的返回字典里取 issue 类型集合。"""
    return {issue["type"] for issue in result["issues"]}


def _rule_ids(issues: list[Issue]) -> set[str]:
    """从 registry 返回的 Issue 对象列表里取类型集合。"""
    return {issue.type for issue in issues}


def _func_node(code: str):
    """从源码中提取第一个函数节点。"""
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    raise AssertionError("测试代码里没有函数定义")


# ==================== 1. RuleContext.config / threshold ====================


def test_build_context_stores_config_and_reads_threshold():
    cfg = Config(thresholds={"max_line_length": 42})
    ctx = build_context("x = 1\n", "test.py", "python", cfg)
    assert ctx.config is cfg
    assert ctx.threshold("max_line_length", 120) == 42
    # 配置里没有该键时，使用调用方传入的默认值
    assert ctx.threshold("max_cyclomatic_complexity", 7) == 7


def test_build_context_without_config_uses_default():
    ctx = build_context("x = 1\n", "test.py", "python")
    assert ctx.config is None
    assert ctx.threshold("max_line_length", 120) == 120
    assert ctx.threshold("max_line_length", 55) == 55


def test_analyze_file_content_passes_config_into_context(monkeypatch):
    """analyze_file_content 必须把它已有的 config 透传给 build_context。"""
    seen: list[Config | None] = []
    original = context_module.build_context

    def spy(source, file_path, language=None, config=None):
        seen.append(config)
        return original(source, file_path, language, config)

    monkeypatch.setattr(context_module, "build_context", spy)
    cfg = Config(thresholds={"max_line_length": 20})
    analyze_file_content("x = 1\n", "test.py", config=cfg)
    assert len(seen) == 1
    assert seen[0] is cfg


# ==================== 2. 配置阈值真正生效 ====================

_LONG_FUNC = "def long_func():\n" + "".join(f"    value_{i} = {i}\n" for i in range(7))
_BRANCHY = """\
def branchy(a, b):
    if a:
        return 1
    if b:
        return 2
    return 0
"""
_NESTED_IF = """\
def nested(a, b):
    if a:
        if b:
            return 1
    return 0
"""
_LONG_LINE = "x = '" + "a" * 40 + "'\n"


def test_threshold_complex001_function_length():
    # 8 行函数：默认阈值 50 不报，收紧到 5 才报
    assert "COMPLEX001" not in _issue_ids(_analyze(_LONG_FUNC, Config()))
    result = _analyze(_LONG_FUNC, Config(thresholds={"max_function_length": 5}))
    assert "COMPLEX001" in _issue_ids(result)


def test_threshold_complex002_cyclomatic():
    # 圈复杂度 = 1 + 2 个 if = 3
    assert "COMPLEX002" not in _issue_ids(_analyze(_BRANCHY, Config()))
    result = _analyze(_BRANCHY, Config(thresholds={"max_cyclomatic_complexity": 2}))
    assert "COMPLEX002" in _issue_ids(result)


def test_threshold_complex003_cognitive():
    # 认知复杂度 = 外层 if 1 + 内层 if 2 = 3
    assert "COMPLEX003" not in _issue_ids(_analyze(_NESTED_IF, Config()))
    result = _analyze(_NESTED_IF, Config(thresholds={"max_cognitive_complexity": 1}))
    assert "COMPLEX003" in _issue_ids(result)


def test_threshold_complex004_param_count():
    source = "def three(a, b, c):\n    return a + b + c\n"
    assert "COMPLEX004" not in _issue_ids(_analyze(source, Config()))
    result = _analyze(source, Config(thresholds={"max_param_count": 2}))
    assert "COMPLEX004" in _issue_ids(result)


def test_threshold_complex005_nesting_depth():
    # 2 层嵌套：默认阈值 4 不报，收紧到 1 才报
    assert "COMPLEX005" not in _issue_ids(_analyze(_NESTED_IF, Config()))
    result = _analyze(_NESTED_IF, Config(thresholds={"max_nesting_depth": 1}))
    assert "COMPLEX005" in _issue_ids(result)


def test_threshold_style001_line_length():
    # 行长 46：默认阈值 120 不报，收紧到 20 才报
    assert "STYLE001" not in _issue_ids(_analyze(_LONG_LINE, Config()))
    result = _analyze(_LONG_LINE, Config(thresholds={"max_line_length": 20}))
    assert "STYLE001" in _issue_ids(result)


# ==================== 3. 配置链路：环境变量 + MCP 工具 ====================


def test_env_config_threshold_applied(tmp_path: Path, monkeypatch):
    """CODE_REVIEW_CONFIG 指向的配置应能影响 analyze_file_content 的默认加载。"""
    config_path = tmp_path / "review.json"
    config_path.write_text(json.dumps({"thresholds": {"max_line_length": 20}}), encoding="utf-8")
    monkeypatch.setenv("CODE_REVIEW_CONFIG", str(config_path))
    result = analyze_file_content(_LONG_LINE, "test.py")
    assert "STYLE001" in _issue_ids(result)


def test_env_config_applied_through_analyze_file_tool(tmp_path: Path, monkeypatch):
    """server.analyze_file → load_config → analyze_file_content 全链路生效。"""
    config_path = tmp_path / "review.json"
    config_path.write_text(json.dumps({"thresholds": {"max_line_length": 20}}), encoding="utf-8")
    monkeypatch.setenv("CODE_REVIEW_CONFIG", str(config_path))
    target = tmp_path / "sample.py"
    target.write_text(_LONG_LINE, encoding="utf-8")
    result = server.analyze_file(str(target))
    assert "STYLE001" in _issue_ids(result)


def test_project_config_file_applied_through_analyze_file_tool(tmp_path: Path, monkeypatch):
    """项目内的 .code-review.json 也应被 analyze_file 自动发现。"""
    # 排除外部环境变量干扰，确保走「向上查找配置文件」这条路径
    monkeypatch.delenv("CODE_REVIEW_CONFIG", raising=False)
    (tmp_path / ".code-review.json").write_text(
        json.dumps({"thresholds": {"max_line_length": 20}}), encoding="utf-8"
    )
    target = tmp_path / "sample.py"
    target.write_text(_LONG_LINE, encoding="utf-8")
    result = server.analyze_file(str(target))
    assert "STYLE001" in _issue_ids(result)


# ==================== 4. 认知复杂度：elif / else / BoolOp ====================


def test_cognitive_elif_chain_no_nesting_penalty():
    """if/elif/elif 三个决策点各 +1，elif 不额外叠加嵌套惩罚。"""
    code = """\
def f(x):
    if x == 1:
        pass
    elif x == 2:
        pass
    elif x == 3:
        pass
"""
    assert compute_cognitive_complexity(_func_node(code)) == 3

    two_branch = """\
def f(x):
    if x == 1:
        pass
    elif x == 2:
        pass
"""
    assert compute_cognitive_complexity(_func_node(two_branch)) == 2


def test_cognitive_nested_elif_not_penalized():
    """外层 if 1 + 内层 if 2（嵌套惩罚）+ elif 1（无嵌套惩罚）= 4。"""
    code = """\
def f(a, b, c):
    if a:
        if b:
            pass
        elif c:
            pass
"""
    assert compute_cognitive_complexity(_func_node(code)) == 4


def test_cognitive_else_branch_counts():
    code = """\
def f(x):
    if x:
        pass
    else:
        pass
"""
    assert compute_cognitive_complexity(_func_node(code)) == 2

    with_elif = """\
def f(x):
    if x == 1:
        pass
    elif x == 2:
        pass
    else:
        pass
"""
    assert compute_cognitive_complexity(_func_node(with_elif)) == 3


def test_cognitive_boolop_counted_once():
    """布尔序列只按 values-1 计数，不能同时被 visit_If 与 visit_BoolOp 双计。"""
    sequential = "def f(a, b, c):\n    if a and b and c:\n        pass\n"
    # if 1 + (and 序列 2) = 3
    assert compute_cognitive_complexity(_func_node(sequential)) == 3

    mixed = "def f(a, b, c):\n    if (a and b) or c:\n        pass\n"
    # if 1 + or 1 + and 1 = 3
    assert compute_cognitive_complexity(_func_node(mixed)) == 3


# ==================== 5. 嵌套深度：单次遍历与 with 语义 ====================


def test_nesting_depth_counts_control_flow():
    flat = "def f():\n    if a:\n        pass\n"
    assert compute_max_nesting_depth(_func_node(flat)) == 1

    deep = "def f():\n    if a:\n        if b:\n            if c:\n                pass\n"
    assert compute_max_nesting_depth(_func_node(deep)) == 3

    # async for 与同步 for 一致，需要计入
    async_for = "async def f(xs):\n    async for x in xs:\n        if a:\n            pass\n"
    assert compute_max_nesting_depth(_func_node(async_for)) == 2


def test_nesting_depth_ignores_with_blocks():
    """with/async with 属于上下文管理块，不计入控制流嵌套。"""
    with_code = "def f(p):\n    with open(p) as fh:\n        if a:\n            pass\n"
    assert compute_max_nesting_depth(_func_node(with_code)) == 1

    async_with = "async def f(p):\n    async with open(p) as fh:\n        if a:\n            pass\n"
    assert compute_max_nesting_depth(_func_node(async_with)) == 1


def test_nesting_depth_no_control_flow_is_zero():
    assert compute_max_nesting_depth(_func_node("def f():\n    return 1\n")) == 0


def test_nesting_depth_single_traversal(monkeypatch):
    """旧实现会对同一子节点递归两次（指数级）；这里断言遍历次数与节点数同阶。"""
    calls = 0
    original = ast.iter_child_nodes

    def counting_iter_child_nodes(node):
        nonlocal calls
        calls += 1
        return original(node)

    monkeypatch.setattr(ast, "iter_child_nodes", counting_iter_child_nodes)

    lines = ["def f():"]
    for level in range(10):
        lines.append("    " * (level + 1) + "if a:")
    lines.append("    " * 11 + "pass")
    node = _func_node("\n".join(lines) + "\n")

    assert compute_max_nesting_depth(node) == 10
    # 10 层嵌套约十余人节点；旧实现的重复递归需要 2^10 量级
    assert calls <= 40


# ==================== 6. SEC005：AnnAssign 与调用关键字参数 ====================


def test_sec005_annassign_detected(run_rules):
    issues = run_rules('token: str = "abcdef123456"\n')
    assert "SEC005" in _rule_ids(issues)


def test_sec005_call_keyword_detected(run_rules):
    issues = run_rules('connect(password="hunter2")\n')
    assert "SEC005" in _rule_ids(issues)


def test_sec005_call_keyword_non_literal_safe(run_rules):
    code = "import os\ndb.connect(password=os.environ['DB_PASS'])\n"
    assert "SEC005" not in _rule_ids(run_rules(code))


def test_sec005_call_keyword_placeholder_safe(run_rules):
    issues = run_rules('client.connect(api_key="<your-api-key>")\n')
    assert "SEC005" not in _rule_ids(issues)


def test_sec012_weak_random_still_detected(run_rules):
    """清理 dead code 后 SEC012 的检测行为保持不变。"""
    issues = run_rules("import random\ntoken = random.randint(0, 999999)\n")
    assert "SEC012" in _rule_ids(issues)
    safe = run_rules("import random\nx = random.randint(0, 100)\n")
    assert "SEC012" not in _rule_ids(safe)


# ==================== 7. registry.run_all：去重 / 排序 / 豁免 ====================

_FIVE_LINES = "a = 1\nb = 2\nc = 3\nd = 4\ne = 5\n"


class _DuplicateIssueRule(Rule):
    """测试规则：同一行输出多条消息不同的 issue，并用乱序行号验证排序。"""

    RULE_ID = "TEST001"
    NAME = "测试重复输出"
    DESCRIPTION = "用于 registry 去重与排序测试"
    CATEGORY = Category.STYLE
    DEFAULT_SEVERITY = Severity.INFO

    def check(self, ctx: RuleContext) -> list[Issue]:
        return [
            self.make_issue(line=5, message="第五行重复 A"),
            self.make_issue(line=5, message="第五行重复 B"),
            self.make_issue(line=2, message="第二行 A"),
            self.make_issue(line=2, message="第二行 B"),
        ]


class _OtherRule(Rule):
    """测试规则：在第二行输出另一条规则 ID，用于验证 (line, rule_id) 排序。"""

    RULE_ID = "TEST002"
    NAME = "测试另一条规则"
    DESCRIPTION = "用于 registry 排序测试"
    CATEGORY = Category.STYLE
    DEFAULT_SEVERITY = Severity.INFO

    def check(self, ctx: RuleContext) -> list[Issue]:
        return [self.make_issue(line=2, message="第二行的另一条规则")]


class _ExactDuplicateRule(Rule):
    """测试规则：同一行输出两条一模一样的 issue，用于验证完全去重。"""

    RULE_ID = "TEST003"
    NAME = "测试完全相同输出"
    DESCRIPTION = "用于 registry 完全去重测试"
    CATEGORY = Category.STYLE
    DEFAULT_SEVERITY = Severity.INFO

    def check(self, ctx: RuleContext) -> list[Issue]:
        return [self.make_issue(line=2, message="完全相同的重复输出")] * 2


def _isolated_registry(*rules: Rule) -> RuleRegistry:
    """构造独立 registry，避免污染全局单例。"""
    reg = RuleRegistry()
    for rule in rules:
        reg.register(rule)
    return reg


def test_registry_dedupes_only_exact_duplicates_and_sorts():
    reg = _isolated_registry(_DuplicateIssueRule(), _OtherRule())
    issues = reg.run_all(build_context(_FIVE_LINES, "test.py", config=Config()))
    # 同行同规则但消息不同 → 都保留；排序按 (line, rule_id)，同键保持原始顺序
    assert [(issue.line, issue.type) for issue in issues] == [
        (2, "TEST001"),
        (2, "TEST001"),
        (2, "TEST002"),
        (5, "TEST001"),
        (5, "TEST001"),
    ]


def test_registry_collapses_identical_issues():
    """完全相同的重复输出只保留一条。"""
    reg = _isolated_registry(_ExactDuplicateRule())
    issues = reg.run_all(build_context(_FIVE_LINES, "test.py", config=Config()))
    assert [(issue.line, issue.type, issue.message) for issue in issues] == [
        (2, "TEST003", "完全相同的重复输出"),
    ]


def test_registry_line_suppression_still_applies():
    reg = _isolated_registry(_DuplicateIssueRule())
    source = "a = 1\nb = 2\nc = 3\nd = 4\ne = 5  # codereview: ignore=TEST001\n"
    issues = reg.run_all(build_context(source, "test.py", config=Config()))
    # 第 5 行被豁免；第 2 行的两条不同消息都保留
    assert [(issue.line, issue.type) for issue in issues] == [(2, "TEST001"), (2, "TEST001")]


def test_registry_uses_context_config_as_fallback():
    """未显式传 config 时，run_all 应回退到 ctx.config。"""
    reg = _isolated_registry(_DuplicateIssueRule())
    cfg = Config(rules={"style": {"enabled": False}})
    ctx = build_context(_FIVE_LINES, "test.py", config=cfg)
    assert reg.run_all(ctx) == []


def test_registry_explicit_config_overrides_context_config():
    reg = _isolated_registry(_DuplicateIssueRule())
    ctx = build_context(_FIVE_LINES, "test.py", config=Config())
    disabled = Config(rules={"style": {"enabled": False}})
    assert reg.run_all(ctx, disabled) == []


# ==================== 8. utils 与 config 的豁免解析单一实现 ====================


def test_utils_ignore_helpers_delegate_to_config():
    specific = "x = 1  # codereview: ignore=SEC001,SEC005"
    assert is_ignored_line(specific) is True
    assert get_ignored_rule_ids(specific) == ["SEC001", "SEC005"]

    suppress_all = "x = 1  # codereview: ignore"
    assert is_ignored_line(suppress_all) is True
    # 旧 API：None 表示「无标记或全部豁免」
    assert get_ignored_rule_ids(suppress_all) is None

    spaced = "x = 1  # codereview: ignore = SEC001"
    assert get_ignored_rule_ids(spaced) == ["SEC001"]

    lower = "x = 1  # CodeReview: IGNORE=sec001"
    assert get_ignored_rule_ids(lower) == ["SEC001"]

    plain = "x = 1"
    assert is_ignored_line(plain) is False
    assert get_ignored_rule_ids(plain) is None


def test_ignore_parsing_has_single_implementation():
    """utils 与 config 两套公开函数对同一行的判断必须完全一致。"""
    samples = [
        "x = 1",
        "x = 1  # codereview: ignore",
        "x = 1  # codereview: ignore=SEC001,SEC005",
        "x = 1  # CodeReview: IGNORE = sec001",
    ]
    for line in samples:
        parsed = get_line_ignored_rules(line)
        assert is_ignored_line(line) is (parsed is not None)

        ids = get_ignored_rule_ids(line)
        if not parsed:
            assert ids is None
        else:
            assert ids == sorted(parsed)

        # is_issue_suppressed 与同一份解析结果保持一致（行号 1-indexed）
        for rule_id in ("SEC001", "SEC002"):
            expected = parsed is not None and (not parsed or rule_id in parsed)
            assert is_issue_suppressed([line], 1, rule_id) is expected


# ==================== ast.Match（QA 复审 ③）====================


def test_match_counted_in_nesting_and_cognitive():
    """match 属多分支控制流：嵌套深度与认知复杂度都要统计。"""
    source = (
        "def route(command):\n"
        "    match command:\n"
        "        case 'a':\n"
        "            return 1\n"
        "        case _:\n"
        "            return 0\n"
    )
    func = ast.parse(source).body[0]
    assert compute_max_nesting_depth(func) == 1
    assert compute_cognitive_complexity(func) == 1
