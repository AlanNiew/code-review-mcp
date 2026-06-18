"""评分算法：把 issue 列表转换为多维度的质量画像。

借鉴：
- DeepSource / CodeClimate 的字母评级（A-F）+ 技术债比率
- SonarSource 的分类（Bug / Vulnerability / Code Smell / Hotspot）
- open-code-review 的多维加权（faithfulness / freshness / coherence / quality）

本模块实现 3 个评分维度：
1. 总分（overall）：综合质量，A-F 等级
2. 安全分（security）：仅看 security 类问题，反映安全风险
3. 可维护性分（maintainability）：仅看 complexity / duplication / style 类问题

每个维度独立 0-100 + A-F 字母评级，便于在报告里区分"安全没问题但代码烂"
和"代码漂亮但有 SQL 注入"两种场景。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .models import Category, Issue, Severity

# ==================== 严重级别权重 ====================

# 单项扣分权重（参考 open-code-review 的 critical/high/medium/low/info）
SEVERITY_PENALTY: dict[str, float] = {
    Severity.ERROR.value: 10.0,
    Severity.WARNING.value: 3.0,
    Severity.INFO.value: 0.5,
}

# 维度 → 关心的类别
_SECURITY_CATEGORIES = {Category.SECURITY.value}
_MAINTAINABILITY_CATEGORIES = {
    Category.COMPLEXITY.value,
    Category.STYLE.value,
    Category.DEBUG_CODE.value,
    Category.DUPLICATION.value,
}


@dataclass
class Score:
    """单个维度的评分结果。"""

    score: float  # 0-100
    grade: str  # A/B/C/D/F
    issues_count: int  # 该维度内的问题总数
    errors: int  # error 级别问题数
    warnings: int  # warning 级别问题数
    infos: int  # info 级别问题数
    summary: str  # 一句话总结

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "grade": self.grade,
            "issues_count": self.issues_count,
            "errors": self.errors,
            "warnings": self.warnings,
            "infos": self.infos,
            "summary": self.summary,
        }


def grade_from_score(score: float) -> str:
    """分数 → 字母等级（DeepSource 风格）。

    A: 90-100   优秀
    B: 80-89    良好
    C: 70-79    合格
    D: 60-69    较差
    F: < 60     不合格
    """
    if score >= 90:
        return "A"
    elif score >= 80:
        return "B"
    elif score >= 70:
        return "C"
    elif score >= 60:
        return "D"
    else:
        return "F"


def _summarize(errors: int, warnings: int, infos: int) -> str:
    parts = []
    if errors > 0:
        parts.append(f"{errors} 个错误")
    if warnings > 0:
        parts.append(f"{warnings} 个警告")
    if infos > 0:
        parts.append(f"{infos} 个提示")
    if not parts:
        parts.append("未发现问题")
    return "，".join(parts)


def score_issues(issues: Iterable[Issue]) -> Score:
    """对一组 issue 计算单维度评分。"""
    issues_list = list(issues)
    if not issues_list:
        return Score(
            score=100.0,
            grade="A",
            issues_count=0,
            errors=0,
            warnings=0,
            infos=0,
            summary="未发现问题",
        )

    errors = sum(1 for i in issues_list if i.severity == Severity.ERROR)
    warnings = sum(1 for i in issues_list if i.severity == Severity.WARNING)
    infos = sum(1 for i in issues_list if i.severity == Severity.INFO)

    penalty = (
        errors * SEVERITY_PENALTY[Severity.ERROR.value]
        + warnings * SEVERITY_PENALTY[Severity.WARNING.value]
        + infos * SEVERITY_PENALTY[Severity.INFO.value]
    )
    raw_score = max(0.0, 100.0 - penalty)
    score = round(raw_score, 1)

    return Score(
        score=score,
        grade=grade_from_score(score),
        issues_count=len(issues_list),
        errors=errors,
        warnings=warnings,
        infos=infos,
        summary=_summarize(errors, warnings, infos),
    )


# ==================== 多维综合评分 ====================


@dataclass
class MultiScore:
    """多维度评分结果。"""

    overall: Score  # 综合（所有 issue）
    security: Score  # 仅安全
    maintainability: Score  # 仅可维护性（复杂度/风格/重复/调试）

    def to_dict(self) -> dict:
        return {
            "overall": self.overall.to_dict(),
            "security": self.security.to_dict(),
            "maintainability": self.maintainability.to_dict(),
        }


def score_multi_dimensional(issues: Iterable[Issue]) -> MultiScore:
    """对一组 issue 计算多维评分。

    - overall：所有 issue 都算
    - security：只算 Category.SECURITY
    - maintainability：算 complexity / style / debug_code / duplication
    """
    issues_list = list(issues)

    security_issues = [
        i
        for i in issues_list
        if (i.category.value if hasattr(i.category, "value") else str(i.category))
        in _SECURITY_CATEGORIES
    ]
    maintainability_issues = [
        i
        for i in issues_list
        if (i.category.value if hasattr(i.category, "value") else str(i.category))
        in _MAINTAINABILITY_CATEGORIES
    ]

    return MultiScore(
        overall=score_issues(issues_list),
        security=score_issues(security_issues),
        maintainability=score_issues(maintainability_issues),
    )


# ==================== 向后兼容：单维 quality score ====================


def compute_quality_score(issues: list[Issue], total_lines: int) -> dict:
    """与旧版本兼容的 quality score 输出。

    返回 dict 形式与 v0.1.0 一致（score/grade/summary/errors/warnings/infos），
    但内部使用新的扣分权重。total_lines 参数保留以兼容旧调用方。
    """
    score = score_issues(issues)
    result = score.to_dict()
    # 旧字段名（向后兼容）
    result["score"] = score.score
    return result
