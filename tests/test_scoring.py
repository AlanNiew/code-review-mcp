"""评分算法测试。"""

from code_review_mcp.models import Category, Issue, Severity
from code_review_mcp.scoring import (
    Score,
    grade_from_score,
    score_issues,
    score_multi_dimensional,
)


def _make_issue(severity=Severity.INFO, category=Category.STYLE, type_="TEST001"):
    return Issue(
        type=type_,
        message="test issue",
        line=1,
        severity=severity,
        category=category,
    )


# ==================== 单维评分 ====================


def test_no_issues_is_perfect():
    score = score_issues([])
    assert score.score == 100.0
    assert score.grade == "A"
    assert score.issues_count == 0


def test_single_error():
    score = score_issues([_make_issue(severity=Severity.ERROR)])
    assert score.score == 90.0  # 100 - 10
    assert score.grade == "A"
    assert score.errors == 1


def test_many_errors_caps_at_zero():
    """扣分不会让分数变成负数。"""
    issues = [_make_issue(severity=Severity.ERROR) for _ in range(20)]
    score = score_issues(issues)
    assert score.score == 0.0
    assert score.grade == "F"


def test_warning_penalty():
    """每个 warning 扣 3 分。"""
    score = score_issues([_make_issue(severity=Severity.WARNING) for _ in range(3)])
    assert score.score == 91.0  # 100 - 9


def test_info_penalty():
    """每个 info 扣 0.5 分。"""
    score = score_issues([_make_issue(severity=Severity.INFO) for _ in range(4)])
    assert score.score == 98.0  # 100 - 2


def test_mixed_severity():
    issues = [
        _make_issue(severity=Severity.ERROR),
        _make_issue(severity=Severity.WARNING),
        _make_issue(severity=Severity.INFO),
    ]
    score = score_issues(issues)
    # 100 - 10 - 3 - 0.5 = 86.5
    assert score.score == 86.5
    assert score.errors == 1
    assert score.warnings == 1
    assert score.infos == 1


# ==================== 等级映射 ====================


def test_grade_thresholds():
    assert grade_from_score(100) == "A"
    assert grade_from_score(90) == "A"
    assert grade_from_score(89.9) == "B"
    assert grade_from_score(80) == "B"
    assert grade_from_score(79.9) == "C"
    assert grade_from_score(70) == "C"
    assert grade_from_score(69.9) == "D"
    assert grade_from_score(60) == "D"
    assert grade_from_score(59.9) == "F"
    assert grade_from_score(0) == "F"


# ==================== 多维评分 ====================


def test_multi_dim_empty():
    multi = score_multi_dimensional([])
    assert multi.overall.score == 100.0
    assert multi.security.score == 100.0
    assert multi.maintainability.score == 100.0


def test_multi_dim_separation():
    """安全问题和可维护性问题应该分开计分。"""
    issues = [
        _make_issue(severity=Severity.ERROR, category=Category.SECURITY, type_="SEC001"),
        _make_issue(severity=Severity.WARNING, category=Category.COMPLEXITY, type_="COMPLEX001"),
    ]
    multi = score_multi_dimensional(issues)

    # overall 包含两个：100 - 10 - 3 = 87
    assert multi.overall.score == 87.0

    # security 只看 security：100 - 10 = 90
    assert multi.security.score == 90.0
    assert multi.security.errors == 1

    # maintainability 只看 complexity：100 - 3 = 97
    assert multi.maintainability.score == 97.0
    assert multi.maintainability.warnings == 1


def test_multi_dim_to_dict():
    multi = score_multi_dimensional([_make_issue()])
    d = multi.to_dict()
    assert "overall" in d
    assert "security" in d
    assert "maintainability" in d
    for key in ("overall", "security", "maintainability"):
        assert "score" in d[key]
        assert "grade" in d[key]
        assert "summary" in d[key]


# ==================== Score dataclass ====================


def test_score_to_dict():
    score = Score(score=85.0, grade="B", issues_count=3, errors=1, warnings=1, infos=1, summary="x")
    d = score.to_dict()
    assert d["score"] == 85.0
    assert d["grade"] == "B"
    assert d["issues_count"] == 3
    assert "summary" in d
