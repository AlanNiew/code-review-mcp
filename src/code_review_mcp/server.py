"""
代码审查 MCP 服务器

提供以下工具：
- analyze_file: 分析单个文件的代码复杂度和质量问题
- review_diff: 审查 git diff 中的变更
- check_project: 扫描项目整体代码质量

架构说明：
- 使用 dataclass 定义 Issue 结构，替代松散的 dict
- 所有分析函数按单一职责拆分，每个不超过 50 行
- 共享逻辑（如 TODO 标记检测）提取为可复用的辅助函数
"""

import ast
import os
import subprocess
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("code-review-mcp")

# ==================== 常量定义 ====================

# 文件扩展名 -> 语言映射
EXT_LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
}

# 支持的源代码文件扩展名
SUPPORTED_EXTS = set(EXT_LANGUAGE_MAP.keys())

# TODO 类标记列表
TODO_TAGS = ("TODO", "FIXME", "HACK", "XXX")

# 项目扫描时排除的目录
EXCLUDE_DIRS = {
    "node_modules",
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".next",
    ".nuxt",
    "target",
    "vendor",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "coverage",
}

# 函数长度/复杂度阈值
MAX_FUNCTION_LENGTH = 50
MAX_BRANCH_COMPLEXITY = 10
MAX_PARAM_COUNT = 5
MAX_LINE_LENGTH = 200

# 文件大小上限（5MB）
MAX_FILE_SIZE = 5 * 1024 * 1024


# ==================== 数据结构 ====================


@dataclass
class Issue:
    """代码问题，用于在分析结果中统一表示各种类型的缺陷"""

    type: str
    message: str
    line: int
    severity: str  # error / warning / info
    name: Optional[str] = None
    length: Optional[int] = None
    complexity: Optional[int] = None
    param_count: Optional[int] = None
    tag: Optional[str] = None
    file: Optional[str] = None
    content: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


# ==================== 通用辅助函数 ====================


def _detect_language(file_path: str) -> str | None:
    """根据文件扩展名检测编程语言"""
    return EXT_LANGUAGE_MAP.get(Path(file_path).suffix.lower())


def _detect_todo_tag(text: str) -> str | None:
    """检测文本中是否包含 TODO 类标记，返回第一个匹配的标签名或 None"""
    return next((tag for tag in TODO_TAGS if tag in text), None)


def _count_lines(content: str) -> dict:
    """统计代码行数，区分代码行、空白行和注释行"""
    lines = content.split("\n")
    total = len(lines)
    blank = sum(1 for line in lines if not line.strip())
    comment = _count_comment_lines(lines)

    return {
        "total": total,
        "code": total - blank - comment,
        "blank": blank,
        "comment": comment,
    }


def _count_comment_lines(lines: list[str]) -> int:
    """统计注释行数，支持 Python docstring 和多种语言的单行注释"""
    count = 0
    in_docstring = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # 检测 docstring 边界（三引号出现奇数次时切换状态）
        if '"""' in stripped or "'''" in stripped:
            triple_count = stripped.count('"""') + stripped.count("'''")
            if triple_count == 1:
                in_docstring = not in_docstring
            count += 1
            continue

        if in_docstring:
            count += 1
            continue

        # 单行注释：Python #、JS/TS/Java/C //、C 块注释 /*
        if (
            stripped.startswith("#")
            or stripped.startswith("//")
            or stripped.startswith("/*")
        ):
            count += 1

    return count


