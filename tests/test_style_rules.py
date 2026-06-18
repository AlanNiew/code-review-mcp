"""风格规则测试。"""


def _rule_ids(issues):
    return {i.type for i in issues}


# ==================== STYLE001: 行长 ====================


def test_line_too_long(run_rules):
    long_line = "x = " + "a" * 200
    issues = run_rules(long_line)
    assert "STYLE001" in _rule_ids(issues)


def test_line_length_ok(run_rules):
    code = "x = " + "a" * 50
    issues = run_rules(code)
    assert "STYLE001" not in _rule_ids(issues)


# ==================== STYLE002: TODO 标记 ====================


def test_todo_in_comment(run_rules):
    issues = run_rules("# TODO: fix this")
    assert "STYLE002" in _rule_ids(issues)


def test_fixme_in_comment(run_rules):
    issues = run_rules("// FIXME: broken")
    assert "STYLE002" in _rule_ids(issues)


def test_todo_in_string_not_flagged(run_rules):
    """字符串字面量里的 TODO 不应误报。"""
    issues = run_rules('msg = "TODO list for today"')
    assert "STYLE002" not in _rule_ids(issues)


# ==================== STYLE003: 末尾空白 ====================


def test_trailing_whitespace_python(run_rules):
    issues = run_rules(
        "x = 1   \n",
    )
    assert "STYLE003" in _rule_ids(issues)


def test_no_trailing_whitespace_safe(run_rules):
    issues = run_rules("x = 1\n")
    assert "STYLE003" not in _rule_ids(issues)


# ==================== STYLE004: 调试输出 ====================


def test_print_detected(run_rules):
    """Python 的 print() 调用应该被报 STYLE004（v0.2.1 起用 AST 检测）。"""
    issues = run_rules('print("debug")')
    assert "STYLE004" in _rule_ids(issues)


def test_console_log_detected(run_rules):
    """JS 的 console.log 应该被报 STYLE005（v0.2.1 起拆分独立规则）。"""
    issues = run_rules("console.log('debug')", file_path="test.js")
    assert "STYLE005" in _rule_ids(issues)


def test_print_in_comment_not_flagged(run_rules):
    """注释里提到 print 不应误报（v0.2.1 AST 检测后正确）。"""
    issues = run_rules("# use print() for debug")
    assert "STYLE004" not in _rule_ids(issues)
