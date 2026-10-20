"""变更证据引擎：采集 git 变更并产出结构化审查证据（packet）。

设计要点（规格见 `.tmp/contract.md` §1）：
- 审查对象是「变更」而不是整个文件：findings 只保留落在新增行上的命中。
- 只产出事实（findings / signals / function_deltas），不产出 verdict 与健康分。
- 所有输出有界：max_files / max_findings / 每文件 10 条 / signals 30 条，
  任何截断都写进 `limits`。
- git 调用失败不抛异常，统一转成 `{"error", "hint"}` 结构化错误。

公开 API：
- collect_review_target：采集 git diff 与仓库元信息。
- parse_unified_diff：解析 unified diff 为 FileDiff 列表。
- review_change_set：产出可直接交给审查者的 packet。
- python_function_deltas：比较两版 Python 函数的复杂度/长度增长。
"""

from __future__ import annotations

import ast
import io
import os
import re
import subprocess
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config, is_path_ignored, load_config
from ..rules import registry
from ..rules.complexity import compute_cyclomatic_complexity
from ..utils import EXCLUDE_DIRS, MAX_FILE_SIZE, detect_language
from .context import analyze_file_content

# ==================== 常量 ====================

_GIT_TIMEOUT = 30  # 单条 git 命令超时（秒）
_MAX_FINDINGS_PER_FILE = 10  # 每个文件最多保留的 findings
_MAX_SIGNALS = 30  # 变更级 signal 总数上限
_MAX_PROTECTION_FILES = 3  # protection_bypass 最多标注的文件数
_MAX_DEP_SIGNALS_PER_FILE = 5  # dependency_added 每文件最多条数
_MAX_FUNCTION_DELTAS = 5  # 每个文件最多 function_delta 条数
_FOCUS_MAX = 12  # focus 最多条数

_LARGE_FILE_ADDITIONS = 400  # large_file_change 阈值
_LARGE_CHANGE_ADDITIONS = 1000  # large_change 阈值（总新增行）
_LARGE_CHANGE_FILES = 40  # large_change 阈值（文件数）
_TESTS_MISSING_MIN_ADDITIONS = 10  # tests_missing 要求的最小新增行数
_LENGTH_GROWTH_MIN_DELTA = 10  # 仅长度增长时，至少多出这么多行才算回归（避免 8→13 的噪声）
_COMPLEXITY_HIGH_GROWTH = 5  # 复杂度增长达到该值直接判 high

_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}
_STATUS_ORDER = ("added", "modified", "deleted", "renamed")

_NON_GIT_HINT = "review_changes 需要 git 仓库；单文件请用 analyze_file"
_NO_BASE_HINT = 'base scope 需要指定基线分支，例如 base="main"；或改用 scope="working"'


# ==================== 数据结构 ====================


@dataclass
class FileDiff:
    """单个文件的 diff 事实。"""

    path: str  # 新路径；deleted 时用旧路径
    old_path: str | None  # 仅 renamed 时为旧路径，其余为 None
    status: str  # "added" | "modified" | "deleted" | "renamed"
    additions: int  # 新增行数
    deletions: int  # 删除行数
    added_lines: set[int]  # 新文件行号（1-based）
    removed_lines: set[int]  # 旧文件行号（1-based）
    is_binary: bool = False


@dataclass
class ReviewTarget:
    """一次审查目标的采集结果。"""

    scope: str
    base_ref: str | None
    base_commit: str | None  # 短 sha（7 位）
    head_commit: str | None  # 短 sha（7 位）
    range_spec: str  # 例如 "HEAD" / "main...HEAD"
    diff_text: str
    untracked: list[str] = field(default_factory=list)  # 仅 working 模式
    repo_root: str = ""
    scope_prefix: str | None = None  # directory 是仓库子目录时的相对前缀


# ==================== git 调用 ====================


def _error(message: str, hint: str) -> dict:
    """构造结构化错误（不抛异常，直接返回给调用方）。"""
    return {"error": message, "hint": hint}


def _git(args: list[str], repo_root: str) -> tuple[bool, str, str]:
    """执行一条 git 命令。

    返回 (是否成功, stdout, stderr)；任何失败都不抛异常。
    统一关闭 quotepath，避免中文/特殊字符路径被转义成 "\\346\\226\\207"。
    """
    cmd = ["git", "-c", "core.quotepath=false", *args]
    try:
        proc = subprocess.run(
            cmd,
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT,
        )
    except FileNotFoundError:
        return False, "", "未找到 git 命令"
    except subprocess.TimeoutExpired:
        return False, "", "git 命令执行超时"
    except OSError as exc:  # 例如 cwd 权限不足、路径不是目录
        return False, "", f"git 启动失败: {exc}"

    return proc.returncode == 0, proc.stdout or "", (proc.stderr or "").strip()


def _git_environment_error(stderr: str) -> str | None:
    """判断 git 失败是否属于「环境问题」（未安装/超时）。

    "启动失败" 不算环境缺失：目录存在但不可访问时不能建议用户去装 git。
    """
    for marker in ("未找到 git 命令", "执行超时"):
        if marker in stderr:
            return marker
    return None


# ==================== unified diff 解析 ====================

_DIFF_GIT_RE = re.compile(r'^diff --git "?a/(?P<a>.+?)"? "?b/(?P<b>.+?)"?$')
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


_ESCAPE_SIMPLE = {
    "n": b"\n",
    "t": b"\t",
    "r": b"\r",
    "a": b"\a",
    "b": b"\b",
    "f": b"\f",
    "v": b"\v",
    "\\": b"\\",
    '"': b'"',
}


def _read_escape(body: str, index: int) -> tuple[bytes | None, int]:
    """解析 `body[index]` 处的反斜杠转义；返回 (字节, 下一个下标)。"""
    if index + 1 >= len(body):
        return None, index + 1
    nxt = body[index + 1]
    if nxt in "01234567":
        digits = ""
        cursor = index + 1
        while cursor < len(body) and len(digits) < 3 and body[cursor] in "01234567":
            digits += body[cursor]
            cursor += 1
        return bytes([int(digits, 8) & 0xFF]), cursor
    decoded = _ESCAPE_SIMPLE.get(nxt)
    if decoded is None:
        return None, index + 1
    return decoded, index + 2


def _decode_c_escapes(body: str) -> str:
    """把 git 的 C 转义字节流还原成 UTF-8 字符串。"""
    buffer = bytearray()
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\":
            decoded, next_index = _read_escape(body, index)
            if decoded is not None:
                buffer.extend(decoded)
                index = next_index
                continue
        buffer.extend(char.encode("utf-8"))
        index += 1
    return buffer.decode("utf-8", errors="replace")


def _unescape_git_path(value: str) -> str:
    """还原 git 对路径的 C 风格转义（引号、反斜杠、八进制字节）。

    `git` 在 `core.quotepath=true`（默认）时会把非 ASCII 路径输出成
    `"a/\\346\\226\\207..."`；调用方可能已剥掉引号（正则分组），因此这里
    只要发现反斜杠就按 C 转义解码，再整体 UTF-8 解码，非法字节用 replace 兜底。
    真实文件名里的反斜杠会被 git 转义，所以不会误伤普通路径。
    """
    body = value
    if len(body) >= 2 and body.startswith('"') and body.endswith('"'):
        body = body[1:-1]
    if "\\" not in body:
        return body
    return _decode_c_escapes(body)


def _strip_diff_prefix(raw: str, prefix: str) -> str:
    """把 `a/xxx` / `b/xxx` 还原成仓库相对路径。"""
    path = _unescape_git_path(raw.strip())
    if path.startswith(prefix):
        return path[len(prefix) :]
    return path


