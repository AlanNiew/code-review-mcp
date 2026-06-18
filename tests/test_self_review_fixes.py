"""v0.2.1 自审修复的回归测试。

每条测试对应自审发现的具体 bug / 缺陷，确保未来不会回归：
- test_diff_respects_inline_suppression:  Bug #2 review_diff 行级豁免
- test_diff_line_no_not_zero:             行号正确解析
- test_diff_secret_in_string_literal:     缺陷 #2 字符串字面量误报
- test_print_in_string_not_flagged:       缺陷 #2 字符串字面量
- test_print_in_check_expression:         缺陷 #2 检查表达式（自审真实 bug）
- test_style004_split_for_languages:      STYLE004 拆分（Python/JS）
- test_project_score_not_F_for_large_clean: 缺陷 #1 大项目评分
"""

from code_review_mcp.analyzers.diff import _check_diff_issues, _is_python_print_call

# ==================== Bug #2: review_diff 应用行级豁免 ====================


def test_diff_respects_inline_suppression():
    """带 `# codereview: ignore`（全部豁免）的硬编码密钥应该被豁免。"""
    diff = (
        "diff --git a/app.py b/app.py\n"
        "@@ -1,3 +1,5 @@\n"
        " x = 1\n"
        '+token = "hardcoded-for-dev-only-1234567890"  # codereview: ignore\n'
    )
    issues = _check_diff_issues(diff)
    # 没有 DIFF004 报告
    rule_ids = {i.type for i in issues}
    assert "DIFF004" not in rule_ids, f"全部豁免标记应该过滤掉 DIFF004，实际 issue: {rule_ids}"


def test_diff_wrong_rule_suppression_does_not_apply():
    """`# codereview: ignore=SEC005` 不应该豁免 DIFF004（不同规则 ID）。"""
    diff = (
        "diff --git a/app.py b/app.py\n"
        "@@ -1,3 +1,5 @@\n"
        " x = 1\n"
        '+token = "hardcoded1234567890"  # codereview: ignore=SEC005\n'
    )
    issues = _check_diff_issues(diff)
    rule_ids = {i.type for i in issues}
    # DIFF004 不在豁免列表（SEC005），应该被报
    assert "DIFF004" in rule_ids


def test_diff_reports_secret_without_suppression():
    """没有豁免标记的硬编码密钥应该被报告。"""
    diff = (
        "diff --git a/app.py b/app.py\n"
        "@@ -1,3 +1,5 @@\n"
        " x = 1\n"
        '+token = "hardcoded-for-dev-only-1234567890"\n'
    )
    issues = _check_diff_issues(diff)
    rule_ids = {i.type for i in issues}
    assert "DIFF004" in rule_ids


def test_diff_specific_rule_suppression():
    """`# codereview: ignore=DIFF004` 只豁免 DIFF004，其他规则仍报。"""
    diff = (
        "diff --git a/app.py b/app.py\n"
        "@@ -1,3 +1,5 @@\n"
        " x = 1\n"
        '+token = "hardcoded1234567890"  # codereview: ignore=DIFF004\n'
        '+print("debug")\n'  # DIFF002，应该被报
    )
    issues = _check_diff_issues(diff)
    rule_ids = {i.type for i in issues}
    assert "DIFF004" not in rule_ids  # 被豁免
    assert "DIFF002" in rule_ids  # 不受影响


# ==================== 行号解析（不再始终为 0）====================


def test_diff_line_no_not_zero():
    """issue 的 line 字段应该是正确的行号，不再是 0。"""
    diff = (
        "diff --git a/app.py b/app.py\n"
        "@@ -1,3 +5,7 @@\n"  # 新文件从第 5 行开始
        " x = 1\n"  # 上下文（第 5 行）
        '+print("debug")\n'  # 新增（第 6 行）
        "+y = 2\n"  # 新增（第 7 行）
    )
    issues = _check_diff_issues(diff)
    debug_issues = [i for i in issues if i.type == "DIFF002"]
    assert len(debug_issues) == 1
    assert debug_issues[0].line == 6, (
        f"行号应该是 6（@@ +5,7 @@ 第二个新增行），实际 {debug_issues[0].line}"
    )


def test_diff_line_no_across_multiple_hunks():
    """多个 hunk 的行号应该独立正确。"""
    diff = (
        "diff --git a/app.py b/app.py\n"
        "@@ -1,1 +10,1 @@\n"  # 第一 hunk，从第 10 行开始
        '+print("a")\n'  # 第 10 行
        "@@ -20,1 +25,1 @@\n"  # 第二 hunk，从第 25 行开始
        '+print("b")\n'  # 第 25 行
    )
    issues = _check_diff_issues(diff)
    debug_issues = [i for i in issues if i.type == "DIFF002"]
    lines = sorted(i.line for i in debug_issues)
    assert lines == [10, 25], f"应该是 [10, 25]，实际 {lines}"


# ==================== 缺陷 #2: 字符串字面量误报 ====================


def test_print_in_string_literal_not_flagged():
    """包含 'print(' 字符串字面量不应被报为调试 print。"""
    # 在 diff 层
    diff = (
        "diff --git a/app.py b/app.py\n"
        "@@ -1,1 +1,3 @@\n"
        '+msg = "print this for debug"\n'  # 字符串字面量，不是调用
    )
    issues = _check_diff_issues(diff)
    rule_ids = {i.type for i in issues}
    assert "DIFF002" not in rule_ids


