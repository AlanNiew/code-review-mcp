"""安全规则（SEC001-SEC012）的正反例测试。

每条规则至少 2 个用例：
- 正例（应该命中）
- 反例（不应该命中，避免误报）
"""

from code_review_mcp.models import Severity


def _rule_ids(issues):
    return {i.type for i in issues}


# ==================== SEC001: eval / exec ====================


def test_eval_detected(run_rules):
    issues = run_rules("x = eval('1+1')")
    assert "SEC001" in _rule_ids(issues)
    eval_issue = next(i for i in issues if i.type == "SEC001")
    assert i_severity(eval_issue) == Severity.ERROR


def test_exec_detected(run_rules):
    issues = run_rules("exec('print(1)')")
    assert "SEC001" in _rule_ids(issues)


def test_eval_not_in_attribute(run_rules):
    """obj.eval() 这种用户自定义方法不应该误报。"""
    issues = run_rules("my_obj.eval('data')")
    assert "SEC001" not in _rule_ids(issues)


# ==================== SEC002: shell=True ====================


def test_subprocess_shell_true(run_rules):
    code = "import subprocess\nsubprocess.call('ls', shell=True)"
    issues = run_rules(code)
    assert "SEC002" in _rule_ids(issues)


def test_os_system_detected(run_rules):
    code = "import os\nos.system('rm -rf /')"
    issues = run_rules(code)
    assert "SEC002" in _rule_ids(issues)


def test_subprocess_shell_false_safe(run_rules):
    code = "import subprocess\nsubprocess.run(['ls', '-l'])"
    issues = run_rules(code)
    assert "SEC002" not in _rule_ids(issues)


# ==================== SEC003: 不安全反序列化 ====================


def test_pickle_loads_detected(run_rules):
    code = "import pickle\npickle.loads(data)"
    issues = run_rules(code)
    assert "SEC003" in _rule_ids(issues)


def test_yaml_load_detected(run_rules):
    code = "import yaml\nyaml.load(text)"
    issues = run_rules(code)
    assert "SEC003" in _rule_ids(issues)


def test_yaml_safe_load_safe(run_rules):
    code = "import yaml\nyaml.safe_load(text)"
    issues = run_rules(code)
    assert "SEC003" not in _rule_ids(issues)


def test_json_load_safe(run_rules):
    """json 反序列化应该是安全的。"""
    code = "import json\njson.loads(text)"
    issues = run_rules(code)
    assert "SEC003" not in _rule_ids(issues)


# ==================== SEC004: 弱哈希 ====================


def test_md5_detected(run_rules):
    code = "import hashlib\nhashlib.md5(data)"
    issues = run_rules(code)
    assert "SEC004" in _rule_ids(issues)


def test_sha1_detected(run_rules):
    code = "import hashlib\nhashlib.sha1(data)"
    issues = run_rules(code)
    assert "SEC004" in _rule_ids(issues)


def test_sha256_safe(run_rules):
    code = "import hashlib\nhashlib.sha256(data)"
    issues = run_rules(code)
    assert "SEC004" not in _rule_ids(issues)


def test_md5_usedforsecurity_false_safe(run_rules):
    """hashlib.md5(..., usedforsecurity=False) 是合法用法（Python 3.9+）。"""
    code = "import hashlib\nhashlib.md5(data, usedforsecurity=False)"
    issues = run_rules(code)
    assert "SEC004" not in _rule_ids(issues)


# ==================== SEC005: 硬编码密钥 ====================


def test_hardcoded_api_key(run_rules):
    code = "API_KEY = 'sk-1234567890abcdef'"
    issues = run_rules(code)
    assert "SEC005" in _rule_ids(issues)


def test_hardcoded_password(run_rules):
    code = "password = 'mysecret123'"
    issues = run_rules(code)
    assert "SEC005" in _rule_ids(issues)


def test_empty_password_safe(run_rules):
    """空字符串不应误报。"""
    code = "password = ''"
    issues = run_rules(code)
    assert "SEC005" not in _rule_ids(issues)


