"""配置加载与规则开关 / 行级豁免处理。

借鉴：
- fossil-mcp 的 fossil.toml（规则开关 + 阈值 + ignore paths）
- DeepSource 的 .deepsource.toml（severity overrides）
- ruff/bandit 的行级豁免（# noqa / # nosec）

配置文件查找顺序（自上而下，前者覆盖后者）：
1. 环境变量 CODE_REVIEW_CONFIG 指定的路径
2. 项目根目录的 .code-review.yml / .code-review.yaml / .code-review.json
3. 内置默认配置

为保持零运行时依赖，YAML 解析采用极简实现：只支持本工具用到的少量结构。
若需要复杂配置，建议使用 JSON 格式。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# YAML 是可选依赖：若用户装了 PyYAML 则支持 yaml 配置文件
try:
    import yaml  # type: ignore

    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


# ==================== 默认配置 ====================

DEFAULT_THRESHOLDS: dict = {
    "max_function_length": 50,  # 函数最大行数
    "max_cyclomatic_complexity": 10,  # 最大圈复杂度
    "max_cognitive_complexity": 15,  # 最大认知复杂度（Sonar 默认）
    "max_param_count": 5,  # 最大参数个数
    "max_line_length": 120,  # 最大行长（更严格的业界默认值）
    "max_nesting_depth": 4,  # 最大嵌套深度
}

DEFAULT_RULES_CONFIG: dict = {
    "security": {"enabled": True},
    "complexity": {"enabled": True},
    "style": {"enabled": True},
}

DEFAULT_IGNORE_PATHS: list[str] = []

DEFAULT_SEVERITY_OVERRIDES: dict = {}


@dataclass
class Config:
    """运行时配置。

    所有字段都有默认值，确保不读配置文件也能正常工作。
    """

    thresholds: dict = field(default_factory=lambda: dict(DEFAULT_THRESHOLDS))
    rules: dict = field(
        default_factory=lambda: {k: dict(v) for k, v in DEFAULT_RULES_CONFIG.items()}
    )
    ignore_paths: list[str] = field(default_factory=list)
    severity_overrides: dict = field(default_factory=dict)
    config_path: Path | None = None

    # ====== 阈值的便捷访问器 ======

    def threshold(self, key: str, default: int = 0) -> int:
        """读取阈值，缺失时返回 default。"""
        value = self.thresholds.get(key, default)
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    # ====== 规则开关 ======

    def is_category_enabled(self, category: str) -> bool:
        """检查某个规则类别是否启用。"""
        cfg = self.rules.get(category)
        if cfg is None:
            # 未知类别默认启用
            return True
        if isinstance(cfg, bool):
            return cfg
        if isinstance(cfg, dict):
            return bool(cfg.get("enabled", True))
        return True

    def get_severity(self, rule_id: str, default_severity) -> str:
        """获取规则的有效严重级别（应用 override 后）。

        参数 default_severity 是规则定义中的默认级别（str 值）。
        """
        override = self.severity_overrides.get(rule_id)
        if override:
            return str(override).lower()
        return str(default_severity).lower() if default_severity else "info"


# ==================== 配置文件加载 ====================

_CONFIG_FILENAMES = (
    ".code-review.yml",
    ".code-review.yaml",
    ".code-review.json",
)


def load_config(start_dir: Path | None = None) -> Config:
    """加载配置：环境变量 → 项目根配置文件 → 默认。

    会从 start_dir 向上递归查找配置文件，直到遇到 .git 目录或文件系统根。
    """
    # 优先级 1：环境变量指定的路径
    env_path = os.environ.get("CODE_REVIEW_CONFIG")
    if env_path:
        path = Path(env_path)
        if path.is_file():
            return _load_from_file(path)

    # 优先级 2：向上查找配置文件
    if start_dir is None:
        start_dir = Path.cwd()

    found = _find_config_file(start_dir)
    if found:
        return _load_from_file(found)

    # 优先级 3：默认配置
    return Config()


def _find_config_file(start_dir: Path) -> Path | None:
    """从 start_dir 向上递归查找配置文件，直到 .git 或根目录。"""
    current = start_dir.resolve()
    while True:
        for name in _CONFIG_FILENAMES:
            candidate = current / name
            if candidate.is_file():
                return candidate
        # 遇到 .git 目录（项目根）也停一下，但继续向上找一层以备 monorepo
        if (current / ".git").exists():
            return None
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _load_from_file(path: Path) -> Config:
    """从 YAML / JSON 文件加载配置。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return Config()

    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return Config(config_path=path)
    else:
        if not _HAS_YAML:
            # 没装 PyYAML 时，对 yaml 配置降级为默认配置并打日志
            return Config(config_path=path)
        try:
            data = yaml.safe_load(text) or {}
        except Exception:
            return Config(config_path=path)

    if not isinstance(data, dict):
        return Config(config_path=path)

    return _build_config_from_dict(data, path)