def _compute_quality_score(issues: list[Issue], total_lines: int) -> dict:
    """根据问题列表计算综合质量评分（0-100）和等级（A/B/C/D）"""
    if total_lines == 0:
        return {
            "score": 100,
            "grade": "A",
            "summary": "空文件",
            "errors": 0,
            "warnings": 0,
            "infos": 0,
        }

    errors = sum(1 for i in issues if i.severity == "error")
    warnings = sum(1 for i in issues if i.severity == "warning")
    infos = sum(1 for i in issues if i.severity == "info")

    # 每种严重程度对应不同的扣分权重
    penalty = errors * 10 + warnings * 3 + infos * 0.5
    score = max(0, min(100, 100 - penalty))

    if score >= 90:
        grade = "A"
    elif score >= 75:
        grade = "B"
    elif score >= 60:
        grade = "C"
    else:
        grade = "D"

    parts = []
    if errors > 0:
        parts.append(f"{errors} 个错误")
    if warnings > 0:
        parts.append(f"{warnings} 个警告")
    if infos > 0:
        parts.append(f"{infos} 个提示")
    if not parts:
        parts.append("未发现问题")

    return {
        "score": round(score, 1),
        "grade": grade,
        "summary": "，".join(parts),
        "errors": errors,
        "warnings": warnings,
        "infos": infos,
    }


# ==================== Python 复杂度分析 ====================


def _parse_python_ast(content: str) -> ast.AST | list[Issue]:
    """解析 Python AST，失败时返回包含语法错误的 Issue 列表"""
    try:
        return ast.parse(content)
    except SyntaxError as e:
        return [
            Issue(
                type="syntax_error",
                message=f"语法错误: {e}",
                line=getattr(e, "lineno", 0),
                severity="error",
            )
        ]


def _check_function_length(
    node: ast.FunctionDef | ast.AsyncFunctionDef, issues: list[Issue]
) -> None:
    """检查函数长度是否超过阈值"""
    end_line = node.end_lineno or node.lineno
    func_length = end_line - node.lineno + 1

    if func_length > MAX_FUNCTION_LENGTH:
        issues.append(
            Issue(
                type="function_too_long",
                message=f"函数 `{node.name}` 过长 ({func_length} 行)，建议不超过 {MAX_FUNCTION_LENGTH} 行",
                line=node.lineno,
                severity="warning",
                name=node.name,
                length=func_length,
            )
        )


def _check_branch_complexity(
    node: ast.FunctionDef | ast.AsyncFunctionDef, issues: list[Issue]
) -> None:
    """检查函数内分支复杂度是否过高"""
    branch_count = 0
    for child in ast.walk(node):
        if isinstance(child, (ast.If, ast.For, ast.While, ast.ExceptHandler, ast.With)):
            branch_count += 1
        elif isinstance(child, ast.BoolOp) and isinstance(child.op, ast.And):
            branch_count += len(child.values) - 1

    if branch_count > MAX_BRANCH_COMPLEXITY:
        issues.append(
            Issue(
                type="high_complexity",
                message=f"函数 `{node.name}` 分支复杂度过高 ({branch_count})，建议简化逻辑",
                line=node.lineno,
                severity="warning",
                name=node.name,
                complexity=branch_count,
            )
        )


def _check_param_count(
    node: ast.FunctionDef | ast.AsyncFunctionDef, issues: list[Issue]
) -> None:
    """检查函数参数数量是否过多"""
    args = [a.arg for a in node.args.args]
    if len(args) > MAX_PARAM_COUNT:
        issues.append(
            Issue(
                type="too_many_params",
                message=f"函数 `{node.name}` 参数过多 ({len(args)} 个)，建议使用数据类或配置对象",
                line=node.lineno,
                severity="info",
                name=node.name,
                param_count=len(args),
            )
        )


def _analyze_python_complexity(content: str) -> list[Issue]:
    """分析 Python 代码的函数级复杂度（长度、分支、参数数量）"""
    tree = _parse_python_ast(content)
    if isinstance(tree, list):
        return tree  # 语法错误

    issues: list[Issue] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _check_function_length(node, issues)
            _check_branch_complexity(node, issues)
            _check_param_count(node, issues)

    return issues


# ==================== 通用质量检查 ====================


