"""项目扫描：遍历目录，对每个文件做轻量分析，汇总成项目概况。

设计目标：
- 单进程顺序扫描（避免并发引入的复杂性）
- 大文件/读不动/不支持的格式直接跳过
- 汇总时只关心"有多少问题、什么语言分布、哪些文件最烂"

v0.2.1 修复：项目级评分策略改为"加权平均 + 问题密度惩罚"。
旧版用"硬扣分"（每个 warning -3 分）导致大项目天然得 F；
新版先取所有文件分数的加权平均（按代码行数），再按"问题密度"（issues/1000 行）
做温和惩罚，让评分反映"项目平均水平"而非"扣分上限"。
"""

from __future__ import annotations

import os
from pathlib import Path

from ..config import Config, is_path_ignored, load_config
from ..scoring import Score, grade_from_score
from ..utils import (
    EXCLUDE_DIRS,
    MAX_FILE_SIZE,
    SUPPORTED_EXTS,
    count_lines,
    detect_language,
)
from .context import analyze_file_content


def _scan_single_file(fpath: Path, dir_path: Path, config: Config) -> dict | None:
    """扫描单个文件，返回统计信息。

    返回 None 表示跳过（不支持的扩展名、读取失败等）。
    返回 dict 表示扫描完成（包含 issues_count 即使为 0）。
    """
    rel_path = fpath.relative_to(dir_path)

    # ignore_paths 过滤
    if is_path_ignored(rel_path, config):
        return None

    if fpath.stat().st_size > MAX_FILE_SIZE:
        return {"file": str(rel_path), "status": "skipped", "reason": "文件过大"}

    try:
        content = fpath.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
    except OSError:
        return None

    language = detect_language(str(fpath))
    line_info = count_lines(content)

    result = analyze_file_content(content, str(fpath), language, config)
    issues_dicts = result.get("issues", [])

    return {
        "file": str(rel_path),
        "language": language,
        "lines": line_info["total"],
        "code_lines": line_info["code"],
        "issues_count": len(issues_dicts),
        "errors": sum(1 for i in issues_dicts if i.get("severity") == "error"),
        "warnings": sum(1 for i in issues_dicts if i.get("severity") == "warning"),
        "infos": sum(1 for i in issues_dicts if i.get("severity") == "info"),
        "quality": result.get("quality"),
    }


def _aggregate_project_stats(file_stats: list[dict]) -> dict:
    """汇总所有文件统计，计算总行数、总问题数、语言分布。"""
    total_files = 0
    total_lines = 0
    total_issues = 0
    total_errors = 0
    total_warnings = 0
    total_infos = 0
    language_stats: dict[str, dict] = {}

    for stat in file_stats:
        if stat.get("status") == "skipped":
            continue

        total_files += 1
        total_lines += stat.get("lines", 0)
        total_issues += stat.get("issues_count", 0)
        total_errors += stat.get("errors", 0)
        total_warnings += stat.get("warnings", 0)
        total_infos += stat.get("infos", 0)

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
        "total_errors": total_errors,
        "total_warnings": total_warnings,
        "total_infos": total_infos,
        "language_stats": language_stats,
    }


# ==================== 项目级评分（v0.2.1 新策略）====================


# 问题密度阈值：每 1000 行代码允许多少个问题，超过则惩罚
# 借鉴 SonarQube / DeepSource 的密度思路
_ISSUES_PER_KLOC_SOFT = 20  # 20 个/千行以下：温和惩罚
_ISSUES_PER_KLOC_HARD = 100  # 100 个/千行以上：严厉惩罚


def _weighted_average_score(file_stats: list[dict], dimension: str) -> float | None:
    """计算所有文件在指定维度的加权平均分（按代码行数加权）。

    返回 None 表示无有效文件（无法计算）。
    """
    numerator = 0.0
    denominator = 0.0

    for stat in file_stats:
        if stat.get("status") == "skipped":
            continue
        quality = stat.get("quality") or {}
        dim_score = quality.get(dimension) or {}
        score_val = dim_score.get("score")
        if score_val is None:
            continue
        weight = max(1, stat.get("code_lines", 1))  # 至少 1，避免除以 0
        numerator += float(score_val) * weight
        denominator += weight

    if denominator == 0:
        return None
    return numerator / denominator


