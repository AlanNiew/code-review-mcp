"""数据模型：统一的 Issue 结构、严重级别与分类枚举。

设计参考：
- bandit 的 severity/confidence 双维度
- SonarSource 的 4 大类别（Bug / Vulnerability / Code Smell / Security Hotspot）
- PR-Agent 的强类型 finding schema
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum


class Severity(str, Enum):
    """严重级别（与 CodeRabbit/PR-Agent 业界惯例对齐）。

    用 str 作为基类，便于 JSON 序列化时直接显示字符串值。
    """

    ERROR = "error"  # 必须修复：安全漏洞、潜在 bug、崩溃风险
    WARNING = "warning"  # 建议修复：复杂度过高、坏味道、过时 API
    INFO = "info"  # 可选优化：TODO 标记、调试残留、风格


class Category(str, Enum):
    """问题分类，用于评分维度路由与统计聚合。

    参考 SonarSource 的 Bug/Vulnerability/Code Smell 分类，
    并新增 ai_hallucination / debug_code / stale_api 等 AI 时代维度。
    """

    SECURITY = "security"  # 安全漏洞 / 风险热点
    COMPLEXITY = "complexity"  # 复杂度 / 规模
    STYLE = "style"  # 代码风格 / 规范
    DEBUG_CODE = "debug_code"  # 调试残留
    AI_HALLUCINATION = "ai_hallucination"  # AI 虚构（包/API）
    STALE_API = "stale_api"  # 过时 API
    DUPLICATION = "duplication"  # 重复代码
    ERROR_HANDLING = "error_handling"  # 错误处理问题
    SYNTAX = "syntax"  # 语法错误


class Confidence(str, Enum):
    """置信度（借鉴 bandit），用于在报告里标记"命中规则但可能误报"。"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class Issue:
    """代码问题：所有规则和分析器的统一输出结构。

    必填字段（type/message/line/severity）保证报告最小信息量；
    其他字段按需填，to_dict() 会自动忽略 None 值。
    """

    # 必填
    type: str  # 规则 ID（如 SEC001 / COMPLEX001），便于豁免和聚合
    message: str  # 人类可读的描述
    line: int  # 1-indexed 行号；0 表示整文件
    severity: Severity  # 严重级别

    # 分类与元信息
    category: Category = Category.STYLE  # 问题类别（用于评分维度路由）
    confidence: Confidence = Confidence.HIGH  # 命中置信度

    # 可选的上下文信息（按规则需要填）
    name: str | None = None  # 函数/变量名等
    length: int | None = None  # 长度（行数/字符数）
    complexity: int | None = None  # 复杂度值（圈/认知）
    complexity_kind: str | None = None  # "cyclomatic" 或 "cognitive"
    param_count: int | None = None  # 参数个数
    tag: str | None = None  # TODO/FIXME/HACK 等标记
    file: str | None = None  # 文件路径
    content: str | None = None  # 问题行内容（截断后）
    suggestion: str | None = None  # 修复建议（一句话）

    def to_dict(self) -> dict:
        """序列化为 JSON 友好的 dict（跳过 None 值，枚举转字符串）。"""
        data = {}
        for key, value in asdict(self).items():
            if value is None:
                continue
            # 枚举类型（Severity/Category/Confidence）转字符串
            if isinstance(value, Enum):
                value = value.value
            data[key] = value
        return data
