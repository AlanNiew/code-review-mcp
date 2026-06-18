"""代码审查 MCP 服务器主入口。

提供 3 个 MCP 工具：
- analyze_file: 分析单个文件的代码质量和复杂度
- review_diff: 审查 git diff 中的变更
- check_project: 扫描项目整体代码质量

v0.2.0 升级要点：
- 模块化拆分：rules/analyzers/scoring 各司其职
- 规则引擎：可注册、可配置开关、支持行级豁免
- 多维评分：质量分 + 安全分 + 可维护性分
- 12 条安全规则（SEC001-SEC012）+ 5 条复杂度规则 + 4 条风格规则
"""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

# 导入触发规则自动注册
from .analyzers import (
    analyze_diff,
    analyze_file_content,
    analyze_project_directory,
)
from .config import load_config
from .rules import registry  # noqa: F401  保证 side effect 注册
from .utils import MAX_FILE_SIZE

mcp = FastMCP("code-review-mcp")


# ==================== MCP 工具入口 ====================


@mcp.tool()
def analyze_file(file_path: str) -> dict:
    """分析单个文件的代码质量、复杂度和安全问题。

    会运行所有适用的规则：
    - 安全规则（SEC001-SEC012）：eval/exec、shell 注入、pickle、弱哈希、硬编码密钥、SSL 关闭等
    - 复杂度规则（COMPLEX001-005）：函数长度、圈复杂度、认知复杂度、参数个数、嵌套深度
    - 风格规则（STYLE001-004）：行长、TODO、末尾空白、调试残留

    Args:
        file_path: 要分析的文件路径（相对于项目根目录或绝对路径）
    """
    path = Path(file_path)
    if not path.exists():
        return {"error": f"文件不存在: {file_path}"}
    if path.is_dir():
        return {"error": f"路径是目录，不是文件: {file_path}"}
    if path.stat().st_size > MAX_FILE_SIZE:
        return {"error": "文件过大（超过 5MB），请分析较小的文件"}

    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"error": "无法以 UTF-8 编码读取文件"}

    config = load_config(path.parent)
    return analyze_file_content(content, str(path), config=config)


@mcp.tool()
def review_diff() -> dict:
    """审查当前 git 仓库中未提交的变更（staged + unstaged）。

    对 diff 中的新增行做以下检测：
    - 硬编码的密钥或密码（严重）
    - 遗留的 print/console.log 调试语句
    - 新增的 TODO/FIXME/HACK 标记
    - 过长的新增行
    """
    return analyze_diff()


@mcp.tool()
def check_project(directory: str = ".") -> dict:
    """扫描项目目录的代码质量概况。

    遍历项目下所有支持的源代码文件（Python/JS/TS/Java/Go/Rust 等），
    对每个文件运行完整规则集，最后汇总：

    - 总文件数和总行数
    - 语言分布统计
    - 错误/警告/提示计数
    - 问题最多的前 10 个文件
    - 整体质量评分（A-F 等级）

    Args:
        directory: 要扫描的项目目录路径，默认为当前目录
    """
    return analyze_project_directory(directory)


# ==================== 工具元信息（暴露规则清单给 AI）====================


@mcp.tool()
def list_rules() -> dict:
    """列出所有已注册的代码审查规则，便于 AI 选择性调用。

    返回每条规则的 ID、名称、描述、类别和默认严重级别。
    """
    rules_info = []
    for rule in registry.all_rules():
        rules_info.append(
            {
                "id": rule.RULE_ID,
                "name": rule.NAME,
                "description": rule.DESCRIPTION,
                "category": rule.CATEGORY.value
                if hasattr(rule.CATEGORY, "value")
                else str(rule.CATEGORY),
                "severity": rule.DEFAULT_SEVERITY.value
                if hasattr(rule.DEFAULT_SEVERITY, "value")
                else str(rule.DEFAULT_SEVERITY),
                "languages": list(rule.LANGUAGES) if rule.LANGUAGES else ["*"],
            }
        )

    # 按 ID 排序便于阅读
    rules_info.sort(key=lambda x: x["id"])
    return {
        "total": len(rules_info),
        "rules": rules_info,
    }


def main():
    """CLI 入口点，供 pip install 后直接运行 ai-code-review-mcp 命令。"""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
