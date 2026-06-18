"""通用工具函数：语言检测、行数统计、注释行识别等。

从原 server.py 抽取的共享逻辑，多个 analyzer 都会用到。
"""

from __future__ import annotations

from pathlib import Path

# 文件扩展名 -> 语言映射（与原项目保持一致，便于向后兼容）
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

# 支持的源代码文件扩展名集合
SUPPORTED_EXTS = set(EXT_LANGUAGE_MAP.keys())

# TODO 类标记列表
TODO_TAGS = ("TODO", "FIXME", "HACK", "XXX")

# 函数长度/复杂度阈值（默认值；可被 Config 覆盖）
MAX_FUNCTION_LENGTH = 50
MAX_BRANCH_COMPLEXITY = 10
MAX_PARAM_COUNT = 5
MAX_LINE_LENGTH = 200

# 文件大小上限（5MB）
MAX_FILE_SIZE = 5 * 1024 * 1024

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
    ".ruff_cache",
    ".idea",
    ".vscode",
}


def detect_language(file_path: str) -> str | None:
    """根据文件扩展名检测编程语言。"""
    return EXT_LANGUAGE_MAP.get(Path(file_path).suffix.lower())


def detect_todo_tag(text: str) -> str | None:
    """检测文本中是否包含 TODO 类标记，返回第一个匹配的标签名或 None。"""
    return next((tag for tag in TODO_TAGS if tag in text), None)


def count_lines(content: str) -> dict:
    """统计代码行数，区分代码行、空白行和注释行。"""
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
    """统计注释行数，支持 Python docstring 和多种语言的单行注释。"""
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
        if stripped.startswith("#") or stripped.startswith("//") or stripped.startswith("/*"):
            count += 1

    return count


def is_ignored_line(line: str) -> bool:
    """检查代码行是否包含行级豁免标记（# codereview: ignore）。

    支持两种形式：
    - `# codereview: ignore`            整行豁免所有规则
    - `# codereview: ignore=SEC001,SEC005`  豁免指定规则
    """
    return "codereview: ignore" in line.lower()


def get_ignored_rule_ids(line: str) -> list[str] | None:
    """从代码行解析被豁免的规则 ID 列表。

    返回 None 表示豁免所有规则；返回空列表表示无效（不豁免）；
    返回非空列表表示只豁免这些规则 ID。
    """
    lower = line.lower()
    marker = "codereview: ignore"
    idx = lower.find(marker)
    if idx < 0:
        return None

    rest = line[idx + len(marker) :]
    # 形如 `=SEC001,SEC005`
    if rest.strip().startswith("="):
        ids_part = rest.strip()[1:]
        ids = [s.strip().upper() for s in ids_part.split(",") if s.strip()]
        return ids
    # 形如 `codereview: ignore`（无 =）→ 豁免所有
    return None