def test_print_in_check_expression_not_flagged():
    """`if "print(" in line` 这种检查表达式不应被报（自审真实 bug）。"""
    diff = 'diff --git a/app.py b/app.py\n@@ -1,1 +1,3 @@\n+if "print(" in line:\n+    pass\n'
    issues = _check_diff_issues(diff)
    rule_ids = {i.type for i in issues}
    assert "DIFF002" not in rule_ids


def test_print_in_comment_not_flagged_in_diff():
    """注释行 `# use print() for debug` 不应被报为调试 print。"""
    diff = "diff --git a/app.py b/app.py\n@@ -1,1 +1,3 @@\n+# use print() for debug output\n"
    issues = _check_diff_issues(diff)
    rule_ids = {i.type for i in issues}
    assert "DIFF002" not in rule_ids


def test_actual_print_call_is_flagged():
    """真正的 print() 调用应该被报。"""
    diff = 'diff --git a/app.py b/app.py\n@@ -1,1 +1,3 @@\n+print("debug")\n'
    issues = _check_diff_issues(diff)
    rule_ids = {i.type for i in issues}
    assert "DIFF002" in rule_ids


def test_is_python_print_call_helper():
    """直接测试 AST 辅助函数。"""
    assert _is_python_print_call('print("hello")') is True
    assert _is_python_print_call("print(x, y)") is True
    assert _is_python_print_call("x = print('test')") is True
    # 字符串字面量
    assert _is_python_print_call('msg = "print this"') is False
    assert _is_python_print_call('if "print(" in line:') is False
    # 属性调用（用户自定义 print 方法）
    assert _is_python_print_call("obj.print('data')") is False
    # 语法错误
    assert _is_python_print_call("print(") is False


# ==================== STYLE004 拆分（Python/JS）====================


def test_style004_python_print(run_rules):
    """Python: 直接调用 print() 应该被报 STYLE004。"""
    issues = run_rules('print("hello")', file_path="app.py")
    rule_ids = {i.type for i in issues}
    assert "STYLE004" in rule_ids


def test_style004_python_string_not_flagged(run_rules):
    """Python: 字符串字面量 'print(' 不应被报。"""
    issues = run_rules('msg = "print this"', file_path="app.py")
    rule_ids = {i.type for i in issues}
    assert "STYLE004" not in rule_ids


def test_style004_python_check_expression_not_flagged(run_rules):
    """Python: `if "print(" in line:` 不应被报（自审真实 bug）。"""
    issues = run_rules('if "print(" in line:\n    pass', file_path="app.py")
    rule_ids = {i.type for i in issues}
    assert "STYLE004" not in rule_ids


def test_style005_js_console_log(run_rules):
    """JS: console.log() 应该被报 STYLE005。"""
    issues = run_rules('console.log("debug")', file_path="app.js")
    rule_ids = {i.type for i in issues}
    assert "STYLE005" in rule_ids


def test_style005_js_in_comment_not_flagged(run_rules):
    """JS: 注释里的 console.log 不应被报。"""
    issues = run_rules('// console.log("debug")', file_path="app.js")
    rule_ids = {i.type for i in issues}
    assert "STYLE005" not in rule_ids


# ==================== 缺陷 #1: 项目级评分 ====================


def test_project_score_clean_files_gets_A(tmp_path):
    """干净的小项目应该得 A，不再因为文件多就降级。"""
    # 10 个干净的小文件
    for i in range(10):
        (tmp_path / f"module_{i}.py").write_text(
            f"def add_{i}(a, b):\n    return a + b\n", encoding="utf-8"
        )
    from code_review_mcp.server import check_project

    result = check_project(str(tmp_path))
    overall = result["overall_quality"]
    assert overall["grade"] == "A", (
        f"干净项目应该得 A，实际 {overall['grade']} ({overall['score']})"
    )


def test_project_score_mixed_files_not_F(tmp_path):
    """一个烂文件 + 几个干净文件，整体不应是 F。"""
    # 一个有问题的文件
    (tmp_path / "bad.py").write_text(
        "import pickle\nimport hashlib\n"
        "def f(x):\n    if x:\n        if x:\n            if x:\n                if x:\n                    if x:\n                        return pickle.loads(hashlib.md5(x))\n",
        encoding="utf-8",
    )
    # 几个干净文件
    for i in range(5):
        (tmp_path / f"clean_{i}.py").write_text(
            f"def add_{i}(a, b):\n    return a + b\n", encoding="utf-8"
        )

    from code_review_mcp.server import check_project

    result = check_project(str(tmp_path))
    overall = result["overall_quality"]
    # 旧逻辑：5 个文件都有 issue，硬扣分会得到 F
    # 新逻辑：5/6 文件是干净的，整体应该 ≥ C（不再是 F）
    assert overall["grade"] != "F", (
        f"混合项目不应得 F（v0.2.0 bug），实际 {overall['grade']} ({overall['score']})"
    )
    assert overall["score"] >= 60, f"分数应 >= 60，实际 {overall['score']}"


def test_project_score_returns_multi_dimensional(tmp_path):
    """项目级评分应包含三个维度（v0.2.1 新增）。"""
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    from code_review_mcp.server import check_project

    result = check_project(str(tmp_path))
    # 新字段
    assert "quality" in result
    assert "overall" in result["quality"]
    assert "security" in result["quality"]
    assert "maintainability" in result["quality"]
    # 兼容字段仍然存在
    assert "overall_quality" in result


def test_project_score_empty_dir_is_A(tmp_path):
    """空目录应得 A。"""
    from code_review_mcp.server import check_project

    result = check_project(str(tmp_path))
    assert result["overall_quality"]["grade"] == "A"