def test_placeholder_safe(run_rules):
    """占位符（<...> / xxx / changeme）不应误报。"""
    code = 'api_key = "<your-api-key>"'
    issues = run_rules(code)
    assert "SEC005" not in _rule_ids(issues)


# ==================== SEC006: SSL verify=False ====================


def test_requests_verify_false(run_rules):
    code = "import requests\nrequests.get(url, verify=False)"
    issues = run_rules(code)
    assert "SEC006" in _rule_ids(issues)


def test_requests_verify_true_safe(run_rules):
    code = "import requests\nrequests.get(url, verify=True)"
    issues = run_rules(code)
    assert "SEC006" not in _rule_ids(issues)


# ==================== SEC007: bare except / 吞异常 ====================


def test_bare_except(run_rules):
    code = "try:\n    x = 1\nexcept:\n    pass"
    issues = run_rules(code)
    assert "SEC007" in _rule_ids(issues)


def test_specific_exception_safe(run_rules):
    code = "try:\n    x = 1\nexcept ValueError as e:\n    raise"
    issues = run_rules(code)
    assert "SEC007" not in _rule_ids(issues)


def test_swallowed_exception(run_rules):
    code = "try:\n    x = 1\nexcept Exception:\n    pass"
    issues = run_rules(code)
    assert "SEC007" in _rule_ids(issues)


# ==================== SEC008: SQL 拼接 ====================


def test_sql_string_concat(run_rules):
    code = 'cursor.execute("SELECT * FROM users WHERE id=" + user_id)'
    issues = run_rules(code)
    assert "SEC008" in _rule_ids(issues)


def test_sql_format(run_rules):
    code = 'cursor.execute("SELECT * FROM t WHERE id=%s" % uid)'
    issues = run_rules(code)
    assert "SEC008" in _rule_ids(issues)


def test_sql_parameterized_safe(run_rules):
    code = 'cursor.execute("SELECT * FROM t WHERE id=?", (uid,))'
    issues = run_rules(code)
    assert "SEC008" not in _rule_ids(issues)


# ==================== SEC009: 生产代码 assert ====================


def test_assert_in_production(run_rules):
    issues = run_rules("assert x == 1", file_path="src/app.py")
    assert "SEC009" in _rule_ids(issues)


def test_assert_in_test_file_safe(run_rules):
    """测试文件里的 assert 不应报告。"""
    issues = run_rules("assert x == 1", file_path="tests/test_app.py")
    assert "SEC009" not in _rule_ids(issues)


# ==================== SEC010: Flask debug ====================


def test_flask_debug_true(run_rules):
    code = "from flask import Flask\napp = Flask(__name__)\napp.run(debug=True)"
    issues = run_rules(code)
    assert "SEC010" in _rule_ids(issues)


def test_flask_debug_false_safe(run_rules):
    code = "from flask import Flask\napp = Flask(__name__)\napp.run(debug=False)"
    issues = run_rules(code)
    assert "SEC010" not in _rule_ids(issues)


# ==================== SEC011: JWT 不验签 ====================


def test_jwt_no_verify(run_rules):
    code = "import jwt\njwt.decode(token, options={'verify_signature': False})"
    issues = run_rules(code)
    assert "SEC011" in _rule_ids(issues)


# ==================== SEC012: 不安全 random ====================


def test_weak_random_for_token(run_rules):
    code = "import random\ntoken = random.randint(0, 999999)"
    issues = run_rules(code)
    assert "SEC012" in _rule_ids(issues)


def test_random_for_non_secret_safe(run_rules):
    code = "import random\nx = random.randint(0, 100)"
    issues = run_rules(code)
    assert "SEC012" not in _rule_ids(issues)


# ==================== 辅助函数 ====================


def i_severity(issue):
    """统一获取 issue 的 severity（兼容 Severity 枚举和 str）。"""
    return issue.severity if hasattr(issue.severity, "value") else issue.severity