@dataclass
class _DiffCursor:
    """unified diff 解析过程中的行号与 hunk 状态。"""

    old_line: int = 0
    new_line: int = 0
    old_remaining: int = 0
    new_remaining: int = 0
    in_hunk: bool = False
    old_display: str | None = None  # `--- a/path` 记录的旧路径
    rename_old: str | None = None  # `rename from` 记录的旧路径

    def reset(self) -> None:
        """文件块结束时清空跨文件的元信息。"""
        self.in_hunk = False
        self.old_display = None
        self.rename_old = None

    def start_hunk(self, match: re.Match) -> None:
        """按 `@@ -a,b +c,d @@` 重置行号与剩余行数。"""
        self.old_line = int(match.group(1))
        self.old_remaining = int(match.group(2)) if match.group(2) is not None else 1
        self.new_line = int(match.group(3))
        self.new_remaining = int(match.group(4)) if match.group(4) is not None else 1
        self.in_hunk = True

    def consume(self, current: FileDiff, line: str) -> bool:
        """若该行属于 hunk 正文则消费并返回 True；否则交回元信息处理。"""
        if not self.in_hunk:
            return False
        if self.old_remaining <= 0 and self.new_remaining <= 0:
            self.in_hunk = False
            return False
        if line.startswith("+"):
            current.added_lines.add(self.new_line)
            current.additions += 1
            self.new_line += 1
            self.new_remaining -= 1
            return True
        if line.startswith("-"):
            current.removed_lines.add(self.old_line)
            current.deletions += 1
            self.old_line += 1
            self.old_remaining -= 1
            return True
        if line.startswith(" ") or line == "":
            self.old_line += 1
            self.new_line += 1
            self.old_remaining -= 1
            self.new_remaining -= 1
            return True
        # 不是 hunk 行（例如紧随其后的元信息），回落到元信息处理
        self.in_hunk = False
        return False


def _new_file_diff(header_line: str) -> FileDiff:
    """由 `diff --git a/x b/y` 行创建文件条目。"""
    header = _DIFF_GIT_RE.match(header_line)
    old_name = _unescape_git_path(header.group("a")) if header else ""
    new_name = _unescape_git_path(header.group("b")) if header else ""
    return FileDiff(
        path=new_name or old_name,
        old_path=None,
        status="modified",
        additions=0,
        deletions=0,
        added_lines=set(),
        removed_lines=set(),
    )


def _apply_file_metadata(current: FileDiff, line: str, cursor: _DiffCursor) -> bool:
    """处理文件级元信息（new/deleted/rename/binary/---/+++）；命中返回 True。"""
    if line.startswith("new file mode"):
        current.status = "added"
        return True
    if line.startswith("deleted file mode"):
        current.status = "deleted"
        return True
    if line.startswith("rename from "):
        cursor.rename_old = _unescape_git_path(line[len("rename from ") :].strip())
        current.old_path = cursor.rename_old or None
        current.status = "renamed"
        return True
    if line.startswith("rename to "):
        current.path = _unescape_git_path(line[len("rename to ") :].strip())
        current.status = "renamed"
        return True
    if line.startswith("Binary files ") or line == "GIT binary patch":
        current.is_binary = True
        return True
    if line.startswith("--- "):
        _apply_old_path(current, line[len("--- ") :].strip(), cursor)
        return True
    if line.startswith("+++ "):
        _apply_new_path(current, line[len("+++ ") :].strip())
        return True
    return False


def _apply_old_path(current: FileDiff, value: str, cursor: _DiffCursor) -> None:
    """`--- a/x`：记录旧路径展示名；`--- /dev/null` 表示新文件。"""
    if value == "/dev/null":
        current.status = "added"
        return
    cursor.old_display = _strip_diff_prefix(value, "a/")


def _apply_new_path(current: FileDiff, value: str) -> None:
    """`+++ b/x`：记录新路径；`+++ /dev/null` 表示删除。"""
    if value == "/dev/null":
        current.status = "deleted"
        return
    current.path = _strip_diff_prefix(value, "b/")


def _apply_diff_metadata(current: FileDiff, line: str, cursor: _DiffCursor) -> None:
    """处理文件元信息与 hunk 头；其余行按内容忽略。"""
    if _apply_file_metadata(current, line, cursor):
        return
    hunk = _HUNK_RE.match(line)
    if hunk:
        cursor.start_hunk(hunk)


def parse_unified_diff(diff_text: str) -> list[FileDiff]:
    """解析 git unified diff（可含多个文件），返回 FileDiff 列表。

    正确处理：new/deleted file mode、rename from/to、Binary files、GIT binary patch、
    `\\ No newline at end of file`、多 hunk、省略行数的 hunk 头（`@@ -1 +1 @@`）。
    解析失败的行按元信息忽略，不抛异常。
    """
    results: list[FileDiff] = []
    current: FileDiff | None = None
    cursor = _DiffCursor()

    def flush() -> None:
        nonlocal current
        if current is not None:
            # 删除的文件对外仍以旧路径标识
            if current.status == "deleted" and cursor.old_display:
                current.path = cursor.old_display
            results.append(current)
            current = None
        cursor.reset()

    for raw_line in diff_text.split("\n"):
        line = raw_line[:-1] if raw_line.endswith("\r") else raw_line

        # "\ No newline at end of file" 只是标记，不影响行号与内容
        if line.startswith("\\"):
            continue
        if line.startswith("diff --git "):
            flush()
            current = _new_file_diff(line)
            continue
        if current is None:
            continue
        if cursor.consume(current, line):
            continue
        _apply_diff_metadata(current, line, cursor)

    flush()
    return results


# ==================== 审查目标采集 ====================


@dataclass
class _TargetParts:
    """scope 无关的采集结果：diff 文本 + 基线信息 + 未跟踪文件。"""

    base_commit: str | None
    range_spec: str
    diff_text: str
    untracked: list[str]


def _resolve_base_commit(repo_root: str, base: str | None) -> tuple[str | None, dict | None]:
    """解析 base scope 的基线分支与 merge-base 提交。"""
    if not base:
        return None, _error("base scope 缺少 base 参数", _NO_BASE_HINT)

    ok, out, err = _git(["rev-parse", "--verify", f"{base}^{{commit}}"], repo_root)
    if not ok:
        return None, _error(
            f"无法解析基线分支 `{base}`：{err or '分支不存在'}",
            '确认 base 分支存在（本地或远端）后重试，或改用 scope="working"',
        )

    ok_mb, mb_out, mb_err = _git(["merge-base", base, "HEAD"], repo_root)
    if not ok_mb or not mb_out.strip():
        return None, _error(
            f"无法计算 `{base}` 与 HEAD 的 merge-base：{mb_err or '无共同祖先'}",
            '确认当前分支与 base 有共同祖先（或先提交代码），或改用 scope="working"',
        )
    return mb_out.strip()[:7], None


def _diff_args(*prefix: str) -> list[str]:
    """统一 diff 参数：禁用外部 diff 与颜色、开启重命名检测、3 行上下文。"""
    return ["diff", *prefix, "--no-color", "--no-ext-diff", "-M", "--unified=3"]


def _run_diff(repo_root: str, *prefix: str) -> tuple[str | None, dict | None]:
    """执行一条 git diff，返回 diff 文本；失败返回结构化错误。"""
    ok, out, err = _git(_diff_args(*prefix), repo_root)
    if not ok:
        return None, _error(
            f"git diff 执行失败：{err or '未知错误'}",
            "检查仓库状态（例如 Git 锁文件）后重试",
        )
    return out, None


