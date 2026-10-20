"""变更证据引擎测试：真实 git 仓库端到端 + 纯函数单元测试。

所有 git 操作都走 subprocess，并显式配置 `commit.gpgsign=false`、
`core.autocrlf=false`、`core.quotepath=false`，保证在 Windows 上稳定。
"""

import json
import subprocess
from pathlib import Path

import pytest

from code_review_mcp.analyzers.change import (
    ReviewTarget,
    collect_review_target,
    parse_unified_diff,
    python_function_deltas,
    review_change_set,
)
from code_review_mcp.config import Config

# ==================== git 测试辅助 ====================

_GIT_COMMON = [
    "-c",
    "commit.gpgsign=false",
    "-c",
    "core.autocrlf=false",
    "-c",
    "core.quotepath=false",
    "-c",
    "init.defaultBranch=main",
]

_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


def _run_git(repo: Path, *args: str, check: bool = True) -> str:
    """在指定仓库里执行 git 命令，返回 stdout。"""
    proc = subprocess.run(
        ["git", *_GIT_COMMON, *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} 失败：{proc.stderr}")
    return proc.stdout


def _init_repo(tmp_path: Path, name: str = "repo") -> Path:
    """初始化一个带 user 配置的临时 git 仓库（默认分支 main）。"""
    repo = tmp_path / name
    repo.mkdir()
    _run_git(repo, "init")
    _run_git(repo, "config", "user.name", "Change Analyzer Tests")
    _run_git(repo, "config", "user.email", "change-analyzer@example.com")
    return repo


def _write(repo: Path, relative: str, text: str) -> Path:
    """写入 UTF-8 文本文件（统一 LF 换行）。"""
    target = repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    return target


def _commit(repo: Path, message: str) -> str:
    """提交全部改动，返回 HEAD 的短 sha。"""
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-m", message)
    return _run_git(repo, "rev-parse", "--short=7", "HEAD").strip()


def _file_entry(packet: dict, path: str) -> dict:
    """从 packet 中取出指定文件的条目。"""
    for entry in packet["files"]:
        if entry["path"] == path:
            return entry
    pytest.fail(f"packet 中没有文件 {path}：{[item['path'] for item in packet['files']]}")


def _all_findings(packet: dict) -> list[dict]:
    """汇总所有文件条目里的 findings。"""
    return [finding for entry in packet["files"] for finding in entry["findings"]]


_BASELINE_APP = '''"""基线示例模块。"""


def run(value):
    return eval(value)  # 基线里已有的问题，不应出现在 findings 中
'''

_BASELINE_TEST = """from app import run


def test_run():
    assert run("1 + 1") == 2
"""

_UNSAFE_APPEND = """


def unsafe(value):
    return eval(value)
"""

_COMPLEX_APP = '''"""基线示例模块。"""


def run(value):
    if value is None:
        return 0
    if value == "":
        return 1
    if value == "x":
        return 2
    return eval(value)
'''

_SERVICE_UTILS = '''"""服务工具（没有对应测试）。"""

import os  # type: ignore[import]


def ping():
    """返回当前工作目录。"""
    return os.getcwd()


def pong():
    """返回 False。"""
    return False


def pong2():
    """返回 True。"""
    return True
'''


@pytest.fixture()
def baseline_repo(tmp_path: Path) -> Path:
    """带一次基线提交的仓库。"""
    repo = _init_repo(tmp_path)
    _write(repo, "app.py", _BASELINE_APP)
    _write(repo, "tests/test_app.py", _BASELINE_TEST)
    _write(repo, "requirements.txt", "requests>=2.28.0\n")
    _write(repo, "README.md", "# 示例仓库\n")
    _commit(repo, "baseline")
    return repo


# ==================== parse_unified_diff ====================


def test_parse_added_file_lines():
    """新增文件：状态、行数、新增行号。"""
    diff = (
        "diff --git a/new.py b/new.py\n"
        "new file mode 100644\n"
        "index 0000000..1111111\n"
        "--- /dev/null\n"
        "+++ b/new.py\n"
        "@@ -0,0 +1,3 @@\n"
        "+a = 1\n"
        "+b = 2\n"
        "+c = 3\n"
    )
    diffs = parse_unified_diff(diff)
    assert len(diffs) == 1
    fd = diffs[0]
    assert fd.path == "new.py"
    assert fd.old_path is None
    assert fd.status == "added"
    assert (fd.additions, fd.deletions) == (3, 0)
    assert fd.added_lines == {1, 2, 3}
    assert fd.removed_lines == set()
    assert fd.is_binary is False


def test_parse_multi_hunk_and_omitted_counts():
    """多 hunk + 省略行数的 hunk 头：新旧行号必须精确。"""
    diff = (
        "diff --git a/m.py b/m.py\n"
        "--- a/m.py\n"
        "+++ b/m.py\n"
        "@@ -1,3 +1,4 @@\n"
        " one\n"
        "-two\n"
        "+two-new\n"
        "+two-extra\n"
        " three\n"
        "@@ -10 +11 @@\n"
        "-old\n"
        "+new\n"
    )
    fd = parse_unified_diff(diff)[0]
    assert (fd.additions, fd.deletions) == (3, 2)
    assert fd.added_lines == {2, 3, 11}
    assert fd.removed_lines == {2, 10}


def test_parse_deleted_renamed_binary_and_no_newline():
    """deleted / renamed / binary / 无行尾换行 标记。"""
    diff = (
        "diff --git a/gone.py b/gone.py\n"
        "deleted file mode 100644\n"
        "--- a/gone.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-x = 1\n"
        "-y = 2\n"
        "diff --git a/old_name.py b/new_name.py\n"
        "similarity index 90%\n"
        "rename from old_name.py\n"
        "rename to new_name.py\n"
        "--- a/old_name.py\n"
        "+++ b/new_name.py\n"
        "@@ -1 +1 @@\n"
        "-a = 1\n"
        "+a = 2\n"
        "diff --git a/logo.png b/logo.png\n"
        "new file mode 100644\n"
        "index 0000000..2222222\n"
        "Binary files /dev/null and b/logo.png differ\n"
        "diff --git a/keep.txt b/keep.txt\n"
        "--- a/keep.txt\n"
        "+++ b/keep.txt\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "\\ No newline at end of file\n"
        "+new\n"
        "\\ No newline at end of file\n"
    )
    diffs = {fd.path: fd for fd in parse_unified_diff(diff)}

    assert diffs["gone.py"].status == "deleted"
    assert diffs["gone.py"].deletions == 2
    assert diffs["gone.py"].removed_lines == {1, 2}

    assert diffs["new_name.py"].status == "renamed"
    assert diffs["new_name.py"].old_path == "old_name.py"
    assert diffs["new_name.py"].added_lines == {1}

    assert diffs["logo.png"].status == "added"
    assert diffs["logo.png"].is_binary is True

    assert diffs["keep.txt"].added_lines == {1}
    assert diffs["keep.txt"].removed_lines == {1}


def test_parse_empty_diff():
    """空 diff 返回空列表。"""
    assert parse_unified_diff("") == []


def test_parse_escaped_path():
    """带引号转义的路径要还原成真实文件名。"""
    diff = (
        'diff --git "a/\\346\\226\\207\\344\\273\\266.py" "b/\\346\\226\\207\\344\\273\\266.py"\n'
        "new file mode 100644\n"
        "--- /dev/null\n"
        '+++ "b/\\346\\226\\207\\344\\273\\266.py"\n'
        "@@ -0,0 +1 @@\n"
        "+x = 1\n"
    )
    fd = parse_unified_diff(diff)[0]
    # 只要不抛异常且能解析出一行新增即可（不同 git 版本转义形态略有差异）
    assert fd.status == "added"
    assert fd.additions >= 1


# ==================== collect_review_target ====================


def test_collect_working_target(baseline_repo: Path):
    """working：改动 + 未跟踪文件 + 提交元信息。"""
    _write(baseline_repo, "app.py", _BASELINE_APP + _UNSAFE_APPEND)
    _write(baseline_repo, "notes.txt", "hello\n")

    target = collect_review_target(scope="working", repo_root=str(baseline_repo))

    assert isinstance(target, ReviewTarget)
    assert target.scope == "working"
    assert target.range_spec == "HEAD"
    assert target.base_ref is None
    assert target.base_commit is not None and len(target.base_commit) == 7
    assert target.head_commit == target.base_commit
    assert "notes.txt" in target.untracked
    assert "app.py" in target.diff_text
    assert Path(target.repo_root).resolve() == baseline_repo.resolve()


def test_collect_unknown_scope_returns_error(baseline_repo: Path):
    """未知 scope：结构化错误而非异常。"""
    result = collect_review_target(scope="bogus", repo_root=str(baseline_repo))
    assert isinstance(result, dict)
    assert "error" in result and "hint" in result


def test_collect_non_git_repo_returns_error(tmp_path: Path):
    """非 git 仓库：error + hint。"""
    plain = tmp_path / "plain"
    plain.mkdir()
    probe = _run_git(plain, "rev-parse", "--show-toplevel", check=False)
    if probe.strip():
        pytest.skip("临时目录位于别的 git 仓库内，无法构造非 git 场景")

    result = collect_review_target(repo_root=str(plain))
    assert isinstance(result, dict)
    assert result.get("error")
    assert "hint" in result
    assert "git" in result["hint"]

    packet = review_change_set(repo_root=str(plain))
    assert isinstance(packet, dict)
    assert "error" in packet


def test_collect_base_unknown_ref_returns_error(baseline_repo: Path):
    """base 分支不存在：结构化错误。"""
    result = collect_review_target(
        scope="base", base="no-such-branch", repo_root=str(baseline_repo)
    )
    assert isinstance(result, dict)
    assert "error" in result
    assert "hint" in result


def test_collect_base_requires_base_argument(baseline_repo: Path):
    """base scope 未传 base：结构化错误。"""
    result = collect_review_target(scope="base", repo_root=str(baseline_repo))
    assert isinstance(result, dict)
    assert "error" in result


def test_no_commit_repo(tmp_path: Path):
    """没有任何 commit：working / staged 仍可用，所有文件按新增处理。"""
    repo = _init_repo(tmp_path, "fresh")
    _write(repo, "solo.py", '"""无提交仓库。"""\n\n\ndef hello():\n    return "hi"\n')

    target = collect_review_target(scope="working", repo_root=str(repo))
    assert isinstance(target, ReviewTarget)
    assert target.base_commit is None
    assert target.head_commit is None
    assert target.range_spec == "working tree"
    assert "solo.py" in target.untracked
    assert target.diff_text.strip() == ""

    packet = review_change_set(scope="working", repo_root=str(repo))
    entry = _file_entry(packet, "solo.py")
    assert entry["status"] == "added"
    assert entry["additions"] == 5

    # base 模式在没有 commit 时直接报错
    base_result = collect_review_target(scope="base", base="main", repo_root=str(repo))
    assert isinstance(base_result, dict)
    assert "error" in base_result

    # staged 模式：index blob 可读，range 用可读的 "index"
    _run_git(repo, "add", "solo.py")
    staged_target = collect_review_target(scope="staged", repo_root=str(repo))
    assert isinstance(staged_target, ReviewTarget)
    assert staged_target.range_spec == "index"
    staged_packet = review_change_set(scope="staged", repo_root=str(repo))
    staged_entry = _file_entry(staged_packet, "solo.py")
    assert staged_entry["status"] == "added"
    assert staged_entry["additions"] == 5


# ==================== review_change_set：三种 scope ====================


def test_working_scope_packet(baseline_repo: Path):
    """working scope 的端到端 packet 结构与字段。"""
    _write(baseline_repo, "app.py", _BASELINE_APP + _UNSAFE_APPEND)
    _write(baseline_repo, "extra.py", "def extra():\n    return 1\n")
    head = _run_git(baseline_repo, "rev-parse", "--short=7", "HEAD").strip()

    packet = review_change_set(scope="working", repo_root=str(baseline_repo))

    assert set(packet) == {
        "scope",
        "range",
        "base_ref",
        "base_commit",
        "head_commit",
        "stats",
        "files",
        "signals",
        "focus",
        "limits",
    }
    assert packet["scope"] == "working"
    assert packet["range"] == "HEAD"
    assert packet["base_ref"] is None
    assert packet["base_commit"] == head
    assert packet["head_commit"] == head
    assert set(packet["stats"]) == {"files", "additions", "deletions", "by_status", "languages"}
    assert packet["stats"]["files"] == 2
    assert packet["stats"]["by_status"] == {"added": 1, "modified": 1}
    assert packet["stats"]["languages"] == {"python": 2}

    entry = _file_entry(packet, "app.py")
    assert set(entry) == {
        "path",
        "old_path",
        "status",
        "additions",
        "deletions",
        "language",
        "priority",
        "signals",
        "findings",
        "function_deltas",
    }
    assert entry["status"] == "modified"
    assert entry["language"] == "python"
    assert entry["priority"] in {"high", "medium", "low"}
    for finding in entry["findings"]:
        assert {"rule", "severity", "category", "line", "confidence", "message"} <= set(finding)

    # 文件排序：priority 升序（high→low），同级按 additions 降序
    order = [(_PRIORITY_RANK[item["priority"]], -item["additions"]) for item in packet["files"]]
    assert order == sorted(order)
    assert packet["limits"]["truncated"] is False
    json.dumps(packet, ensure_ascii=False)  # packet 必须可 JSON 序列化


def test_staged_scope_only_returns_staged_changes(baseline_repo: Path):
    """staged scope 不包含工作区未暂存改动与未跟踪文件。"""
    _write(baseline_repo, "app.py", _BASELINE_APP + _UNSAFE_APPEND)
    _run_git(baseline_repo, "add", "app.py")
    _write(baseline_repo, "unstaged.py", "def nothing():\n    return None\n")

    packet = review_change_set(scope="staged", repo_root=str(baseline_repo))

    paths = [item["path"] for item in packet["files"]]
    assert "app.py" in paths
    assert "unstaged.py" not in paths
    assert packet["scope"] == "staged"
    assert packet["range"] == "HEAD"


def test_staged_scope_reads_index_version(baseline_repo: Path):
    """staged 模式必须以 index 版本为准：工作区再改也不影响分析与行号。"""
    staged_source = _BASELINE_APP + "\n\ndef danger(value):\n    return eval(value)\n"
    _write(baseline_repo, "app.py", staged_source)
    _run_git(baseline_repo, "add", "app.py")
    # 工作区在 index 之上再改：删掉 eval 并改变行数；若读工作区则不会有 SEC001
    _write(
        baseline_repo,
        "app.py",
        "def safe():\n    return 1\n\n\ndef other():\n    return 2\n",
    )

    packet = review_change_set(scope="staged", repo_root=str(baseline_repo))
    entry = _file_entry(packet, "app.py")

    hits = [finding["line"] for finding in entry["findings"] if finding["rule"] == "SEC001"]
    assert hits == [len(staged_source.splitlines())], "必须分析 index 版本，行号按 index 计算"
    assert entry["additions"] == len(staged_source.splitlines()) - len(_BASELINE_APP.splitlines())


def test_base_scope_reads_head_version(baseline_repo: Path):
    """base 模式分析 HEAD 版本：工作区偏离 HEAD 也不影响结论。"""
    _run_git(baseline_repo, "checkout", "-b", "feature")
    committed = _BASELINE_APP + "\n\ndef danger(value):\n    return eval(value)\n"
    _write(baseline_repo, "app.py", committed)
    _commit(baseline_repo, "feature adds eval")
    # 工作区再把 eval 去掉并改变行数
    _write(baseline_repo, "app.py", "def safe():\n    return 1\n")

    packet = review_change_set(scope="base", base="main", repo_root=str(baseline_repo))
    entry = _file_entry(packet, "app.py")

    hits = [finding["line"] for finding in entry["findings"] if finding["rule"] == "SEC001"]
    assert hits == [len(committed.splitlines())], "必须分析 HEAD 版本，行号按 HEAD 计算"


def test_base_scope_uses_merge_base_and_function_deltas(baseline_repo: Path):
    """base scope：range / base_commit 正确，且带回函数复杂度增长。"""
    main_sha = _run_git(baseline_repo, "rev-parse", "--short=7", "HEAD").strip()
    _run_git(baseline_repo, "checkout", "-b", "feature")
    _write(baseline_repo, "app.py", _COMPLEX_APP)
    _write(
        baseline_repo,
        "tests/test_app.py",
        _BASELINE_TEST + "\n\ndef test_more():\n    assert True\n",
    )
    _commit(baseline_repo, "feature work")

    target = collect_review_target(scope="base", base="main", repo_root=str(baseline_repo))
    assert isinstance(target, ReviewTarget)
    assert target.range_spec == "main...HEAD"
    assert target.base_ref == "main"
    assert target.base_commit == main_sha

    packet = review_change_set(scope="base", base="main", repo_root=str(baseline_repo))
    assert packet["scope"] == "base"
    assert packet["range"] == "main...HEAD"
    assert packet["base_commit"] == main_sha

    entry = _file_entry(packet, "app.py")
    deltas = entry["function_deltas"]
    assert deltas, "app.py 的 run 复杂度上升，应产生 function_delta"
    assert deltas[0]["name"] == "run"
    assert deltas[0]["complexity_after"] > deltas[0]["complexity_before"]
    assert {
        "name",
        "line",
        "complexity_before",
        "complexity_after",
        "length_before",
        "length_after",
    } == set(deltas[0])
    # 复杂度增长达到 3 → medium 及以上
    assert entry["priority"] in {"high", "medium"}


def test_no_changes_returns_empty_packet(baseline_repo: Path):
    """没有改动时 packet 所有集合为空。"""
    packet = review_change_set(scope="working", repo_root=str(baseline_repo))

    assert packet["files"] == []
    assert packet["signals"] == []
    assert packet["focus"] == []
    assert packet["stats"]["files"] == 0
    assert packet["limits"] == {"skipped": [], "truncated": False, "notes": []}


# ==================== findings 只落在新增行 ====================


def test_findings_only_on_added_lines(baseline_repo: Path):
    """基线里已有的 eval 不报；新增行上的 eval 要报。"""
    _write(baseline_repo, "app.py", _BASELINE_APP + _UNSAFE_APPEND)

    target = collect_review_target(scope="working", repo_root=str(baseline_repo))
    added = set()
    for fd in parse_unified_diff(target.diff_text):
        if fd.path == "app.py":
            added = fd.added_lines
    assert 5 not in added, "基线行不应出现在新增行里"
    assert added == set(range(6, 11))

    packet = review_change_set(scope="working", repo_root=str(baseline_repo))
    entry = _file_entry(packet, "app.py")

    hits = [(finding["rule"], finding["line"]) for finding in entry["findings"]]
    assert ("SEC001", 5) not in hits, "基线行号 5 的问题不应被报告"
    assert any(rule == "SEC001" and line in added for rule, line in hits)
    for finding in entry["findings"]:
        assert finding["line"] in added, f"finding 不在新增行：{finding}"


def test_untracked_file_all_lines_are_added(baseline_repo: Path):
    """未跟踪文件按全文件新增处理，问题落在对应行。"""
    _write(
        baseline_repo,
        "brand_new.py",
        '"""新文件。"""\n\n\ndef broken():\n    return eval("1")\n',
    )

    packet = review_change_set(scope="working", repo_root=str(baseline_repo))
    entry = _file_entry(packet, "brand_new.py")

    assert entry["status"] == "added"
    assert entry["additions"] == 5
    lines = [finding["line"] for finding in entry["findings"] if finding["rule"] == "SEC001"]
    assert lines == [5]


def test_findings_deduplicated_and_per_file_capped(baseline_repo: Path):
    """同一 (rule, line) 不重复；每文件最多 10 条并标记 truncated。"""
    blocks = "\n\n".join(f"def f{index}(value):\n    return eval(value)" for index in range(14))
    _write(baseline_repo, "bad.py", blocks + "\n")

    packet = review_change_set(scope="working", repo_root=str(baseline_repo))
    entry = _file_entry(packet, "bad.py")

    assert len(entry["findings"]) == 10
    keys = [(finding["rule"], finding["line"]) for finding in entry["findings"]]
    assert len(keys) == len(set(keys))
    assert packet["limits"]["truncated"] is True
    assert any("bad.py" in note for note in packet["limits"]["notes"])


def test_max_findings_budget(baseline_repo: Path):
    """整体 max_findings 生效并标记 truncated。"""
    blocks = "\n\n".join(f"def g{index}(value):\n    return eval(value)" for index in range(8))
    _write(baseline_repo, "many.py", blocks + "\n")

    packet = review_change_set(scope="working", repo_root=str(baseline_repo), max_findings=3)

    assert len(_all_findings(packet)) <= 3
    assert packet["limits"]["truncated"] is True


def test_findings_budget_keeps_signals_and_priority(baseline_repo: Path):
    """整体预算耗尽后，后续文件仍然计算 signals 与 priority（只是不再输出 finding）。"""
    _write(baseline_repo, "first_bad.py", 'password = "hunter2hunter2"\n')
    _write(baseline_repo, "second_bad.py", 'token = "abcdef1234567"\n')

    packet = review_change_set(scope="working", repo_root=str(baseline_repo), max_findings=1)

    assert len(_all_findings(packet)) == 1
    assert packet["limits"]["truncated"] is True

    second = _file_entry(packet, "second_bad.py")
    assert second["findings"] == []
    assert second["priority"] == "high", "预算外的 error 命中仍应抬高 priority"
    assert ("secret_added", "second_bad.py") in {
        (signal["kind"], signal["path"]) for signal in packet["signals"]
    }, "预算外的密钥命中仍应产出 secret_added signal"


def test_max_files_truncation(baseline_repo: Path):
    """文件数超过 max_files 时截断并记 note。"""
    for index in range(4):
        _write(baseline_repo, f"mod_{index}.py", f"def fn_{index}():\n    return {index}\n")

    packet = review_change_set(scope="working", repo_root=str(baseline_repo), max_files=2)

    assert len(packet["files"]) == 2
    assert packet["limits"]["truncated"] is True
    assert any("max_files" in note or "上限" in note for note in packet["limits"]["notes"])


# ==================== signals / priority / focus ====================


def test_change_signals_priority_and_focus(baseline_repo: Path):
    """至少三类 signal + 文件优先级 + focus 排序。"""
    _write(baseline_repo, "requirements.txt", "requests>=2.28.0\nhttpx>=0.27.0\n")
    _write(baseline_repo, "service_utils.py", _SERVICE_UTILS)
    _write(
        baseline_repo,
        "tests/test_app.py",
        _BASELINE_TEST + "\n\ndef test_extra():\n    assert True\n",
    )

    packet = review_change_set(scope="working", repo_root=str(baseline_repo))

    signal_kinds = {(signal["kind"], signal["path"]) for signal in packet["signals"]}
    assert ("dependency_added", "requirements.txt") in signal_kinds
    assert ("test_change", "tests/test_app.py") in signal_kinds

    dependency = next(
        signal
        for signal in packet["signals"]
        if signal["kind"] == "dependency_added" and "httpx" in signal["detail"]
    )
    assert dependency["priority"] == "high"
    assert dependency["line"] == 2

    service = _file_entry(packet, "service_utils.py")
    # 本次变更集已经改过测试文件 → 不再逐文件断言「缺测试」
    assert "tests_missing" not in service["signals"]
    assert "protection_bypass" in service["signals"]
    assert service["priority"] == "medium"

    # signal 排序：priority 非递减
    ranks = [_PRIORITY_RANK[signal["priority"]] for signal in packet["signals"]]
    assert ranks == sorted(ranks)

    # focus：有内容、不超过 12 条、high 级 signal 参与
    assert packet["focus"]
    assert len(packet["focus"]) <= 12
    assert any("dependency_added" in item for item in packet["focus"])
    assert packet["focus"][0].startswith("requirements.txt:2")
    json.dumps(packet, ensure_ascii=False)


def test_tests_missing_signal_when_no_test_change(baseline_repo: Path):
    """源码新增 ≥10 行、变更集没有动测试 → 报 tests_missing 并提到 medium。"""
    _write(baseline_repo, "service_utils.py", _SERVICE_UTILS)

    packet = review_change_set(scope="working", repo_root=str(baseline_repo))

    service = _file_entry(packet, "service_utils.py")
    assert "tests_missing" in service["signals"]
    assert service["priority"] == "medium"


def test_protection_bypass_ignores_docs(baseline_repo: Path):
    """文档里提到 `# noqa` / `# type: ignore` 不算绕过检查。"""
    _write(
        baseline_repo,
        "README.md",
        "# 示例仓库\n\n临时绕过可以用 `# type: ignore` 或 `# noqa`。\n",
    )

    packet = review_change_set(scope="working", repo_root=str(baseline_repo))

    readme = _file_entry(packet, "README.md")
    assert "protection_bypass" not in readme["signals"]


def test_test_file_complexity_is_not_reported(baseline_repo: Path):
    """测试文件里的大函数不产生 COMPLEX* 命中（属噪声）。"""
    body = "\n".join(f"    if {index} == 0:\n        assert True" for index in range(60))
    _write(baseline_repo, "tests/test_app.py", _BASELINE_TEST + f"\n\ndef test_big():\n{body}\n")

    packet = review_change_set(scope="working", repo_root=str(baseline_repo))

    entry = _file_entry(packet, "tests/test_app.py")
    assert any(
        signal["kind"] == "test_change" and signal["path"] == "tests/test_app.py"
        for signal in packet["signals"]
    )
    assert not [finding for finding in entry["findings"] if finding["rule"].startswith("COMPLEX")]


def test_focus_reports_length_when_complexity_unchanged(baseline_repo: Path):
    """只有长度增长的函数，focus 文案应写 length 而不是 complexity 1→1。"""
    grown = '''"""基线示例模块。"""


def run(value):
    first = value
    second = first
    third = second
    fourth = third
    fifth = fourth
    sixth = fifth
    seventh = sixth
    eighth = seventh
    ninth = eighth
    tenth = ninth
    eleventh = tenth
    twelfth = eleventh
    thirteenth = twelfth
    fourteenth = thirteenth
    return eval(fourteenth)
'''
    _write(baseline_repo, "app.py", grown)

    packet = review_change_set(scope="working", repo_root=str(baseline_repo))

    focus_items = [item for item in packet["focus"] if "run" in item]
    assert any("length " in item for item in focus_items)
    assert not any("complexity 1→1" in item for item in packet["focus"])


def test_dependency_signal_ignores_package_metadata(baseline_repo: Path):
    """`version` / `requires-python` 等元数据不算依赖新增。"""
    _write(
        baseline_repo,
        "pyproject.toml",
        '[project]\nname = "demo"\nversion = "0.4.0"\nrequires-python = ">=3.10"\n'
        'dependencies = [\n    "httpx>=0.27",\n]\n',
    )

    packet = review_change_set(scope="working", repo_root=str(baseline_repo))
    details = [
        signal["detail"] for signal in packet["signals"] if signal["kind"] == "dependency_added"
    ]

    assert any("httpx" in detail for detail in details)
    assert not any("version" in detail for detail in details)
    assert not any("requires-python" in detail for detail in details)

    kinds = {signal["kind"] for signal in packet["signals"]}
    assert "config_change" in kinds


def test_ignored_and_binary_files_skipped(baseline_repo: Path):
    """ignore_paths 命中与二进制文件都进 limits.skipped。"""
    _write(baseline_repo, "generated/gen.py", "def gen():\n    return 1\n")
    (baseline_repo / "blob.bin").write_bytes(b"\x00\x01\x02binary")

    config = Config(ignore_paths=["generated/"])
    packet = review_change_set(scope="working", config=config, repo_root=str(baseline_repo))

    skipped = {item["path"]: item["reason"] for item in packet["limits"]["skipped"]}
    assert skipped.get("generated/gen.py") == "ignored"
    assert skipped.get("blob.bin") == "binary"

    paths = [item["path"] for item in packet["files"]]
    assert "generated/gen.py" not in paths
    assert "blob.bin" not in paths


def test_deleted_test_file_entry(baseline_repo: Path):
    """删除测试文件：保留 deleted 条目、priority 至少 medium、带 deleted_test_assertions。"""
    (baseline_repo / "tests" / "test_app.py").unlink()

    packet = review_change_set(scope="working", repo_root=str(baseline_repo))
    entry = _file_entry(packet, "tests/test_app.py")

    assert entry["status"] == "deleted"
    assert entry["deletions"] >= 3
    assert entry["additions"] == 0
    assert entry["findings"] == []
    assert entry["priority"] == "medium", "删除带测试/保护的文件不应埋在 low"
    assert "deleted_test_assertions" in entry["signals"]
    assert ("test_change", "tests/test_app.py") in {
        (signal["kind"], signal["path"]) for signal in packet["signals"]
    }


def test_deleted_file_priority_follows_signals(baseline_repo: Path):
    """删除高风险文件（迁移/schema）→ high；无任何 signal 的删除 → low。"""
    _write(baseline_repo, "migrations/0001_init.sql", "CREATE TABLE demo (id INTEGER);\n")
    _commit(baseline_repo, "add migration")
    (baseline_repo / "migrations" / "0001_init.sql").unlink()
    (baseline_repo / "README.md").unlink()

    packet = review_change_set(scope="working", repo_root=str(baseline_repo))

    migration = _file_entry(packet, "migrations/0001_init.sql")
    assert migration["status"] == "deleted"
    assert migration["priority"] == "high"
    assert ("database_schema", "migrations/0001_init.sql") in {
        (signal["kind"], signal["path"]) for signal in packet["signals"]
    }

    readme = _file_entry(packet, "README.md")
    assert readme["status"] == "deleted"
    assert readme["priority"] == "low"


def test_deleted_assertions_in_modified_test_file(baseline_repo: Path):
    """测试文件被改写且删掉了断言：仍然给出 deleted_test_assertions。"""
    _write(
        baseline_repo,
        "tests/test_app.py",
        'from app import run\n\n\ndef test_run():\n    values = ["1 + 1"]\n'
        "    assert run(values[0]) == 2\n",
    )

    packet = review_change_set(scope="working", repo_root=str(baseline_repo))
    entry = _file_entry(packet, "tests/test_app.py")

    assert "deleted_test_assertions" in entry["signals"]
    assert entry["priority"] in {"medium", "low"}


def test_large_file_change_signal(baseline_repo: Path):
    """单文件新增超过 400 行 → large_file_change。"""
    lines = [f"VALUE_{index} = {index}" for index in range(420)]
    _write(baseline_repo, "big.py", "\n".join(lines) + "\n")

    packet = review_change_set(scope="working", repo_root=str(baseline_repo))
    entry = _file_entry(packet, "big.py")

    assert entry["additions"] == 420
    assert "large_file_change" in entry["signals"]


def test_large_change_signal_when_many_files(baseline_repo: Path):
    """文件数超过 40 → 变更级 large_change，并触发 max_files 截断。"""
    for index in range(41):
        _write(baseline_repo, f"m_{index}.py", f"def fn_{index}():\n    return {index}\n")

    packet = review_change_set(scope="working", repo_root=str(baseline_repo))

    assert packet["stats"]["files"] == 41
    assert any(signal["kind"] == "large_change" for signal in packet["signals"])
    assert len(packet["files"]) == 40
    assert packet["limits"]["truncated"] is True


# ==================== python_function_deltas ====================

_OLD_FUNCS = '''"""旧版本。"""


def simple(value):
    return value


class Calc:
    def total(self, items):
        return sum(items)
'''

_NEW_FUNCS = '''"""新版本。"""


def simple(value):
    if value is None:
        return 0
    if value == "":
        return 1
    if value == "x":
        return 2
    return value


class Calc:
    def total(self, items):
        if not items:
            return 0
        if len(items) > 10:
            return 10
        return sum(items)
'''


def test_python_function_deltas_reports_complexity_growth():
    """同名函数复杂度增长要被报告，并带完整字段。"""
    deltas = python_function_deltas(_OLD_FUNCS, _NEW_FUNCS, "demo.py")

    names = [delta["name"] for delta in deltas]
    assert names[0] == "simple"
    assert "Calc.total" in names
    assert set(deltas[0]) == {
        "name",
        "line",
        "complexity_before",
        "complexity_after",
        "length_before",
        "length_after",
    }
    assert deltas[0]["complexity_before"] == 1
    assert deltas[0]["complexity_after"] >= 3
    assert all(
        delta["complexity_after"] > delta["complexity_before"]
        or delta["length_after"] > delta["length_before"] * 1.5
        for delta in deltas
    )
    assert len(deltas) <= 5


def test_python_function_deltas_nested_qualname():
    """嵌套函数用 outer.<locals>.inner 拼接 qualname。"""
    old_source = "def outer(flag):\n    def inner(value):\n        return value\n    return inner\n"
    new_source = (
        "def outer(flag):\n"
        "    def inner(value):\n"
        "        if value is None:\n"
        "            return 0\n"
        "        if value == '':\n"
        "            return 1\n"
        "        return value\n"
        "    return inner\n"
    )
    names = [delta["name"] for delta in python_function_deltas(old_source, new_source)]
    assert "outer.<locals>.inner" in names


def test_python_function_deltas_ignores_unchanged_and_new_functions():
    """没有增长、只在新版本出现的函数都不报告。"""
    assert python_function_deltas(_NEW_FUNCS, _NEW_FUNCS) == []
    assert python_function_deltas("", "def fresh():\n    if True:\n        return 1\n") == []


def test_python_function_deltas_handles_parse_errors():
    """任一侧解析失败返回空列表。"""
    assert python_function_deltas("def broken(:", _NEW_FUNCS) == []
    assert python_function_deltas(_OLD_FUNCS, "  ???") == []
    assert python_function_deltas("", "") == []


# ==================== QA 复审回归（v0.3.0）====================


def test_missing_directory_is_not_reported_as_git_environment(tmp_path: Path):
    """目录不存在时直接说明目录问题，而不是建议安装 git。"""
    missing = tmp_path / "nope"
    packet = review_change_set(scope="working", repo_root=str(missing))

    assert "error" in packet
    assert "目录不存在或不是目录" in packet["error"]
    assert "git" not in packet["error"]


def test_file_path_as_directory_is_rejected(tmp_path: Path):
    """directory 指向文件时同样明确拒绝，而不是当成 git 故障。"""
    target = tmp_path / "single.py"
    target.write_text("x = 1\n", encoding="utf-8")

    packet = review_change_set(scope="working", repo_root=str(target))

    assert "目录不存在或不是目录" in packet["error"]


def test_subdirectory_scope_only_reviews_that_subdir(baseline_repo: Path):
    """directory 指向仓库子目录时只审该子目录，不静默扩大到整仓。"""
    _write(baseline_repo, "pkg/a.py", "x = 1\n")
    _write(baseline_repo, "other/c.py", "z = 1\n")
    _commit(baseline_repo, "add packages")
    _write(baseline_repo, "pkg/a.py", "x = 2\n")
    _write(baseline_repo, "other/c.py", "z = 2\n")

    packet = review_change_set(scope="working", repo_root=str(baseline_repo / "pkg"))

    assert [entry["path"] for entry in packet["files"]] == ["pkg/a.py"]
    assert any("子目录" in note for note in packet["limits"]["notes"])


def test_eval_change_is_security_finding_not_secret(baseline_repo: Path):
    """新增 eval 应归为 security_finding，不能叫「新增密钥」。"""
    _write(baseline_repo, "eval_case.py", "value = eval('1 + 1')\n")

    packet = review_change_set(scope="working", repo_root=str(baseline_repo))

    kinds = {signal["kind"] for signal in packet["signals"] if signal["path"] == "eval_case.py"}
    assert "security_finding" in kinds
    assert "secret_added" not in kinds


def test_two_secrets_on_same_line_both_reported(baseline_repo: Path):
    """同一行两个不同的硬编码密钥都要保留（去重键含 message）。"""
    _write(
        baseline_repo,
        "secrets.py",
        'password = "hunter2hunter2"; token = "abcdef1234567"\n',
    )

    packet = review_change_set(scope="working", repo_root=str(baseline_repo))

    entry = _file_entry(packet, "secrets.py")
    secret_findings = [item for item in entry["findings"] if item["rule"] == "SEC005"]
    assert len(secret_findings) == 2


def test_protection_bypass_cap_is_reported(tmp_path: Path):
    """超过文件数上限时补 note + truncated，而不是静默丢弃。"""
    repo = _init_repo(tmp_path, "cap-repo")
    for index in range(4):
        _write(repo, f"m{index}.py", "x = 1\n")
    _commit(repo, "baseline")
    for index in range(4):
        _write(repo, f"m{index}.py", "x = 1\n# type: ignore\n")

    packet = review_change_set(scope="working", repo_root=str(repo))

    protected = [
        entry["path"] for entry in packet["files"] if "protection_bypass" in entry["signals"]
    ]
    assert len(protected) == 3
    assert packet["limits"]["truncated"] is True
    assert any("protection_bypass 最多标注" in note for note in packet["limits"]["notes"])


def test_quoted_utf8_path_is_unescaped():
    """core.quotepath=true 时的八进制转义路径要还原成真实 UTF-8。"""
    from code_review_mcp.analyzers.change import _unescape_git_path

    assert _unescape_git_path('"\\344\\270\\255\\346\\226\\207.py"') == "中文.py"
    assert _unescape_git_path("plain/path.py") == "plain/path.py"
    assert _unescape_git_path('"a\\\\b.py"') == "a\\b.py"


def test_parse_unified_diff_quoted_path():
    """带引号+八进制路径的 diff 头部也要解析出正确文件路径。"""
    diff = (
        'diff --git "a/\\344\\270\\255\\346\\226\\207.py" '
        '"b/\\344\\270\\255\\346\\226\\207.py"\n'
        "new file mode 100644\n"
        "--- /dev/null\n"
        '+++ "b/\\344\\270\\255\\346\\226\\207.py"\n'
        "@@ -0,0 +1 @@\n"
        "+x = 1\n"
    )

    files = parse_unified_diff(diff)

    assert [item.path for item in files] == ["中文.py"]