def _density_penalty(total_issues: int, total_lines: int) -> float:
    """根据问题密度（issues / 1000 lines）计算惩罚分。

    - 密度 ≤ 20/千行：不惩罚（项目整体很干净）
    - 密度 20-100：温和惩罚，每增加 10 个/千行扣 1 分
    - 密度 > 100：严厉惩罚，但最多扣 30 分（避免无限扣到 0）
    """
    if total_lines == 0:
        return 0.0

    density = total_issues * 1000.0 / total_lines

    if density <= _ISSUES_PER_KLOC_SOFT:
        return 0.0
    if density <= _ISSUES_PER_KLOC_HARD:
        # 20-100 之间，温和惩罚
        return (density - _ISSUES_PER_KLOC_SOFT) / 10.0
    # > 100，严厉但封顶
    return (_ISSUES_PER_KLOC_HARD - _ISSUES_PER_KLOC_SOFT) / 10.0 + min(
        30.0, (density - _ISSUES_PER_KLOC_HARD) / 5.0
    )


def _project_score(file_stats: list[dict], stats: dict, dimension: str) -> Score:
    """计算项目级某个维度的评分（加权平均 + 密度惩罚）。"""
    avg = _weighted_average_score(file_stats, dimension)
    if avg is None:
        # 没有有效文件，给满分（空项目视为无问题）
        return Score(
            score=100.0,
            grade="A",
            issues_count=0,
            errors=0,
            warnings=0,
            infos=0,
            summary="无文件可评分",
        )

    penalty = _density_penalty(stats["total_issues"], stats["total_lines"])
    final_score = max(0.0, min(100.0, avg - penalty))
    grade = grade_from_score(final_score)

    # 错误数 / 警告数 / 提示数仍按总数报告（便于决策）
    if dimension == "overall":
        errors = stats["total_errors"]
        warnings = stats["total_warnings"]
        infos = stats["total_infos"]
    else:
        # 各维度的细分计数暂时复用总数（更精确的实现需要 per-file 反查）
        errors = stats["total_errors"] if dimension == "security" else 0
        warnings = stats["total_warnings"]
        infos = stats["total_infos"]

    parts = []
    if errors > 0:
        parts.append(f"{errors} 个错误")
    if warnings > 0:
        parts.append(f"{warnings} 个警告")
    if infos > 0:
        parts.append(f"{infos} 个提示")
    if not parts:
        parts.append("未发现问题")

    return Score(
        score=round(final_score, 1),
        grade=grade,
        issues_count=stats["total_issues"],
        errors=errors,
        warnings=warnings,
        infos=infos,
        summary="，".join(parts),
    )


# ==================== 主入口 ====================


def analyze_project_directory(
    directory: str = ".",
    config: Config | None = None,
) -> dict:
    """扫描项目目录的代码质量概况。"""
    dir_path = Path(directory).resolve()
    if not dir_path.exists():
        return {"error": f"目录不存在: {directory}"}

    config = config or load_config(dir_path)

    file_stats: list[dict] = []
    for root, dirs, files in os.walk(dir_path):
        # 排除约定俗成的依赖/构建目录
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for fname in files:
            if Path(fname).suffix.lower() not in SUPPORTED_EXTS:
                continue
            result = _scan_single_file(Path(root) / fname, dir_path, config)
            if result is not None:
                file_stats.append(result)

    # 按问题数降序
    file_stats.sort(key=lambda x: x.get("issues_count", 0), reverse=True)

    stats = _aggregate_project_stats(file_stats)

    # v0.2.1 新评分策略：加权平均 + 密度惩罚，三维度独立
    overall = _project_score(file_stats, stats, "overall")
    security = _project_score(file_stats, stats, "security")
    maintainability = _project_score(file_stats, stats, "maintainability")

    return {
        "directory": str(dir_path),
        "summary": {
            "total_files": stats["total_files"],
            "total_lines": stats["total_lines"],
            "total_issues": stats["total_issues"],
            "errors": stats["total_errors"],
            "warnings": stats["total_warnings"],
            "infos": stats["total_infos"],
        },
        "languages": stats["language_stats"],
        "top_issues_files": file_stats[:10],
        "overall_quality": overall.to_dict(),
        "quality": {  # v0.2.1 多维（overall_quality 字段保留向后兼容）
            "overall": overall.to_dict(),
            "security": security.to_dict(),
            "maintainability": maintainability.to_dict(),
        },
    }
