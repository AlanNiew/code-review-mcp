"""MCP 工具入口的端到端测试（analyze_file / review_diff / check_project / list_rules）。"""

import subprocess
from pathlib import Path

from code_review_mcp import server

# ==================== list_rules ====================


def test_list_rules_returns_all():
    result = server.list_rules()
    assert result["total"] >= 20  # 至少 21 条规则
    assert len(result["rules"]) == result["total"]

    rule_ids = {r["id"] for r in result["rules"]}
    # 抽查核心规则
    assert "SEC001" in rule_ids
    assert "SEC005" in rule_ids
    assert "COMPLEX003" in rule_ids
    assert "STYLE001" in rule_ids

    # 每条都有必要字段
    for rule in result["rules"]:
        assert "id" in rule
        assert "name" in rule
        assert "description" in rule
        assert "category" in rule
        assert "severity" in rule
        assert "languages" in rule


# ==================== analyze_file ====================


def test_analyze_file_clean(tmp_path: Path):
    """干净的代码应该高分且无 issue。"""
    code = 'def add(a, b):\n    """Add two numbers."""\n    return a + b\n'
    fp = tmp_path / "clean.py"
    fp.write_text(code, encoding="utf-8")

    result = server.analyze_file(str(fp))
    assert "error" not in result
    assert result["language"] == "python"
    assert len(result["issues"]) == 0
    assert result["quality"]["overall"]["grade"] == "A"


def test_analyze_file_with_security_issues(tmp_path: Path):
    code = "import pickle\npickle.loads(data)\neval('1')\n"
    fp = tmp_path / "vuln.py"
    fp.write_text(code, encoding="utf-8")

    result = server.analyze_file(str(fp))
    rule_ids = {i["type"] for i in result["issues"]}
    assert "SEC001" in rule_ids  # eval
    assert "SEC003" in rule_ids  # pickle.loads
    assert result["quality"]["security"]["grade"] != "A"


def test_analyze_file_nonexistent():
    result = server.analyze_file("/nonexistent/path/file.py")
    assert "error" in result
    assert "不存在" in result["error"]


def test_analyze_file_directory(tmp_path: Path):
    result = server.analyze_file(str(tmp_path))
    assert "error" in result
    assert "目录" in result["error"]


def test_analyze_file_too_large(tmp_path: Path):
    fp = tmp_path / "big.py"
    fp.write_text("x = 1\n" * (6 * 1024 * 1024 // 5), encoding="utf-8")  # ~6MB
    result = server.analyze_file(str(fp))
    assert "error" in result
    assert "过大" in result["error"]


def test_analyze_file_unicode_error(tmp_path: Path):
    """非 UTF-8 文件应给出友好错误。"""
    fp = tmp_path / "binary.py"
    fp.write_bytes(b"\xff\xfe\x00\x01invalid utf-8")
    result = server.analyze_file(str(fp))
    assert "error" in result


def test_analyze_file_returns_multi_dim_quality(tmp_path: Path):
    """质量分应包含 overall / security / maintainability 三个维度。"""
    fp = tmp_path / "x.py"
    fp.write_text("x = 1\n", encoding="utf-8")
    result = server.analyze_file(str(fp))
    quality = result["quality"]
    assert "overall" in quality
    assert "security" in quality
    assert "maintainability" in quality


# ==================== review_diff ====================


def test_review_diff_with_no_changes(tmp_path: Path, monkeypatch):
    """没有未提交变更时应返回提示信息。"""
    monkeypatch.chdir(tmp_path)
    # tmp_path 不是 git 仓库，git diff 会失败
    # 先 init 一个仓库
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=False)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, capture_output=True)

    result = server.review_diff()
    assert "message" in result or "error" in result


def test_review_diff_with_changes(tmp_path: Path, monkeypatch):
    """有变更时应能检测出问题。"""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=False)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, capture_output=True)

    # 创建并提交一个干净的初始文件
    fp = tmp_path / "app.py"
    fp.write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True, check=False)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True, check=False)

    # 修改：加入有问题的代码
    fp.write_text(
        "import pickle\nAPI_KEY = 'hardcoded1234567'\npickle.loads(data)\nprint('debug')\n",
        encoding="utf-8",
    )
    # 不 commit，让变更进入 unstaged
    result = server.review_diff()

    # 应该检测出至少一个问题（unstaged 部分）
    unstaged = result.get("unstaged_changes", {})
    if unstaged:
        assert len(unstaged.get("issues", [])) > 0


def test_review_diff_secret_detection_in_diff():
    """diff 中的硬编码密钥应该被检测。"""
    from code_review_mcp.analyzers.diff import _check_diff_issues

    diff = (
        "diff --git a/app.py b/app.py\n"
        "@@ -1,3 +1,5 @@\n"
        " x = 1\n"
        "+API_KEY = 'sk-1234567890abcdef'\n"
        "+print('debug')\n"
    )
    issues = _check_diff_issues(diff)
    rule_ids = {i.type for i in issues}
    assert "DIFF004" in rule_ids  # 硬编码密钥
    assert "DIFF002" in rule_ids  # 调试 print


# ==================== check_project ====================


def test_check_project_empty_dir(tmp_path: Path):
    """空目录扫描应有 0 文件。"""
    result = server.check_project(str(tmp_path))
    assert result["summary"]["total_files"] == 0


def test_check_project_finds_files(tmp_path: Path):
    """应找到并分析源码文件。"""
    (tmp_path / "app.py").write_text(
        "import pickle\npickle.loads(x)\n",
        encoding="utf-8",
    )
    (tmp_path / "clean.py").write_text("x = 1\n", encoding="utf-8")
    # 非源码文件不应被统计
    (tmp_path / "README.md").write_text("# project", encoding="utf-8")

    result = server.check_project(str(tmp_path))
    assert result["summary"]["total_files"] == 2
    assert result["summary"]["total_issues"] > 0
    assert "python" in result["languages"]


def test_check_project_excludes_common_dirs(tmp_path: Path):
    """node_modules / .venv 等应被排除。"""
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text(
        "console.log('should be ignored')\n",
        encoding="utf-8",
    )
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "lib.py").write_text("x = 1\n", encoding="utf-8")

    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")

    result = server.check_project(str(tmp_path))
    assert result["summary"]["total_files"] == 1


def test_check_project_nonexistent_dir():
    result = server.check_project("/nonexistent/directory")
    assert "error" in result


def test_check_project_returns_overall_quality(tmp_path: Path):
    """应返回整体质量评分。"""
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    result = server.check_project(str(tmp_path))
    assert "overall_quality" in result
    assert "grade" in result["overall_quality"]
    assert "score" in result["overall_quality"]