def _list_untracked(repo_root: str) -> list[str]:
    """列出未跟踪文件（统一使用 / 分隔的仓库相对路径）。"""
    ok, out, _ = _git(["ls-files", "--others", "--exclude-standard"], repo_root)
    if not ok:
        return []
    return [item.strip().replace("\\", "/") for item in out.splitlines() if item.strip()]


def _collect_base_parts(repo_root: str, base: str | None) -> _TargetParts | dict:
    """base scope：base...HEAD（基于 merge-base 的三点 diff）。"""
    base_commit, error = _resolve_base_commit(repo_root, base)
    if error is not None:
        return error
    diff_text, error = _run_diff(repo_root, str(base_commit), "HEAD")
    if error is not None:
        return error
    return _TargetParts(base_commit, f"{base}...HEAD", diff_text or "", [])


def _staged_parts(repo_root: str, head_commit: str | None) -> _TargetParts | dict:
    """staged scope：index 相对 HEAD 的改动（无 commit 时相对空树）。"""
    prefix = ("--cached", "HEAD") if head_commit else ("--cached",)
    diff_text, error = _run_diff(repo_root, *prefix)
    if error is not None:
        return error
    range_spec = "HEAD" if head_commit else "index"
    return _TargetParts(head_commit, range_spec, diff_text or "", [])


def _working_diff_text(repo_root: str, head_commit: str | None) -> tuple[str, dict | None]:
    """working scope 的 diff 文本；无 commit 时 cached + unstaged 都按「全新增」处理。"""
    if head_commit:
        diff_text, error = _run_diff(repo_root, "HEAD")
        if error is not None:
            return "", error
        return diff_text or "", None

    parts: list[str] = []
    for prefix in (("--cached",), ()):
        ok, out, _ = _git(_diff_args(*prefix), repo_root)
        if ok and out:
            parts.append(out)
    return "\n".join(part for part in parts if part), None


def _collect_worktree_parts(
    repo_root: str, scope: str, head_commit: str | None
) -> _TargetParts | dict:
    """staged / working scope：有 HEAD 时以 HEAD 为基线，无 commit 时按全新增处理。"""
    if scope == "staged":
        return _staged_parts(repo_root, head_commit)

    diff_text, error = _working_diff_text(repo_root, head_commit)
    if error is not None:
        return error
    range_spec = "HEAD" if head_commit else "working tree"
    return _TargetParts(head_commit, range_spec, diff_text, [])


def collect_review_target(
    scope: str = "working",
    base: str | None = None,
    repo_root: str | None = None,
) -> ReviewTarget | dict:
    """采集审查目标：git diff + 仓库/提交元信息 + 未跟踪文件清单。

    scope：
    - "working"：工作区未提交改动（staged + unstaged）+ 未跟踪文件。
    - "staged"：仅已暂存改动。
    - "base"：`base...HEAD`（三点，基于 merge-base）的改动。

    非 git 仓库、无 commit（base 模式）、git 执行失败时返回 `{"error", "hint"}`。
    `repo_root` 指向仓库子目录时，`scope_prefix` 记录相对路径，由调用方限定审查范围。
    """
    if scope not in ("working", "staged", "base"):
        return _error(f"未知 scope: {scope}", 'scope 只支持 "working" / "staged" / "base"')

    start = repo_root or os.getcwd()
    if not os.path.isdir(start):
        return _error(
            f"目录不存在或不是目录: {start}",
            "请把 directory 指向存在的仓库目录；单文件审查请用 analyze_file",
        )

    actual_root = _resolve_repo_root(start)
    if isinstance(actual_root, dict):
        return actual_root

    ok_head, head_out, _ = _git(["rev-parse", "--short=7", "HEAD"], actual_root)
    head_commit = head_out.strip() if ok_head else None

    parts = _collect_parts(actual_root, scope, base, head_commit)
    if isinstance(parts, dict):
        return parts

    return ReviewTarget(
        scope=scope,
        base_ref=base,
        base_commit=parts.base_commit,
        head_commit=head_commit,
        range_spec=parts.range_spec,
        diff_text=parts.diff_text,
        untracked=_list_untracked(actual_root) if scope == "working" else [],
        repo_root=actual_root,
        scope_prefix=_subdir_prefix(start, actual_root),
    )


def _resolve_repo_root(start: str) -> str | dict:
    """解析 git 仓库根目录；失败时返回带 hint 的错误 dict。"""
    ok, out, err = _git(["rev-parse", "--show-toplevel"], start)
    if ok:
        return out.strip() or start
    if _git_environment_error(err):
        return _error("git 不可用", f"{err}；请安装 git 后重试")
    if err.startswith("git 启动失败"):
        return _error("git 无法启动", f"{err}；请检查目录权限后重试")
    return _error("当前目录不是 git 仓库", _NON_GIT_HINT)


def _collect_parts(
    repo_root: str,
    scope: str,
    base: str | None,
    head_commit: str | None,
) -> _TargetParts | dict:
    """按 scope 采集 base commit、range 描述与 diff 文本。"""
    if scope == "base":
        return _collect_base_parts(repo_root, base)
    return _collect_worktree_parts(repo_root, scope, head_commit)


def _subdir_prefix(requested: str, repo_root: str) -> str | None:
    """请求目录相对仓库根的路径；就是根目录时返回 None。"""
    try:
        requested_abs = os.path.realpath(requested)
        root_abs = os.path.realpath(repo_root)
    except OSError:
        return None
    if os.path.normcase(requested_abs) == os.path.normcase(root_abs):
        return None
    relative = os.path.relpath(requested_abs, root_abs).replace("\\", "/")
    if relative == "." or relative.startswith(".."):
        return None
    return relative.rstrip("/") or None


# ==================== 仓库文件索引 ====================


def _list_repo_files(repo_root: str) -> list[str]:
    """取仓库文件清单（git ls-files；失败则 os.walk，跳过 EXCLUDE_DIRS）。"""
    ok, out, _ = _git(["ls-files"], repo_root)
    if ok:
        return [item.strip().replace("\\", "/") for item in out.splitlines() if item.strip()]

    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [name for name in dirnames if name not in EXCLUDE_DIRS]
        for filename in filenames:
            absolute = os.path.join(dirpath, filename)
            files.append(os.path.relpath(absolute, repo_root).replace("\\", "/"))
    return files


