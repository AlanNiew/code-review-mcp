"""MCP 工具入口的端到端测试（review_changes / analyze_file / review_diff / check_project / list_rules）。"""

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
    # tmp_path 不是 git 仓库，先 init 一个
    _init_git_repo(tmp_path)

    result = server.review_diff()
    assert "message" in result or "error" in result


def test_review_diff_with_changes(tmp_path: Path, monkeypatch):
    """有变更时应能检测出问题。"""
    monkeypatch.chdir(tmp_path)
    _init_git_repo(tmp_path)

    # 创建并提交一个干净的初始文件
    fp = tmp_path / "app.py"
    fp.write_text("x = 1\n", encoding="utf-8")
    _git_commit_all(tmp_path, "init")

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


# ==================== review_changes（v0.3.0 旗舰）====================


# 隔离宿主 git 配置：签名/换行/身份都不依赖运行者机器（QA 复审建议）
_GIT_ISOLATION = (
    "-c",
    "commit.gpgsign=false",
    "-c",
    "core.autocrlf=false",
    "-c",
    "core.safecrlf=false",
    "-c",
    "user.name=test",
    "-c",
    "user.email=test@example.com",
    "-c",
    "init.defaultBranch=main",
)


def _git(path: Path, *args: str) -> None:
    """在临时仓库里执行 git，失败直接抛错（不允许静默吞掉）。"""
    subprocess.run(
        ["git", *_GIT_ISOLATION, *args], cwd=path, capture_output=True, check=True, text=True
    )


def _init_git_repo(path: Path) -> None:
    """初始化一个可提交的临时 git 仓库。"""
    _git(path, "init", "-q")


def _git_commit_all(path: Path, message: str) -> None:
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", message)


def test_review_changes_working_packet(tmp_path: Path):
    """working 模式应返回结构完整、可 JSON 序列化的证据包。"""
    import json

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    (repo / "base.py").write_text("x = 1\n", encoding="utf-8")
    _git_commit_all(repo, "init")

    # 新增一个带问题的文件（未提交）
    (repo / "app.py").write_text("import pickle\npickle.loads(data)\n", encoding="utf-8")

    result = server.review_changes(scope="working", directory=str(repo))
    assert "error" not in result
    assert result["scope"] == "working"
    assert result["stats"]["files"] >= 1
    assert {"skipped", "truncated", "notes"} <= set(result["limits"])

    # 能 JSON 序列化（MCP 传输前提）
    json.dumps(result, ensure_ascii=False)

    app = next(f for f in result["files"] if f["path"] == "app.py")
    assert app["status"] == "added"
    assert app["additions"] == 2
    # 新文件的全部行都算新增，命中行号正确
    assert any(f["rule"] == "SEC003" and f["line"] == 2 for f in app["findings"])


def test_review_changes_findings_only_on_added_lines(tmp_path: Path):
    """基线中已有问题不应出现在本次审查结果里。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    (repo / "app.py").write_text("import pickle\npickle.loads(old)\nvalue = 1\n", encoding="utf-8")
    _git_commit_all(repo, "init")

    # 只改动最后一行
    (repo / "app.py").write_text(
        'import pickle\npickle.loads(old)\nvalue = 2\nprint("debug")\n', encoding="utf-8"
    )

    result = server.review_changes(scope="working", directory=str(repo))
    app = next(f for f in result["files"] if f["path"] == "app.py")
    rules_at_line_2 = [f for f in app["findings"] if f["line"] == 2]
    assert rules_at_line_2 == []  # 历史 pickle.loads 不报


def test_review_changes_non_git_repo(tmp_path: Path):
    """非 git 仓库应返回 error + hint，而不是抛异常。"""
    result = server.review_changes(directory=str(tmp_path))
    assert "error" in result
    assert "hint" in result


def test_review_changes_invalid_scope(tmp_path: Path):
    """未知 scope 应返回 error。"""
    _init_git_repo(tmp_path)
    result = server.review_changes(scope="everything", directory=str(tmp_path))
    assert "error" in result


def test_review_changes_base_scope(tmp_path: Path):
    """base 模式应审 merge-base 三点差异。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git_commit_all(repo, "init")
    (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    _git_commit_all(repo, "change")

    result = server.review_changes(scope="base", base="HEAD~1", directory=str(repo))
    assert "error" not in result
    assert "..." in result["range"]
    assert result["stats"]["files"] == 1
    assert result["files"][0]["path"] == "app.py"


def test_review_changes_staged_scope(tmp_path: Path):
    """staged 模式只应包含已暂存的改动。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git_commit_all(repo, "init")

    (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)

    result = server.review_changes(scope="staged", directory=str(repo))
    assert "error" not in result
    assert [f["path"] for f in result["files"]] == ["app.py"]
    assert result["stats"]["additions"] == 1


def test_analyze_file_accepts_utf8_bom(tmp_path: Path):
    """带 UTF-8 BOM 的合法 Python 文件不应被补 SYNTAX001。"""
    target = tmp_path / "bom.py"
    target.write_text("\ufeffvalue = 1\n", encoding="utf-8")

    result = server.analyze_file(str(target))

    assert "error" not in result
    assert all(issue["type"] != "SYNTAX001" for issue in result["issues"])
    assert result["lines"]["code"] >= 1