def _check_line_length(line: str, line_num: int, issues: list[Issue]) -> None:
    """检查单行长度是否超过阈值"""
    stripped = line.strip()
    if len(stripped) > MAX_LINE_LENGTH:
        issues.append(
            Issue(
                type="line_too_long",
                message=f"第 {line_num} 行过长 ({len(stripped)} 字符)，建议不超过 {MAX_LINE_LENGTH} 字符",
                line=line_num,
                severity="warning",
            )
        )


def _check_todo_in_line(line: str, line_num: int, issues: list[Issue]) -> None:
    """检查注释行中是否包含 TODO 类标记（仅检测以注释开头的行，避免误报）"""
    stripped = line.strip()
    # 只在注释行中检测，避免匹配字符串字面量
    if not (
        stripped.startswith("#")
        or stripped.startswith("//")
        or stripped.startswith("/*")
    ):
        return
    tag = _detect_todo_tag(stripped)
    if tag:
        issues.append(
            Issue(
                type="has_todo",
                message=f"第 {line_num} 行包含 {tag} 标记",
                line=line_num,
                severity="info",
                tag=tag,
            )
        )


def _check_trailing_whitespace(
    line: str, line_num: int, language: str, issues: list[Issue]
) -> None:
    """检查行末是否有多余的空白字符"""
    if language not in ("python", "javascript", "typescript"):
        return
    if line != line.rstrip():
        issues.append(
            Issue(
                type="trailing_whitespace",
                message=f"第 {line_num} 行末尾有多余空白字符",
                line=line_num,
                severity="info",
            )
        )


def _analyze_generic_quality(content: str, language: str) -> list[Issue]:
    """对任意语言文件执行通用质量检查（行长度、TODO标记、末尾空白）"""
    issues: list[Issue] = []

    for i, line in enumerate(content.split("\n"), 1):
        _check_line_length(line, i, issues)
        _check_todo_in_line(line, i, issues)
        _check_trailing_whitespace(line, i, language, issues)

    return issues


# ==================== Git Diff 分析 ====================