class _RepoFileIndex:
    """仓库文件名索引：用于快速判断某个源代码文件是否已有对应测试。"""

    def __init__(self, paths: list[str]) -> None:
        self._bases: set[str] = set()  # 去扩展名的文件名
        self._test_dir_bases: set[str] = set()  # __tests__/ 下的去扩展名文件名

        for path in paths:
            parts = path.replace("\\", "/").split("/")
            name = parts[-1]
            if not name:
                continue
            base = name.rsplit(".", 1)[0] if "." in name else name
            self._bases.add(base)
            if "__tests__" in parts[:-1]:
                self._test_dir_bases.add(base)

    def has_test_for(self, path: str) -> bool:
        """判断 `path`（源代码文件）在仓库里是否已有对应测试文件。"""
        stem = Path(path).stem
        for suffix in (".test", ".spec"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
        if not stem:
            return False

        # tests/test_<stem>.py / test_<stem>.py（任意目录）
        if f"test_{stem}" in self._bases:
            return True
        # <stem>_test.py
        if f"{stem}_test" in self._bases:
            return True
        # <stem>.test.ts / <stem>.spec.ts
        if f"{stem}.test" in self._bases or f"{stem}.spec" in self._bases:
            return True
        # __tests__/<stem>.*
        return stem in self._test_dir_bases


# ==================== 路径/文本判定 ====================


def _is_test_path(path: str) -> bool:
    """判断路径是否是测试文件（契约 §1.7 的 test_change 口径）。"""
    parts = path.replace("\\", "/").split("/")
    name = parts[-1]
    if any(part in ("tests", "__tests__") for part in parts[:-1]):
        return True
    if name == "conftest.py":
        return True
    if name.startswith("test_") or name.endswith("_test.py"):
        return True
    base = name.rsplit(".", 1)[0] if "." in name else name
    return base.endswith(".test") or base.endswith(".spec")


_PROTECTION_PATTERN = re.compile(
    r"#\s*type:\s*ignore"
    r"|#\s*noqa"
    r"|#\s*pylint:\s*disable"
    r"|@ts-ignore"
    r"|@ts-nocheck"
    r"|eslint-disable"
    r"|pytest\.mark\.skip"
    r"|pytest\.mark\.xfail"
    r"|\.skip\("
    r"|--no-verify"
    r"|coverage:\s*ignore",
    re.IGNORECASE,
)

_ASSERTION_MARKERS = ("assert ", "expect(", "should.")


def _python_comment_lines(source: str) -> set[int] | None:
    """返回 Python 源码中「注释」所在行号集合；解析失败返回 None。"""
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        return {token.start[0] for token in tokens if token.type == tokenize.COMMENT}
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None


def _has_protection_bypass(
    source: str,
    source_lines: list[str],
    added_lines: set[int],
    language: str | None,
) -> bool:
    """判断新增行里是否出现审查规避标记。

    对 Python 只认真正的注释行：字符串字面量、文档、正则定义里出现这些字样不算绕过。
    解析失败时退回全文匹配。非 Python 语言直接按行匹配。
    """
    candidate_lines = [line for line in sorted(added_lines) if 1 <= line <= len(source_lines)]
    if not candidate_lines:
        return False
    if language == "python":
        comment_lines = _python_comment_lines(source)
        if comment_lines is not None:
            return any(
                line in comment_lines and _PROTECTION_PATTERN.search(source_lines[line - 1])
                for line in candidate_lines
            )
    return any(_PROTECTION_PATTERN.search(source_lines[line - 1]) for line in candidate_lines)


def _deleted_assertions_present(removed_lines: set[int], old_source: str | None) -> bool:
    """判断被删除的行里是否包含测试断言。"""
    if not old_source:
        return False
    old_lines = old_source.splitlines()
    for line_no in removed_lines:
        if 1 <= line_no <= len(old_lines):
            text = old_lines[line_no - 1]
            if any(marker in text for marker in _ASSERTION_MARKERS):
                return True
    return False


# ==================== 变更级 signal 判定 ====================

_DEP_FILE_RE = re.compile(
    r"(^|/)(requirements[\w.-]*\.txt|pyproject\.toml|package\.json|go\.mod"
    r"|Cargo\.toml|Gemfile|pom\.xml)$"
)
# 契约给定的依赖形态：`name[x] >=1.0` / `name==1.0`
_DEP_SPEC_RE = re.compile(r"^\s*[\"']?[\w.@/-]+(\[[^\]]*\])?\s*[<>=~!^]")
# TOML / JSON 形态：`"name": "^1.2.3"` 或 `name = ">=1.2"`
_DEP_QUOTED_RE = re.compile(r"[\w.@/-]+\s*[=:]\s*[\"']\s*[\^~<>=]*\d")
# go.mod 形态：`github.com/x/y v1.2.3`
_DEP_GO_RE = re.compile(r"^\s*[\w.@/-]+\s+v\d")
# `=` 后面必须像版本号，避免把 `readme = "README.md"` 误判为依赖
_DEP_EQ_TAIL_RE = re.compile(r"=\s*[\"']?\s*[\^~<>=]*\d")
# 行首键名：用于排除包元数据（version / name / requires-python 等）的误判
_DEP_KEY_RE = re.compile(r"^\s*[\"']?([\w.@/-]+)")
_DEP_KEY_VALUE_RE = re.compile(r"^\s*[\"']?[\w.@/-]+(\[[^\]]*\])?\s*=")
# 这些键是包元数据，不是依赖项（例如 `version = "0.3.0"`、`requires-python = ">=3.10"`）
_DEP_METADATA_KEYS = frozenset(
    {
        "version",
        "name",
        "description",
        "authors",
        "author",
        "maintainers",
        "maintainer",
        "license",
        "readme",
        "requires-python",
        "python",
        "edition",
        "keywords",
        "classifiers",
        "homepage",
        "repository",
        "documentation",
        "target",
        "include",
        "exclude",
        "module",
        "main",
        "private",
        "build-backend",
        "requires",
        "source",
        "status",
        "category",
    }
)

_DATABASE_PATH_RE = re.compile(r"(^|/)(migrations|alembic|schema)(/|$)|\.sql$", re.IGNORECASE)
_CI_PATH_RE = re.compile(
    r"(^|/)\.github/workflows/|(^|/)\.gitlab-ci\.ya?ml$|(^|/)Jenkinsfile$"
    r"|(^|/)Dockerfile[\w.-]*$|(^|/)docker-compose[\w.-]*\.ya?ml$"
)
_API_PATH_RE = re.compile(
    r"(^|/)openapi[\w.-]*\.(ya?ml|json)$|\.proto$|\.graphql$|\.gql$",
    re.IGNORECASE,
)
_CONFIG_PATH_RE = re.compile(
    r"(^|/)\.code-review\.|(^|/)pyproject\.toml$|(^|/)tsconfig\.json$"
    r"|(^|/)\.eslintrc[\w.-]*$|(^|/)ruff\.toml$"
)


def _looks_like_dependency(line: str) -> bool:
    """判断一行新增内容是否形如依赖声明。"""
    if not _DEP_SPEC_RE.match(line):
        return False
    key_match = _DEP_KEY_RE.match(line)
    if key_match and key_match.group(1).lower() in _DEP_METADATA_KEYS:
        return False
    # `name = "value"` 这种形态额外要求右侧像版本号，降低 TOML 元数据误报
    if _DEP_EQ_TAIL_RE.search(line) or _DEP_GO_RE.match(line) or _DEP_QUOTED_RE.search(line):
        return True
    # 仅出现 `=` 但不像版本号 → 视为普通配置
    return not _DEP_KEY_VALUE_RE.match(line)


def _signal(kind: str, path: str, detail: str, priority: str, line: int | None = None) -> dict:
    """构造一条变更级 signal。"""
    payload: dict = {"kind": kind, "path": path}
    if line is not None:
        payload["line"] = line
    payload["detail"] = detail
    payload["priority"] = priority
    return payload


_PATH_SIGNAL_RULES: tuple[tuple[re.Pattern, str, str, str], ...] = (
    (_DATABASE_PATH_RE, "database_schema", "数据库 schema / 迁移文件变更", "high"),
    (_CI_PATH_RE, "ci_or_build", "CI / 构建配置变更", "medium"),
    (_API_PATH_RE, "api_contract", "API 契约文件变更", "high"),
    (_CONFIG_PATH_RE, "config_change", "审查 / 构建工具配置变更", "low"),
)


def _dependency_signals(path: str, added_texts: dict[int, str]) -> list[dict]:
    """依赖清单文件新增的依赖项。"""
    if not _DEP_FILE_RE.search(path):
        return []
    signals: list[dict] = []
    for line_no in sorted(added_texts):
        text = added_texts[line_no]
        if not text.strip() or not _looks_like_dependency(text):
            continue
        signals.append(_signal("dependency_added", path, text.strip()[:120], "high", line_no))
        if len(signals) >= _MAX_DEP_SIGNALS_PER_FILE:
            break
    return signals


def _path_kind_signals(path: str) -> list[dict]:
    """按路径特征判定变更类型（schema / CI / API / 配置 / 测试）。"""
    signals = [
        _signal(kind, path, detail, priority)
        for pattern, kind, detail, priority in _PATH_SIGNAL_RULES
        if pattern.search(path)
    ]
    if _is_test_path(path):
        signals.append(_signal("test_change", path, "测试文件变更", "medium"))
    return signals


_SECRET_RULE_IDS = frozenset({"SEC005"})  # 硬编码密钥；只有它是「密钥新增」


def _security_signal(path: str, findings: list[dict]) -> list[dict]:
    """安全类 error 命中 → 变更级信号。

    只有密钥规则（SEC005）叫 `secret_added`；eval/pickle/shell 注入等归为
    `security_finding`，否则审查者会把「新增 eval」误读成「新增密钥」。
    """
    for finding in findings:
        if finding["category"] != "security" or finding["severity"] != "error":
            continue
        kind = "secret_added" if finding["rule"] in _SECRET_RULE_IDS else "security_finding"
        return [
            _signal(
                kind,
                path,
                f"{finding['rule']}：{finding['message'][:100]}",
                "high",
                finding["line"],
            )
        ]
    return []


def _change_signals_for_file(
    path: str,
    added_texts: dict[int, str],
    findings: list[dict],
) -> list[dict]:
    """按契约 §1.7 为一个文件产出变更级 signals。"""
    return (
        _dependency_signals(path, added_texts)
        + _path_kind_signals(path)
        + _security_signal(path, findings)
    )


def _signal_sort_key(signal: dict) -> tuple:
    """signal 排序：priority → path → line。"""
    return (
        _PRIORITY_RANK.get(signal["priority"], 9),
        signal["path"],
        signal.get("line", 0),
    )


# ==================== 单个文件的读取与分析 ====================


def _read_source(absolute_path: str) -> tuple[str | None, str]:
    """读取 UTF-8 文本源码，返回 (内容, 跳过原因)。"""
    try:
        size = os.path.getsize(absolute_path)
    except OSError:
        return None, "missing"
    if size > MAX_FILE_SIZE:
        return None, "too_large"

    try:
        with open(absolute_path, "rb") as handle:
            head = handle.read(8192)
    except OSError:
        return None, "read_error"
    if b"\x00" in head:
        return None, "binary"

    try:
        with open(absolute_path, encoding="utf-8") as handle:
            return handle.read(), ""
    except UnicodeDecodeError:
        return None, "decode_error"
    except OSError:
        return None, "read_error"


def _compact_finding(issue: dict) -> dict:
    """把引擎 issue 压缩成 packet 里的 finding 结构。"""
    finding = {
        "rule": issue.get("type", ""),
        "severity": issue.get("severity", "info"),
        "category": issue.get("category", ""),
        "line": issue.get("line", 0),
        "confidence": issue.get("confidence") or "high",
        "message": issue.get("message", ""),
    }
    suggestion = issue.get("suggestion")
    if suggestion:
        finding["suggestion"] = suggestion
    return finding


def _rule_display_name(rule_id: str) -> str:
    """取规则的短名（用于 focus 文案），未知规则返回空串。"""
    rule = registry.get(rule_id)
    return rule.NAME if rule is not None and rule.NAME else ""


def _show_old_source(repo_root: str, commit: str | None, path: str) -> str | None:
    """用 `git show <commit>:<path>` 取旧版本文件内容，失败返回 None。"""
    if not commit:
        return None
    normalized = path.replace("\\", "/")
    ok, out, _ = _git(["show", f"{commit}:{normalized}"], repo_root)
    return out if ok else None


def _read_version_source(
    repo_root: str,
    scope: str,
    head_commit: str | None,
    path: str,
) -> tuple[str | None, str]:
    """读取「被审查版本」的文件内容，而不是无条件读工作区。

    - working：审查对象就是工作区，直接读工作区文件；
    - staged：读 index blob（`git show :<path>`），失败再回退工作区；
    - base：读 HEAD blob（`git show <head_commit>:<path>`），失败再回退工作区。

    这样当工作区在 index/HEAD 之上还有未暂存改动时，分析内容与行号仍与
    被审查版本一致。返回 (内容, 跳过原因)；内容为 None 时原因说明为何跳过。
    """
    normalized = path.replace("\\", "/")
    spec: str | None = None
    if scope == "staged":
        spec = f":{normalized}"
    elif scope == "base" and head_commit:
        spec = f"{head_commit}:{normalized}"

    if spec is not None:
        ok, out, _ = _git(["show", spec], repo_root)
        if ok:
            if len(out) > MAX_FILE_SIZE:
                return None, "too_large"
            if "\x00" in out:
                return None, "binary"
            return out, ""

    return _read_source(os.path.join(repo_root, normalized))


# ==================== function deltas ====================


@dataclass
class _FunctionMetrics:
    """单个函数的复杂度/长度指标。"""

    line: int
    complexity: int
    length: int


def _collect_function_metrics(source: str) -> dict[str, _FunctionMetrics] | None:
    """收集源码中所有函数（含嵌套/方法）的指标；解析失败返回 None。"""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None

    metrics: dict[str, _FunctionMetrics] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{prefix}.{child.name}" if prefix else child.name
                end_line = child.end_lineno or child.lineno
                metrics[qualname] = _FunctionMetrics(
                    line=child.lineno,
                    complexity=compute_cyclomatic_complexity(child),
                    length=end_line - child.lineno + 1,
                )
                # 嵌套函数用 outer.<locals>.inner 形式拼接
                walk(child, f"{qualname}.<locals>")
            elif isinstance(child, ast.ClassDef):
                qualname = f"{prefix}.{child.name}" if prefix else child.name
                walk(child, qualname)
            else:
                walk(child, prefix)

    walk(tree, "")
    return metrics


def python_function_deltas(
    old_source: str,
    new_source: str,
    file_path: str = "",
) -> list[dict]:
    """比较两版 Python 源码中同名函数的复杂度/长度增长。

    只输出同时存在于两版、且复杂度增长或长度增长超过 1.5 倍的函数，
    按复杂度增长降序，最多 5 条；任一版本解析失败返回空列表。
    `file_path` 仅用于调用方语境，不参与计算。
    """
    old_metrics = _collect_function_metrics(old_source or "")
    new_metrics = _collect_function_metrics(new_source or "")
    if old_metrics is None or new_metrics is None:
        return []

    deltas: list[dict] = []
    for name, after in new_metrics.items():
        before = old_metrics.get(name)
        if before is None:
            continue
        complexity_grew = after.complexity > before.complexity
        length_grew = (
            after.length - before.length >= _LENGTH_GROWTH_MIN_DELTA
            and after.length > before.length * 1.5
        )
        if not (complexity_grew or length_grew):
            continue
        deltas.append(
            {
                "name": name,
                "line": after.line,
                "complexity_before": before.complexity,
                "complexity_after": after.complexity,
                "length_before": before.length,
                "length_after": after.length,
            }
        )

    deltas.sort(
        key=lambda item: (
            -(item["complexity_after"] - item["complexity_before"]),
            -(item["length_after"] - item["length_before"]),
            item["name"],
        )
    )
    return deltas[:_MAX_FUNCTION_DELTAS]


# ==================== review_change_set 内部步骤 ====================


def _merge_file_diffs(diffs: list[FileDiff]) -> list[FileDiff]:
    """按路径合并重复的 FileDiff（无 commit 时 cached 与 unstaged diff 可能重复）。"""
    merged: dict[str, FileDiff] = {}
    order: list[str] = []
    for diff in diffs:
        existing = merged.get(diff.path)
        if existing is None:
            merged[diff.path] = diff
            order.append(diff.path)
            continue
        existing.additions += diff.additions
        existing.deletions += diff.deletions
        existing.added_lines |= diff.added_lines
        existing.removed_lines |= diff.removed_lines
        existing.is_binary = existing.is_binary or diff.is_binary
        if diff.status == "deleted":
            existing.status = "deleted"
    return [merged[path] for path in order]


def _untracked_file_diffs(paths: list[str]) -> list[FileDiff]:
    """为未跟踪文件构造「全文件新增」的 FileDiff（行数在读取内容时补齐）。"""
    return [
        FileDiff(
            path=path,
            old_path=None,
            status="added",
            additions=0,
            deletions=0,
            added_lines=set(),
            removed_lines=set(),
        )
        for path in paths
    ]


def _file_priority(
    findings: list[dict],
    file_signals: list[str],
    function_deltas: list[dict],
    change_signals: list[dict],
) -> str:
    """按契约 §1.5 判定文件优先级。"""
    severities = {finding["severity"] for finding in findings}
    if "error" in severities:
        return "high"
    if any(signal["priority"] == "high" for signal in change_signals):
        return "high"
    if any(
        delta["complexity_after"] - delta["complexity_before"] >= _COMPLEXITY_HIGH_GROWTH
        for delta in function_deltas
    ):
        return "high"
    # 绕过检查标记、缺测试、超大单文件改动都值得进深审队列（但不必阻塞）
    medium_signals = {"tests_missing", "protection_bypass", "large_file_change"}
    if "warning" in severities or function_deltas or medium_signals & set(file_signals):
        return "medium"
    return "low"


def _delta_metric(delta: dict) -> str:
    """描述函数指标变化：优先报复杂度，只有长度增长时如实报长度。"""
    before = delta["complexity_before"]
    after = delta["complexity_after"]
    if after > before:
        return f"complexity {before}→{after}"
    return f"length {delta['length_before']}→{delta['length_after']}"


def _focus_errors(files: list[dict]) -> list[str]:
    """error 级 finding 一律进 focus。"""
    items: list[str] = []
    for file_entry in files:
        for finding in file_entry["findings"]:
            if finding["severity"] != "error":
                continue
            name = _rule_display_name(finding["rule"])
            label = f"{finding['rule']} {name}".strip()
            items.append(f"{file_entry['path']}:{finding['line']} — {label} (error)")
    return items


def _focus_high_signals(signals: list[dict]) -> list[str]:
    """high 优先级信号进 focus。"""
    items: list[str] = []
    for signal in signals:
        if signal["priority"] != "high":
            continue
        location = signal["path"]
        if signal.get("line"):
            location = f"{location}:{signal['line']}"
        items.append(f"{location} — {signal['kind']}: {signal['detail']}")
    return items


def _focus_deltas(files: list[dict]) -> list[str]:
    """复杂度真增长的排前面；只有长度增长的排后面（文案也区分为 length）。"""
    items: list[tuple[int, str]] = []
    for file_entry in files:
        for delta in file_entry["function_deltas"]:
            growth = delta["complexity_after"] - delta["complexity_before"]
            text = (
                f"{file_entry['path']}:{delta['line']} — {_delta_metric(delta)} in {delta['name']}"
            )
            items.append((growth, text))
    items.sort(key=lambda item: -item[0])
    return [text for _, text in items]


def _dedupe_focus(items: list[str]) -> list[str]:
    """去重并截断到 _FOCUS_MAX 条。"""
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
        if len(deduped) >= _FOCUS_MAX:
            break
    return deduped


def _build_focus(files: list[dict], signals: list[dict]) -> list[str]:
    """构造 focus 列表：error findings → high signals → 复杂度增长，最多 12 条。"""
    return _dedupe_focus(
        [
            *_focus_errors(files),
            *_focus_high_signals(signals),
            *_focus_deltas(files),
        ]
    )


def _empty_packet(target: ReviewTarget) -> dict:
    """无改动时的空 packet。"""
    return {
        "scope": target.scope,
        "range": target.range_spec,
        "base_ref": target.base_ref,
        "base_commit": target.base_commit,
        "head_commit": target.head_commit,
        "stats": {
            "files": 0,
            "additions": 0,
            "deletions": 0,
            "by_status": {},
            "languages": {},
        },
        "files": [],
        "signals": [],
        "focus": [],
        "limits": {"skipped": [], "truncated": False, "notes": []},
    }


# ==================== 主入口 ====================


@dataclass
class _ReviewAccumulator:
    """一次 review_change_set 内跨文件累积的预算与统计状态。"""

    finding_budget: int
    finding_count: int = 0
    budget_note_added: bool = False
    truncated: bool = False
    notes: list[str] = field(default_factory=list)
    stats_files: int = 0
    stats_additions: int = 0
    stats_deletions: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    languages: dict[str, int] = field(default_factory=dict)
    change_signals: list[dict] = field(default_factory=list)
    protection_files: int = 0
    protection_note_added: bool = False

    def record(self, diff: FileDiff, language: str | None) -> None:
        """累计 stats：文件数、增删行、状态分布、语言分布。"""
        self.stats_files += 1
        self.stats_additions += diff.additions
        self.stats_deletions += diff.deletions
        self.by_status[diff.status] = self.by_status.get(diff.status, 0) + 1
        if language:
            self.languages[language] = self.languages.get(language, 0) + 1

    @staticmethod
    def _candidate_key(
        path: str, added_lines: set[int], issue: dict
    ) -> tuple[str, int, str] | None:
        """判定 issue 是否落在新增行且值得考虑；返回去重键。

        键含 message：同一行同规则的两条不同命中（例如一行里既硬编码密码又硬编码
        token）都要保留，但规则重复注册造成的完全相同输出会被去重。
        """
        line_no = issue.get("line", 0)
        if line_no not in added_lines:
            return None
        # 测试代码天然长、分支多，复杂度规则在变更审查里只产生噪声
        if _is_test_path(path) and str(issue.get("type", "")).startswith("COMPLEX"):
            return None
        return (str(issue.get("type", "")), line_no, str(issue.get("message", "")))

    def _admit(self, path: str, per_file: int, per_file_note_added: bool) -> tuple[bool, bool]:
        """按预算判定该 finding 是否输出；返回 (是否输出, 每文件上限提示是否已记录)。"""
        if per_file >= _MAX_FINDINGS_PER_FILE:
            self.truncated = True
            if not per_file_note_added:
                self.notes.append(f"{path}：findings 超过每文件 {_MAX_FINDINGS_PER_FILE} 条上限")
                return False, True
            return False, per_file_note_added
        if self.finding_count < self.finding_budget:
            return True, per_file_note_added
        self.truncated = True
        if not self.budget_note_added:
            self.notes.append(
                f"findings 超过整体 {self.finding_budget} 条上限，"
                "其余命中不再输出、但仍参与 signals 与 priority 判定"
            )
            self.budget_note_added = True
        return False, per_file_note_added

    @staticmethod
    def _keep_hidden(hidden: list[dict], compact: dict) -> None:
        """未写入 packet 的命中：只留 error/warning 级供后续判定，数量有界。"""
        if compact["severity"] not in ("error", "warning"):
            return
        if len(hidden) < _MAX_FINDINGS_PER_FILE:
            hidden.append(compact)

    def pack_findings(
        self, path: str, added_lines: set[int], report: dict
    ) -> tuple[list[dict], list[dict]]:
        """把规则命中压成 packet 条目，并返回 (输出条目, 被预算隐藏的条目)。

        整体/每文件预算用满后仍然继续分析，只是不再把 finding 写进 packet；
        priority 与 secret_added 依赖命中的严重级/类别，不能因为输出有界而丢信号。
        """
        findings: list[dict] = []
        hidden: list[dict] = []
        seen: set[tuple[str, int]] = set()
        per_file = 0
        per_file_note_added = False
        for issue in report.get("issues", []):
            key = self._candidate_key(path, added_lines, issue)
            if key is None or key in seen:
                continue
            seen.add(key)
            compact = _compact_finding(issue)
            admitted, per_file_note_added = self._admit(path, per_file, per_file_note_added)
            if admitted:
                findings.append(compact)
                per_file += 1
                self.finding_count += 1
                continue
            self._keep_hidden(hidden, compact)
        findings.sort(key=lambda item: item["line"])
        return findings, hidden


@dataclass
class _ReviewSession:
    """一次审查的全局输入：被审目标、配置、预算状态与仓库索引。"""

    target: ReviewTarget
    config: Config
    state: _ReviewAccumulator
    test_index: _RepoFileIndex
    changed_test_files: list[str]


@dataclass
class _FileReview:
    """单个变更文件的分析输入。删除文件没有内容，source 为空。"""

    diff: FileDiff
    path: str
    language: str | None
    source: str
    source_lines: list[str]

    @property
    def added_lines(self) -> set[int]:
        return self.diff.added_lines


def _has_protection(session: _ReviewSession, item: _FileReview) -> bool:
    """是否需要标注 protection_bypass：只对生产源码，且不超过文件数上限。

    超过上限时记一条 note 并置 truncated，避免「第 4 个文件起静默消失」。
    """
    if not (item.language and not _is_test_path(item.path)):
        return False
    if not _has_protection_bypass(item.source, item.source_lines, item.added_lines, item.language):
        return False
    if session.state.protection_files >= _MAX_PROTECTION_FILES:
        if not session.state.protection_note_added:
            session.state.protection_note_added = True
            session.state.truncated = True
            session.state.notes.append(
                f"protection_bypass 最多标注 {_MAX_PROTECTION_FILES} 个文件，"
                "其余文件的绕过标记不再单独列出"
            )
        return False
    return True


def _needs_tests_signal(session: _ReviewSession, item: _FileReview) -> bool:
    """是否提示「改了代码但没配套测试」。本次变更已动测试时不逐文件判定，避免误报。"""
    return bool(
        item.language
        and not _is_test_path(item.path)
        and item.diff.additions >= _TESTS_MISSING_MIN_ADDITIONS
        and not session.test_index.has_test_for(item.path)
        and not session.changed_test_files
    )


def _deleted_assertions_signal(session: _ReviewSession, item: _FileReview) -> bool:
    """测试文件删除了断言（对比基线版本内容）。"""
    if not (_is_test_path(item.path) and item.diff.removed_lines):
        return False
    base_commit = session.target.base_commit
    if base_commit is None:
        return False
    old_path = item.diff.old_path or item.path
    old_test_source = _show_old_source(session.target.repo_root, base_commit, old_path)
    return _deleted_assertions_present(item.diff.removed_lines, old_test_source)


def _file_signals(session: _ReviewSession, item: _FileReview) -> tuple[list[str], bool]:
    """文件级 signals（契约 §1.6）。返回 (signals, 是否新增 protection_bypass 计数)。"""
    signals: list[str] = []
    # 绕过检查标记只对「生产源码」有意义：测试里 skip/type: ignore 是常规操作，
    # 文档/配置里提到这些字样也不算；Python 还需区分注释与字符串字面量。
    protected = _has_protection(session, item)
    if protected:
        signals.append("protection_bypass")
    if item.diff.additions > _LARGE_FILE_ADDITIONS:
        signals.append("large_file_change")
    if _needs_tests_signal(session, item):
        signals.append("tests_missing")
    if _deleted_assertions_signal(session, item):
        signals.append("deleted_test_assertions")
    return signals, protected


def _review_deleted_file(session: _ReviewSession, item: _FileReview) -> dict:
    """删除的文件不读取内容，只保留变更事实与风险信号。"""
    file_signals = ["deleted_test_assertions"] if _is_test_path(item.path) else []
    file_change_signals = _change_signals_for_file(item.path, {}, [])
    session.state.change_signals.extend(file_change_signals)
    # 「删测试 / 删迁移 / 删 API 契约」属于风险信号，不应一律埋在 low
    if any(signal["priority"] == "high" for signal in file_change_signals):
        priority = "high"
    elif file_signals or file_change_signals:
        priority = "medium"
    else:
        priority = "low"
    session.state.record(item.diff, item.language)
    return _file_entry(item, priority, file_signals, [], [])


def _normalize_added_lines(diff: FileDiff, source_lines: list[str], no_baseline: bool) -> None:
    """补齐「全文件新增」语义：无 commit 或未跟踪文件。"""
    if no_baseline:
        diff.status = "added"
        diff.added_lines = set(range(1, len(source_lines) + 1))
        diff.additions = len(source_lines)
        diff.deletions = 0
        diff.removed_lines = set()
    elif diff.status == "added" and not diff.added_lines:
        diff.added_lines = set(range(1, len(source_lines) + 1))
        diff.additions = len(source_lines)


def _function_deltas(session: _ReviewSession, item: _FileReview) -> list[dict]:
    """仅 python 的修改/重命名文件比较函数指标。"""
    base_commit = session.target.base_commit
    if item.language != "python" or not base_commit:
        return []
    if item.diff.status not in ("modified", "renamed"):
        return []
    old_path = item.diff.old_path or item.path
    old_source = _show_old_source(session.target.repo_root, base_commit, old_path)
    if old_source is None:
        return []
    return python_function_deltas(old_source, item.source, item.path)


def _file_entry(
    item: _FileReview,
    priority: str,
    file_signals: list[str],
    findings: list[dict],
    deltas: list[dict],
) -> dict:
    """组装 packet 的文件条目。"""
    return {
        "path": item.path,
        "old_path": item.diff.old_path,
        "status": item.diff.status,
        "additions": item.diff.additions,
        "deletions": item.diff.deletions,
        "language": item.language,
        "priority": priority,
        "signals": file_signals,
        "findings": findings,
        "function_deltas": deltas,
    }


def _added_line_texts(source_lines: list[str], added_lines: set[int]) -> dict[int, str]:
    """新增行号 → 行文本（越界行忽略）。"""
    return {
        line_no: source_lines[line_no - 1]
        for line_no in added_lines
        if 1 <= line_no <= len(source_lines)
    }


def _finding_entries(session: _ReviewSession, item: _FileReview) -> tuple[list[dict], list[dict]]:
    """规则命中 → packet findings（仅新增行），返回 (输出条目, 被预算隐藏的条目)。"""
    if not item.language:
        return [], []
    report = analyze_file_content(item.source, item.path, item.language, session.config)
    return session.state.pack_findings(item.path, item.added_lines, report)


def _review_changed_file(
    session: _ReviewSession, diff: FileDiff, path: str, language: str | None
) -> tuple[dict | None, str | None]:
    """分析单个非删除文件；读不到内容时返回 (None, 跳过原因)。"""
    # 内容来源必须与被审查版本一致（staged=index，base=HEAD），见 _read_version_source
    source, skip_reason = _read_version_source(
        session.target.repo_root, session.target.scope, session.target.head_commit, path
    )
    if source is None:
        return None, skip_reason

    source_lines = source.splitlines()
    _normalize_added_lines(diff, source_lines, no_baseline=session.target.base_commit is None)
    item = _FileReview(
        diff=diff,
        path=path,
        language=language,
        source=source,
        source_lines=source_lines,
    )

    findings, hidden = _finding_entries(session, item)
    deltas = _function_deltas(session, item)
    file_signals, protection_added = _file_signals(session, item)
    if protection_added:
        session.state.protection_files += 1

    # findings 可能因预算被压缩；hidden 仅用于信号与优先级判定
    priority_findings = findings + hidden
    file_change_signals = _change_signals_for_file(
        path, _added_line_texts(source_lines, diff.added_lines), priority_findings
    )
    session.state.change_signals.extend(file_change_signals)
    priority = _file_priority(priority_findings, file_signals, deltas, file_change_signals)
    session.state.record(diff, language)
    return _file_entry(item, priority, file_signals, findings, deltas), None


def _review_one_file(session: _ReviewSession, diff: FileDiff) -> tuple[dict | None, dict | None]:
    """按文件状态分派：忽略/二进制 → skipped，删除 → 只记事实，其余 → 分析。"""
    path = diff.path.replace("\\", "/")
    if not path:
        return None, None
    if is_path_ignored(Path(path), session.config):
        return None, {"path": path, "reason": "ignored"}
    if diff.is_binary:
        return None, {"path": path, "reason": "binary"}

    language = detect_language(path)
    if diff.status == "deleted":
        item = _FileReview(diff=diff, path=path, language=language, source="", source_lines=[])
        return _review_deleted_file(session, item), None
    entry, skip_reason = _review_changed_file(session, diff, path, language)
    if entry is None:
        return None, {"path": path, "reason": skip_reason or "unreadable"}
    return entry, None


def _packet_stats(state: _ReviewAccumulator) -> dict:
    """packet 的 stats 字段。"""
    return {
        "files": state.stats_files,
        "additions": state.stats_additions,
        "deletions": state.stats_deletions,
        "by_status": {
            status: state.by_status[status] for status in _STATUS_ORDER if status in state.by_status
        },
        "languages": dict(sorted(state.languages.items())),
    }


def _sorted_signals(state: _ReviewAccumulator) -> list[dict]:
    """排序并截断变更级 signals。"""
    signals = state.change_signals
    signals.sort(key=_signal_sort_key)
    if len(signals) > _MAX_SIGNALS:
        state.truncated = True
        state.notes.append(f"变更级 signals {len(signals)} 条，超过上限 {_MAX_SIGNALS}，已截断")
        return signals[:_MAX_SIGNALS]
    return signals


def _finalize_packet(
    target: ReviewTarget,
    state: _ReviewAccumulator,
    reviews: list[dict],
    skipped: list[dict],
    file_budget: int,
) -> dict:
    """收尾：整体信号、排序、截断与 packet 组装。"""
    if not reviews and not state.change_signals and not skipped:
        return _empty_packet(target)

    if state.stats_additions > _LARGE_CHANGE_ADDITIONS or state.stats_files > _LARGE_CHANGE_FILES:
        state.change_signals.append(
            _signal(
                "large_change",
                ".",
                f"总计 {state.stats_additions} 行新增、{state.stats_files} 个文件变更",
                "medium",
            )
        )

    # 文件排序：priority → additions 降序 → path
    reviews.sort(
        key=lambda entry: (
            _PRIORITY_RANK.get(entry["priority"], 9),
            -entry["additions"],
            entry["path"],
        )
    )
    if len(reviews) > file_budget:
        state.truncated = True
        state.notes.append(f"变更文件 {len(reviews)} 个，超过上限 {file_budget}，已省略其余文件")
        reviews = reviews[:file_budget]

    signals = _sorted_signals(state)
    return {
        "scope": target.scope,
        "range": target.range_spec,
        "base_ref": target.base_ref,
        "base_commit": target.base_commit,
        "head_commit": target.head_commit,
        "stats": _packet_stats(state),
        "files": reviews,
        "signals": signals,
        "focus": _build_focus(reviews, signals),
        "limits": {"skipped": skipped, "truncated": state.truncated, "notes": state.notes},
    }


def _under_prefix(path: str, prefix: str) -> bool:
    """路径是否位于给定仓库相对目录下（含目录本身）。"""
    normalized = path.replace("\\", "/")
    return normalized == prefix or normalized.startswith(prefix + "/")


# 公开入口保留 6 个参数（scope / base / config / repo_root / 两个预算），都是调用方要用的
# 显式旋钮；这里用本工具自己的行级豁免标注 COMPLEX004，而不是为了指标把预算藏进隐式全局。
def review_change_set(  # codereview: ignore=COMPLEX004
    scope: str = "working",
    base: str | None = None,
    config: Config | None = None,
    repo_root: str | None = None,
    max_files: int = 40,
    max_findings: int = 80,
) -> dict:
    """审查当前变更并产出证据 packet（契约 §1.3）。

    返回结构固定为 scope/range/base_commit/head_commit/stats/files/signals/
    focus/limits 一组字段；采集失败时返回 `{"error", "hint"}`。
    """
    target = collect_review_target(scope=scope, base=base, repo_root=repo_root)
    if isinstance(target, dict):
        return target

    cfg = config or load_config(Path(target.repo_root))
    state = _ReviewAccumulator(finding_budget=max(0, int(max_findings)))
    diffs = _load_target_diffs(target)
    if target.scope_prefix:
        # directory 指向子目录时只审该子目录下的变更，不静默扩大到整仓
        state.notes.append(f"审查范围限定在子目录 {target.scope_prefix}/")
    session = _ReviewSession(
        target=target,
        config=cfg,
        state=state,
        test_index=_RepoFileIndex(_list_repo_files(target.repo_root) + target.untracked),
        changed_test_files=_reviewed_test_paths(diffs, cfg),
    )

    reviews, skipped = _run_file_reviews(session, diffs)
    return _finalize_packet(target, state, reviews, skipped, max(0, int(max_files)))


def _load_target_diffs(target: ReviewTarget) -> list[FileDiff]:
    """解析 diff 与未跟踪文件，并按子目录前缀限定范围。"""
    diffs = _merge_file_diffs(
        list(parse_unified_diff(target.diff_text)) + _untracked_file_diffs(target.untracked)
    )
    if not target.scope_prefix:
        return diffs
    return [diff for diff in diffs if _under_prefix(diff.path, target.scope_prefix)]


def _reviewed_test_paths(diffs: list[FileDiff], cfg: Config) -> list[str]:
    """真正会被审查的测试文件（被 ignore 或 binary 排除的不算「本次动过测试」）。"""
    paths = []
    for diff in diffs:
        path = diff.path.replace("\\", "/")
        if _is_test_path(path) and not diff.is_binary and not is_path_ignored(Path(path), cfg):
            paths.append(path)
    return paths


def _run_file_reviews(
    session: _ReviewSession, diffs: list[FileDiff]
) -> tuple[list[dict], list[dict]]:
    """逐文件审查，返回 (文件条目, 跳过说明)。"""
    reviews: list[dict] = []
    skipped: list[dict] = []
    for diff in diffs:
        entry, skip_entry = _review_one_file(session, diff)
        if skip_entry is not None:
            skipped.append(skip_entry)
        if entry is not None:
            reviews.append(entry)
    return reviews, skipped
