"""analyzers 子包：调用规则引擎执行分析。

对外暴露 4 个分析器：
- analyze_file_content：分析单文件内容（核心，被其他 3 个复用）
- analyze_diff：分析 git diff
- analyze_project：扫描整个项目目录
- build_context：构造 RuleContext 工具函数
"""

from .context import analyze_file_content, build_context
from .diff import analyze_diff, get_git_diffs, parse_changed_files
from .project import analyze_project_directory

__all__ = [
    "build_context",
    "analyze_file_content",
    "analyze_diff",
    "get_git_diffs",
    "parse_changed_files",
    "analyze_project_directory",
]
