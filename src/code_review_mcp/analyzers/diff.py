"""Diff 分析器：审查 git 变更。

设计要点：
- 调用 git 命令获取 staged/unstaged diff
- 对每个新增行做"AI 时代 PR review"风格的检测
- 复用规则引擎中的核心检测（密钥、调试代码、TODO、行长）
- v0.2.1 修复：
  * 正确解析 hunk 行号（不再始终为 0）
  * 应用行级豁免（# codereview: ignore）
  * 调试 print 检测改为 AST 精确匹配，避免字符串字面量误报
  * 拆分 _check_diff_line 为 5 个职责单一的检测函数
"""

from __future__ import annotations

import ast
import re
import subprocess
from dataclasses import dataclass

from ..config import Config, is_issue_suppressed, load_config
from ..models import Category, Issue, Severity
from ..scoring import score_multi_dimensional
from ..utils import detect_language, detect_todo_tag

# ==================== Git 命令 ====================


def get_git_diffs() -> tuple[str, str] | dict:
    """获取 staged 和 unstaged 的 git diff 输出，失败时返回错误 dict。

    注意：显式指定 UTF-8 编码 + errors="replace"，避免在 Windows 中文 locale
    下默认用 GBK 解码 git 输出导致 UnicodeDecodeError。
    """
    try:
        staged = subprocess.run(
            ["git", "diff", "--cached"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        unstaged = subprocess.run(
            ["git", "diff"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except FileNotFoundError:
        return {"error": "未找到 git 命令，请确保 git 已安装"}
    except subprocess.TimeoutExpired:
        return {"error": "git diff 命令执行超时"}

    return staged.stdout or "", unstaged.stdout or ""


def parse_changed_files(diff: str) -> list[dict]:
    """从 diff 输出中提取变更文件列表及其语言类型。"""
    files_changed = re.findall(r"^diff --git a/(.+?) b/(.+?)$", diff, re.MULTILINE)
    file_names = list({f[1] for f in files_changed})
    return [{"file": fname, "language": detect_language(fname)} for fname in file_names]


# ==================== Diff 解析器：维护当前行号 ====================


_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass
class _LineContext:
    """单次 + 行的解析上下文。"""

    added_content: str  # 去掉前导 + 后的内容
    stripped: str  # strip 后的内容
    line_no: int  # 新文件里的实际行号（1-indexed）
    language: str  # 当前文件的语言
    file_path: str | None  # 当前文件路径
    source_lines: list[
        str
    ]  # 当前 hunk 累积的"新文件"内容（0-indexed，与 is_issue_suppressed 约定一致）


def _iter_added_lines(diff: str):
    r"""迭代 diff 中的所有 + 行，维护正确的行号和上下文。

    行号算法：
    - @@ -a,b +c,d @@ 表示新文件从第 c 行开始
    - 每个 ` `（空格开头）或 `+`（加号开头）行推进新文件行号
    - `-`（减号开头）行不推进
    - `\` 行（如 "\ No newline at end of file"）不推进

    source_lines 约定（与 config.is_issue_suppressed 一致）：
    - 0-indexed：source_lines[i] 对应源文件第 i+1 行
    """
    current_language = "unknown"
    current_file: str | None = None
    current_line_no = 0
    # 累积新文件的内容，用于行级豁免匹配（0-indexed）
    source_lines: list[str] = []

    for line in diff.split("\n"):
        # 文件切换
        m = re.match(r"^diff --git a/(.+?) b/(.+?)$", line)
        if m:
            current_file = m.group(2)
            current_language = detect_language(current_file) or "unknown"
            current_line_no = 0
            source_lines = []
            continue

        # hunk 头：@@ -1,3 +5,7 @@ → 新文件从第 5 行开始
        hunk_m = _HUNK_HEADER_RE.match(line)
        if hunk_m:
            new_start = int(hunk_m.group(1))
            # 行号跳变，填充中间空行（让 source_lines 索引对齐到 new_start - 1）
            while len(source_lines) < new_start - 1:
                source_lines.append("")
            current_line_no = new_start
            continue

        # 新增行：+xxx
        if line.startswith("+") and not line.startswith("+++"):
            added = line[1:]
            idx = current_line_no - 1  # 0-indexed
            # 扩容 source_lines 到 idx
            while len(source_lines) <= idx:
                source_lines.append("")
            source_lines[idx] = added
            yield _LineContext(
                added_content=added,
                stripped=added.strip(),
                line_no=current_line_no,
                language=current_language,
                file_path=current_file,
                source_lines=source_lines,
            )
            current_line_no += 1
            continue

        # 上下文行（空格开头）：推进行号但不报 issue
        if line.startswith(" "):
            idx = current_line_no - 1
            while len(source_lines) <= idx:
                source_lines.append("")
            source_lines[idx] = line[1:]
            current_line_no += 1
            continue

        # 删除行（-xxx）、diff 元信息（+++ / ---）、No newline 等：不推进


# ==================== 单行检测函数（职责单一）====================


def _check_diff_todo(ctx: _LineContext, issues: list[Issue]) -> None:
    """检测新增的 TODO/FIXME/HACK/XXX 标记（仅在注释行中）。"""
    if not (
        ctx.stripped.startswith("#")
        or ctx.stripped.startswith("//")
        or ctx.stripped.startswith("/*")
    ):
        return
    tag = detect_todo_tag(ctx.stripped)
    if tag:
        issues.append(
            Issue(
                type="DIFF001",
                message=f"新增 {tag} 标记",
                line=ctx.line_no,
                severity=Severity.INFO,
                category=Category.STYLE,
                tag=tag,
                content=ctx.stripped,
                file=ctx.file_path,
                suggestion="如果是临时占位，创建 issue 跟踪",
            )
        )


def _check_diff_debug(ctx: _LineContext, issues: list[Issue]) -> None:
    """检测遗留的调试语句（Python print / JS console.log）。

    v0.2.1：Python 用 AST 精确匹配 Call(func=Name(id='print'))，
    避免对字符串字面量（如 'print(' 这种）误报。
    """
    if not ctx.stripped:
        return

    if ctx.language == "python":
        # 尝试解析为表达式，检查是否是 print() 调用
        if _is_python_print_call(ctx.added_content):
            issues.append(
                Issue(
                    type="DIFF002",
                    message="可能遗留的调试 print 语句",
                    line=ctx.line_no,
                    severity=Severity.WARNING,
                    category=Category.DEBUG_CODE,
                    content=ctx.stripped,
                    file=ctx.file_path,
                    suggestion="使用 logging 模块替代",
                )
            )
    elif ctx.language in ("javascript", "typescript"):  # noqa: SIM102
        # JS/TS：用简单文本匹配（没有 AST），但排除注释行
        if not ctx.stripped.startswith(("//", "/*", "*")):  # noqa: SIM102
            # 用更严格的匹配：console.log( 后跟非空白
            if re.search(r"\bconsole\.log\s*\(", ctx.stripped):
                issues.append(
                    Issue(
                        type="DIFF002",
                        message="可能遗留的调试 console.log 语句",
                        line=ctx.line_no,
                        severity=Severity.WARNING,
                        category=Category.DEBUG_CODE,
                        content=ctx.stripped,
                        file=ctx.file_path,
                        suggestion="移除调试代码或使用专业 logger",
                    )
                )


def _is_python_print_call(text: str) -> bool:
    """判断一行 Python 代码是否包含 print() 调用（用 AST 精确匹配）。

    优势：
    - 不会匹配字符串字面量 'print(' （例如 msg = "print this"）
    - 不会匹配属性调用 obj.print()
    """
    try:
        # 单行可能是表达式、赋值或语句，用 try 各种解析
        # 简单 trick：包一层 if needed
        tree = ast.parse(text, mode="exec")
    except SyntaxError:
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            # 直接的 print(...) 调用
            if isinstance(func, ast.Name) and func.id == "print":
                return True
    return False


def _check_diff_secret(ctx: _LineContext, issues: list[Issue]) -> None:
    """启发式：检测包含敏感关键字且非环境变量引用的行。"""
    stripped = ctx.stripped
    if not stripped:
        return

    lower = stripped.lower()
    has_kw = any(kw in lower for kw in ("password", "secret", "api_key", "apikey", "token"))
    uses_env = (
        "env" in lower or "os.getenv" in stripped or "process.env" in stripped or "${" in stripped
    )
    if not (has_kw and not uses_env):  # noqa: SIM102
        return
    # 进一步要求：行里有等号或冒号或字符串字面量
    if "=" in stripped or ":" in stripped or '"' in stripped or "'" in stripped:
        issues.append(
            Issue(
                type="DIFF004",
                message="可能包含硬编码的密钥或密码",
                line=ctx.line_no,
                severity=Severity.ERROR,
                category=Category.SECURITY,
                content=stripped,
                file=ctx.file_path,
                suggestion="从环境变量或密钥管理服务读取",
            )
        )


def _check_diff_line_length(ctx: _LineContext, issues: list[Issue]) -> None:
    """检测新增行是否过长。"""
    if len(ctx.stripped) > 200:
        issues.append(
            Issue(
                type="DIFF003",
                message=f"新增行过长 ({len(ctx.stripped)} 字符)",
                line=ctx.line_no,
                severity=Severity.WARNING,
                category=Category.STYLE,
                file=ctx.file_path,
                content=ctx.stripped[:80] + "...",
                suggestion="拆分为多行",
            )
        )


# 所有检测函数的注册表（顺序即执行顺序）
_DIFF_CHECKS = (
    _check_diff_todo,
    _check_diff_debug,
    _check_diff_secret,
    _check_diff_line_length,
)


def _check_diff_issues(diff: str) -> list[Issue]:
    """扫描 diff 中的新增行，应用所有检测 + 行级豁免。"""
    issues: list[Issue] = []

    for ctx in _iter_added_lines(diff):
        # 跳过空行
        if not ctx.stripped:
            continue

        # 跑所有检测函数
        raw_issues: list[Issue] = []
        for check_fn in _DIFF_CHECKS:
            check_fn(ctx, raw_issues)

        # 应用行级豁免（# codereview: ignore）
        for issue in raw_issues:
            if is_issue_suppressed(ctx.source_lines, issue.line, issue.type):
                continue
            issues.append(issue)

    return issues


# ==================== 单 diff 报告构建 ====================


def _build_diff_review(diff: str) -> dict:
    """对单份 diff 输出构建审查结果（文件列表 + 问题列表 + 质量评分）。"""
    if not diff:
        return {
            "files": [],
            "issues": [],
            "quality": score_multi_dimensional([]).to_dict(),
        }

    file_reviews = parse_changed_files(diff)
    issues = _check_diff_issues(diff)
    multi = score_multi_dimensional(issues)

    return {
        "files": file_reviews,
        "issues": [i.to_dict() for i in issues],
        "quality": multi.to_dict(),
    }


def analyze_diff(config: Config | None = None) -> dict:
    """分析 git diff（staged + unstaged），返回完整报告。"""
    config = config or load_config()

    diffs = get_git_diffs()
    if isinstance(diffs, dict):
        return diffs  # 错误信息

    staged_output, unstaged_output = diffs

    if not staged_output and not unstaged_output:
        return {
            "message": "没有未提交的变更",
            "staged_changes": [],
            "unstaged_changes": [],
        }

    staged_review = _build_diff_review(staged_output)
    unstaged_review = _build_diff_review(unstaged_output)

    # 汇总所有问题，计算整体质量
    all_issues_dicts = staged_review.get("issues", []) + unstaged_review.get("issues", [])
    # 简单聚合统计（不重新构造 Issue，直接用 dict 计数）
    total_errors = sum(1 for i in all_issues_dicts if i.get("severity") == "error")
    total_warnings = sum(1 for i in all_issues_dicts if i.get("severity") == "warning")
    total_infos = sum(1 for i in all_issues_dicts if i.get("severity") == "info")

    return {
        "staged_changes": staged_review,
        "unstaged_changes": unstaged_review,
        "overall_quality": {
            "errors": total_errors,
            "warnings": total_warnings,
            "infos": total_infos,
            "total": len(all_issues_dicts),
        },
        "summary": (f"共发现 {total_errors} 个错误、{total_warnings} 个警告、{total_infos} 个提示"),
    }