def _get_git_diffs() -> tuple[str, str] | dict:
    """获取 staged 和 unstaged 的 git diff 输出，失败时返回错误 dict"""
    try:
        staged = subprocess.run(
            ["git", "diff", "--cached"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        unstaged = subprocess.run(
            ["git", "diff"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        return {"error": "未找到 git 命令，请确保 git 已安装"}
    except subprocess.TimeoutExpired:
        return {"error": "git diff 命令执行超时"}

    return staged.stdout, unstaged.stdout


def _parse_changed_files(diff: str) -> list[dict]:
    """从 diff 输出中提取变更文件列表及其语言类型"""
    files_changed = re.findall(r"^diff --git a/(.+?) b/(.+?)$", diff, re.MULTILINE)
    file_names = list(set(f[1] for f in files_changed))
    return [
        {"file": fname, "language": _detect_language(fname)} for fname in file_names
    ]


def _check_diff_issues(diff: str) -> list[Issue]:
    """扫描 diff 中的新增行，检测调试代码、硬编码密钥、TODO 标记和长行"""
    issues: list[Issue] = []
    language = "unknown"

    # 从 diff 头部提取文件语言
    files = re.findall(r"^diff --git a/(.+?) b/(.+?)$", diff, re.MULTILINE)
    if files:
        language = _detect_language(files[-1][1]) or "unknown"

    for match in re.finditer(r"^\+\s*(.+)$", diff, re.MULTILINE):
        added_line = match.group(1)
        # 跳过 diff 元数据行
        if added_line.startswith(("+++", "---")):
            continue

        # 从最近的 hunk 头部获取行号
        hunk_match = re.search(
            r"^@@ -\d+(?:,\d+)? \+(\d+)", diff[: match.start()], re.MULTILINE
        )
        line_num = int(hunk_match.group(1)) if hunk_match else 0

        _check_diff_todo(added_line, line_num, issues)
        _check_diff_debug(added_line, line_num, language, issues)
        _check_diff_secrets(added_line, line_num, issues)
        _check_diff_line_length(added_line, line_num, issues)

    return issues


def _check_diff_todo(added_line: str, line_num: int, issues: list[Issue]) -> None:
    """检测新增行中的 TODO 类标记"""
    tag = _detect_todo_tag(added_line)
    if tag:
        issues.append(
            Issue(
                type="new_todo",
                message=f"新增 {tag} 标记",
                line=line_num,
                severity="info",
                content=added_line.strip(),
                tag=tag,
            )
        )


def _check_diff_debug(
    added_line: str, line_num: int, language: str, issues: list[Issue]
) -> None:
    """检测遗留的调试语句（print / console.log）"""
    if "print(" in added_line and language == "python":
        issues.append(
            Issue(
                type="debug_print",
                message="可能遗留的调试 print 语句",
                line=line_num,
                severity="warning",
                content=added_line.strip(),
            )
        )
    elif "console.log" in added_line and language in ("javascript", "typescript"):
        issues.append(
            Issue(
                type="debug_log",
                message="可能遗留的调试 console.log 语句",
                line=line_num,
                severity="warning",
                content=added_line.strip(),
            )
        )


def _check_diff_secrets(added_line: str, line_num: int, issues: list[Issue]) -> None:
    """检测可能硬编码的密钥或密码（排除环境变量引用）"""
    lower = added_line.lower()
    has_secret_keyword = "password" in lower or "secret" in lower or "api_key" in lower
    uses_env = (
        "env" in lower or "os.getenv" in added_line or "process.env" in added_line
    )

    if has_secret_keyword and not uses_env:
        issues.append(
            Issue(
                type="potential_secret",
                message="可能包含硬编码的密钥或密码",
                line=line_num,
                severity="error",
                content=added_line.strip(),
            )
        )


def _check_diff_line_length(
    added_line: str, line_num: int, issues: list[Issue]
) -> None:
    """检测新增行是否过长"""
    stripped = added_line.strip()
    if stripped and len(stripped) > MAX_LINE_LENGTH:
        issues.append(
            Issue(
                type="long_line",
                message=f"新增行过长 ({len(stripped)} 字符)",
                line=line_num,
                severity="warning",
            )
        )


def _build_diff_review(diff: str) -> dict:
    """对单份 diff 输出构建审查结果（文件列表 + 问题列表 + 质量评分）"""
    if not diff:
        return {"files": [], "issues": [], "quality": _compute_quality_score([], 0)}

    file_reviews = _parse_changed_files(diff)
    issues = _check_diff_issues(diff)
    quality = _compute_quality_score(issues, len(diff.split("\n")))

    return {
        "files": file_reviews,
        "issues": [i.to_dict() for i in issues],
        "quality": quality,
    }


# ==================== 项目扫描 ====================


def _scan_single_file(fpath: Path, dir_path: Path) -> dict | None:
    """扫描单个文件，返回统计信息，跳过不支持的文件或读取失败的情况"""
    rel_path = fpath.relative_to(dir_path)

    if fpath.stat().st_size > MAX_FILE_SIZE:
        return {"file": str(rel_path), "status": "skipped", "reason": "文件过大"}

    try:
        content = fpath.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None

    language = _detect_language(str(fpath))
    line_info = _count_lines(content)

    # 收集问题
    issues: list[Issue] = []
    if language == "python":
        try:
            issues = _analyze_python_complexity(content)
        except Exception:
            issues = []
    issues.extend(_analyze_generic_quality(content, language or "unknown"))

    return {
        "file": str(rel_path),
        "language": language,
        "lines": line_info["total"],
        "code_lines": line_info["code"],
        "issues_count": len(issues),
    }


def _aggregate_project_stats(file_stats: list[dict]) -> dict:
    """汇总所有文件统计，计算总行数、总问题数、语言分布"""
    total_files = 0
    total_lines = 0
    total_issues = 0
    language_stats: dict[str, dict] = {}

    for stat in file_stats:
        if stat.get("status") == "skipped":
            continue

        total_files += 1
        total_lines += stat.get("lines", 0)
        total_issues += stat.get("issues_count", 0)

        lang = stat.get("language")
        if lang:
            if lang not in language_stats:
                language_stats[lang] = {"files": 0, "lines": 0}
            language_stats[lang]["files"] += 1
            language_stats[lang]["lines"] += stat.get("lines", 0)

    return {
        "total_files": total_files,
        "total_lines": total_lines,
        "total_issues": total_issues,
        "language_stats": language_stats,
    }


# ==================== MCP 工具入口 ====================


@mcp.tool()
def analyze_file(file_path: str) -> dict:
    """分析单个文件的代码质量和复杂度。

    Args:
        file_path: 要分析的文件路径（相对于项目根目录或绝对路径）
    """
    path = Path(file_path)
    if not path.exists():
        return {"error": f"文件不存在: {file_path}"}
    if path.is_dir():
        return {"error": f"路径是目录，不是文件: {file_path}"}
    if path.stat().st_size > MAX_FILE_SIZE:
        return {"error": "文件过大（超过 5MB），请分析较小的文件"}

    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"error": "无法以 UTF-8 编码读取文件"}

    language = _detect_language(str(path))
    line_stats = _count_lines(content)

    # 收集问题
    issues: list[Issue] = []
    if language == "python":
        issues.extend(_analyze_python_complexity(content))
    issues.extend(_analyze_generic_quality(content, language or "unknown"))

    quality = _compute_quality_score(issues, line_stats["total"])

    return {
        "file": str(path),
        "language": language,
        "lines": line_stats,
        "issues": [i.to_dict() for i in issues],
        "quality": quality,
    }


@mcp.tool()
def review_diff() -> dict:
    """审查当前 git 仓库中未提交的变更（staged + unstaged）。

    分析 diff 中的每一处变更，检查潜在问题和代码风格。
    """
    diffs = _get_git_diffs()
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
    all_issues = [
        Issue(**{k: v for k, v in i.items() if k != "file"})
        for review in (staged_review, unstaged_review)
        for i in review.get("issues", [])
    ]
    overall = _compute_quality_score(all_issues, 100)

    return {
        "staged_changes": staged_review,
        "unstaged_changes": unstaged_review,
        "overall_quality": overall,
        "summary": f"共发现 {overall['errors']} 个错误、{overall['warnings']} 个警告、{overall['infos']} 个提示",
    }


@mcp.tool()
def check_project(directory: str = ".") -> dict:
    """扫描项目目录的代码质量概况。

    Args:
        directory: 要扫描的项目目录路径，默认为当前目录
    """
    dir_path = Path(directory)
    if not dir_path.exists():
        return {"error": f"目录不存在: {directory}"}

    # 遍历项目文件
    file_stats: list[dict] = []
    for root, dirs, files in os.walk(dir_path):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for fname in files:
            if Path(fname).suffix.lower() not in SUPPORTED_EXTS:
                continue
            result = _scan_single_file(Path(root) / fname, dir_path)
            if result is not None:
                file_stats.append(result)

    # 按问题数降序排列
    file_stats.sort(key=lambda x: x.get("issues_count", 0), reverse=True)

    # 汇总统计
    stats = _aggregate_project_stats(file_stats)
    overall = _compute_quality_score(
        [Issue(type="", message="", line=0, severity="warning")]
        * stats["total_issues"],
        stats["total_lines"],
    )

    return {
        "directory": str(dir_path.resolve()),
        "summary": {
            "total_files": stats["total_files"],
            "total_lines": stats["total_lines"],
            "total_issues": stats["total_issues"],
        },
        "languages": stats["language_stats"],
        "top_issues_files": file_stats[:10],
        "overall_quality": overall,
    }


def main():
    """CLI 入口点，供 pip install 后直接运行 code-review-mcp 命令"""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
