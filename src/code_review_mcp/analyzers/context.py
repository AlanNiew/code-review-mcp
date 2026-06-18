"""分析器共用入口：构造 RuleContext、运行规则、聚合结果。"""

from __future__ import annotations

import ast
from pathlib import Path

from ..config import Config, load_config
from ..models import Category, Issue, Severity
from ..rules import registry
from ..rules.base import RuleContext
from ..scoring import score_multi_dimensional
from ..utils import count_lines, detect_language


def build_context(
    source: str,
    file_path: str,
    language: str | None = None,
) -> RuleContext:
    """构造规则上下文：解析源码、识别语言、构建 Python AST（如适用）。"""
    if language is None:
        language = detect_language(file_path) or "unknown"

    # Python 才尝试解析 AST
    tree: ast.AST | None = None
    if language == "python":
        try:
            tree = ast.parse(source)
        except SyntaxError:
            tree = None

    return RuleContext(
        source=source,
        source_lines=source.splitlines() or [source],
        tree=tree,
        language=language,
        file_path=file_path,
    )


def analyze_file_content(
    source: str,
    file_path: str,
    language: str | None = None,
    config: Config | None = None,
) -> dict:
    """分析单文件内容，返回完整报告 dict。

    返回结构：
    {
        "file": str,
        "language": str | None,
        "lines": {total, code, blank, comment},
        "issues": [Issue.to_dict(), ...],
        "quality": MultiScore.to_dict(),
    }
    """
    config = config or load_config(Path(file_path).parent if file_path else None)
    if language is None:
        language = detect_language(file_path) or "unknown"

    line_stats = count_lines(source)
    ctx = build_context(source, file_path, language)

    # 若 AST 解析失败且是 Python，补一个语法错误 issue
    issues: list[Issue] = []
    if language == "python" and ctx.tree is None:
        try:
            ast.parse(source)
        except SyntaxError as e:
            issues.append(
                Issue(
                    type="SYNTAX001",
                    message=f"语法错误: {e.msg}",
                    line=getattr(e, "lineno", 0) or 0,
                    severity=Severity.ERROR,
                    category=Category.SYNTAX,
                    suggestion="修复语法错误后才能进行其他分析",
                )
            )

    # 跑规则引擎
    issues.extend(registry.run_all(ctx, config))

    # 多维评分
    multi_score = score_multi_dimensional(issues)

    return {
        "file": file_path,
        "language": language,
        "lines": line_stats,
        "issues": [i.to_dict() for i in issues],
        "quality": multi_score.to_dict(),
    }
