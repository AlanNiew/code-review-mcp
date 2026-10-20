"""analyzers 子包：调用规则引擎执行分析。

对外暴露：
- analyze_file_content：分析单文件内容（核心，被其他分析器复用）
- analyze_diff：分析 git diff（v0.2 旧实现，review_diff 兼容用）
- analyze_project_directory：扫描整个项目目录
- build_context：构造 RuleContext 工具函数
- review_change_set：变更审查证据包（v0.3.0 旗舰）
- collect_review_target / parse_unified_diff / python_function_deltas：变更分析组件
"""

from .change import (
    collect_review_target,
    parse_unified_diff,
    python_function_deltas,
    review_change_set,
)
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
    "review_change_set",
    "collect_review_target",
    "parse_unified_diff",
    "python_function_deltas",
]
