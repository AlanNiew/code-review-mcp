"""配置加载和行级豁免测试。"""

import json
from pathlib import Path

from code_review_mcp.config import (
    Config,
    get_line_ignored_rules,
    is_issue_suppressed,
    is_path_ignored,
    load_config,
)

# ==================== 默认配置 ====================


def test_default_config_has_thresholds():
    cfg = Config()
    assert cfg.threshold("max_function_length") == 50
    assert cfg.threshold("max_cognitive_complexity") == 15
    assert cfg.threshold("nonexistent", default=42) == 42


def test_default_config_all_categories_enabled():
    cfg = Config()
    assert cfg.is_category_enabled("security") is True
    assert cfg.is_category_enabled("complexity") is True
    assert cfg.is_category_enabled("style") is True
    # 未知类别默认启用（避免漏检）
    assert cfg.is_category_enabled("unknown_category") is True


# ==================== JSON 配置加载 ====================


def test_load_json_config(tmp_path: Path):
    config_data = {
        "thresholds": {"max_function_length": 80},
        "rules": {"security": {"enabled": False}, "complexity": True},
        "ignore_paths": ["tests/*", "vendor/"],
        "severity_overrides": {"SEC009": "warning"},
    }
    config_path = tmp_path / ".code-review.json"
    config_path.write_text(json.dumps(config_data), encoding="utf-8")

    cfg = load_config(tmp_path)
    assert cfg.config_path == config_path
    assert cfg.threshold("max_function_length") == 80
    assert cfg.is_category_enabled("security") is False
    assert cfg.is_category_enabled("complexity") is True
    assert cfg.ignore_paths == ["tests/*", "vendor/"]
    assert cfg.severity_overrides == {"SEC009": "warning"}


def test_load_config_recursive_search(tmp_path: Path):
    """配置文件应在父目录也能找到。"""
    config_data = {"thresholds": {"max_line_length": 100}}
    (tmp_path / ".code-review.json").write_text(json.dumps(config_data), encoding="utf-8")

    nested = tmp_path / "src" / "deep" / "module"
    nested.mkdir(parents=True)

    cfg = load_config(nested)
    assert cfg.threshold("max_line_length") == 100


def test_env_var_overrides(tmp_path: Path, monkeypatch):
    """环境变量 CODE_REVIEW_CONFIG 优先级最高。"""
    config_data = {"thresholds": {"max_param_count": 10}}
    env_path = tmp_path / "custom-config.json"
    env_path.write_text(json.dumps(config_data), encoding="utf-8")

    # 在另一目录，但用环境变量指向配置
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    monkeypatch.setenv("CODE_REVIEW_CONFIG", str(env_path))

    cfg = load_config(other_dir)
    assert cfg.threshold("max_param_count") == 10


def test_invalid_json_falls_back_to_default(tmp_path: Path):
    """JSON 解析失败时降级到默认配置，不抛异常。"""
    (tmp_path / ".code-review.json").write_text("{ invalid json }", encoding="utf-8")
    cfg = load_config(tmp_path)
    # 默认值
    assert cfg.threshold("max_function_length") == 50


# ==================== 路径忽略 ====================


def test_ignore_path_glob():
    cfg = Config(ignore_paths=["tests/*", "vendor/"])
    assert is_path_ignored(Path("tests/test_app.py"), cfg) is True
    assert is_path_ignored(Path("vendor/lib.py"), cfg) is True
    assert is_path_ignored(Path("src/app.py"), cfg) is False


def test_ignore_path_basename():
    cfg = Config(ignore_paths=["test_*.py"])
    assert is_path_ignored(Path("test_app.py"), cfg) is True
    assert is_path_ignored(Path("src/test_app.py"), cfg) is True
    assert is_path_ignored(Path("app.py"), cfg) is False


def test_no_ignore_paths_disables():
    cfg = Config()
    assert is_path_ignored(Path("anything.py"), cfg) is False


# ==================== 行级豁免 ====================


def test_line_suppress_all():
    """# codereview: ignore 豁免所有规则。"""
    line = "    eval('1+1')  # codereview: ignore"
    ignored = get_line_ignored_rules(line)
    assert ignored == set()  # 空集合 = 豁免所有
    assert is_issue_suppressed([line], 1, "SEC001") is True
    assert is_issue_suppressed([line], 1, "SEC002") is True


def test_line_suppress_specific_rules():
    """# codereview: ignore=SEC001,SEC005 只豁免指定规则。"""
    line = "    eval('1')  # codereview: ignore=SEC001,SEC005"
    ignored = get_line_ignored_rules(line)
    assert ignored == {"SEC001", "SEC005"}
    assert is_issue_suppressed([line], 1, "SEC001") is True
    assert is_issue_suppressed([line], 1, "SEC005") is True
    assert is_issue_suppressed([line], 1, "SEC002") is False


def test_line_no_suppress():
    line = "    eval('1+1')"
    assert get_line_ignored_rules(line) is None
    assert is_issue_suppressed([line], 1, "SEC001") is False


def test_suppress_out_of_range():
    """行号越界返回 False。"""
    assert is_issue_suppressed(["x"], 5, "SEC001") is False
    assert is_issue_suppressed(["x"], 0, "SEC001") is False


def test_suppress_case_insensitive():
    """豁免标记大小写不敏感。"""
    line = "    eval('1')  # CodeReview: IGNORE=sec001"
    assert is_issue_suppressed([line], 1, "SEC001") is True