def _build_config_from_dict(data: dict, path: Path) -> Config:
    """从字典构造 Config，缺失字段使用默认值。"""
    thresholds = dict(DEFAULT_THRESHOLDS)
    thresholds.update(data.get("thresholds", {}) or {})

    rules = {k: dict(v) for k, v in DEFAULT_RULES_CONFIG.items()}
    user_rules = data.get("rules", {}) or {}
    if isinstance(user_rules, dict):
        for key, value in user_rules.items():
            if isinstance(value, bool):
                rules[key] = {"enabled": value}
            elif isinstance(value, dict):
                merged = {"enabled": value.get("enabled", True)}
                # 允许在规则下放自定义阈值（如 rules.complexity.max_function_length）
                for k, v in value.items():
                    if k != "enabled":
                        merged[k] = v
                rules[key] = merged
            else:
                rules[key] = {"enabled": bool(value)}

    ignore_paths = data.get("ignore_paths", []) or []
    if not isinstance(ignore_paths, list):
        ignore_paths = [ignore_paths]

    severity_overrides = data.get("severity_overrides", {}) or {}
    if not isinstance(severity_overrides, dict):
        severity_overrides = {}

    return Config(
        thresholds=thresholds,
        rules=rules,
        ignore_paths=[str(p) for p in ignore_paths],
        severity_overrides=severity_overrides,
        config_path=path,
    )


# ==================== 路径忽略 ====================


def is_path_ignored(file_path: Path, config: Config) -> bool:
    """检查文件是否被 ignore_paths 命中（fnmatch 风格 + 目录前缀）。

    支持三种 pattern 写法：
    - "tests/*" / "*.bak"：fnmatch glob（同时匹配 basename 和完整路径）
    - "vendor/"：以 / 结尾，按目录前缀匹配（vendor/anything 都会被忽略）
    - "tests"：纯目录名，匹配路径中包含该目录段
    """
    if not config.ignore_paths:
        return False

    import fnmatch

    file_str = str(file_path).replace("\\", "/")
    file_parts = file_str.split("/")
    for pattern in config.ignore_paths:
        pattern = pattern.replace("\\", "/")
        # 1. 以 / 结尾：目录前缀匹配
        if pattern.endswith("/"):
            if file_str.startswith(pattern) or file_str.startswith("/" + pattern):
                return True
            continue
        # 2. 标准 fnmatch（同时尝试完整路径和 basename）
        if fnmatch.fnmatch(file_str, pattern):
            return True
        if fnmatch.fnmatch(file_path.name, pattern):
            return True
        # 3. 路径中任意一段匹配（处理 "tests" 这种纯目录名）
        if pattern in file_parts:
            return True
    return False


# ==================== 行级豁免 ====================

# 行级豁免标记：# codereview: ignore[=RULE_ID[,RULE_ID]]
IGNORE_MARKER = re.compile(
    r"codereview:\s*ignore(?:=([A-Z0-9_,\s]+))?",
    re.IGNORECASE,
)


def get_line_ignored_rules(line: str) -> set[str] | None:
    """解析一行的豁免标记。

    返回值含义：
    - None：该行无豁免标记
    - 空集合 set()：豁免该行的所有规则
    - 非空集合：只豁免集合中的规则 ID
    """
    match = IGNORE_MARKER.search(line)
    if not match:
        return None
    ids_str = match.group(1)
    if not ids_str:
        return set()  # 全部豁免
    ids = {s.strip().upper() for s in ids_str.split(",") if s.strip()}
    return ids


def is_issue_suppressed(source_lines: list[str], line_no: int, rule_id: str) -> bool:
    """检查某个 issue 是否被同行的豁免标记豁免。

    参数 line_no 是 1-indexed 的行号。
    """
    if line_no < 1 or line_no > len(source_lines):
        return False
    line = source_lines[line_no - 1]
    ignored = get_line_ignored_rules(line)
    if ignored is None:
        return False
    # 空集合 → 豁免所有；非空集合 → 看是否包含
    return (not ignored) or (rule_id.upper() in ignored)
