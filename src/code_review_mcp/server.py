"""代码审查 MCP 服务器主入口。

提供 5 个 MCP 工具：
- review_changes: 变更审查证据包（v0.3.0 旗舰）
- analyze_file: 分析单个文件的质量和复杂度
- check_project: 扫描项目整体代码质量
- list_rules: 列出已注册规则
- review_diff: 旧版未提交变更审查（已弃用，保留兼容）

v0.3.0 定位：工具只负责返回**事实**（findings / signals / 复杂度回归），
不返回审查结论。审查判断与定级由配套 skill 的审查协议完成。
"""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

# 导入触发规则自动注册
from .analyzers import (
    analyze_diff,
    analyze_file_content,
    analyze_project_directory,
    review_change_set,
)
from .config import load_config
from .rules import registry  # noqa: F401  保证 side effect 注册
from .utils import MAX_FILE_SIZE

mcp = FastMCP("code-review-mcp")


# ==================== MCP 工具入口 ====================


@mcp.tool()
def review_changes(scope: str = "working", base: str | None = None, directory: str = ".") -> dict:
    """审查一个 git 变更，返回「审查证据包」：改了哪些文件、风险在哪、新增行命中了什么。

    这是本服务器的旗舰工具。它只返回事实，不返回审查结论——每一条 findings/signal
    都需要审查者回到源码确认后再决定是否成立、什么级别。

    Args:
        scope: 审查范围。
            - "working"（默认）：所有未提交改动 = 未暂存 + 已暂存 + 未跟踪文件（含源码与配置）
            - "staged"：只审已暂存（git add 过的）
            - "base"：审 base...HEAD（git merge-base 三点差异），必须同时给 base
        base: 基线分支/提交，如 "main"、"origin/main"、commit sha；仅 scope="base" 时使用
        directory: 仓库目录（也可以是仓库内子目录，此时只审该子目录下的变更），
            默认当前工作目录

    返回：
        - scope / range / base_commit / head_commit
        - stats：文件数、增删行、按状态分组、语言分布
        - files[]：每个变更文件含 path、status、additions、deletions、language、
          priority（high/medium/low）、signals、findings（只含新增行命中，按行号排序）、
          function_deltas（Python 函数圈复杂度/长度前后对比）
        - signals[]：变更级信号（dependency_added / database_schema / ci_or_build /
          api_contract / test_change / secret_added / security_finding / large_change /
          config_change）；只有 SEC005（硬编码密钥）记为 secret_added，其余安全命中记为
          security_finding
        - focus[]：按风险排序的深审入口（形如 "path:line — 描述"）
        - limits：skipped（被跳过的文件及原因）、truncated、notes

    使用顺序：focus → priority=high 的文件 → signals → limits。
    需要看调用方、类型定义、测试时直接读文件；不要只凭本工具输出下结论。
    非 git 仓库会返回 {"error", "hint"}，单文件场景请改用 analyze_file。
    """
    return review_change_set(scope=scope, base=base, repo_root=directory)


@mcp.tool()
def analyze_file(file_path: str) -> dict:
    """分析单个文件的代码质量、复杂度和安全问题。

    会运行所有适用的规则：
    - 安全规则（SEC001-SEC012）：eval/exec、shell 注入、pickle、弱哈希、硬编码密钥、SSL 关闭等
    - 复杂度规则（COMPLEX001-005）：函数长度、圈复杂度、认知复杂度、参数个数、嵌套深度
    - 风格规则（STYLE001-005）：行长、TODO、末尾空白、Python print / JS console.log 调试残留

    返回语言、代码/注释/空白行数、issues（含 rule id、severity、category、confidence、
    suggestion）与多维评分（overall / security / maintainability，A-F）。
    评分是文件健康度参考，不是审查结论。

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
    """[已弃用] 审查当前 git 仓库中未提交的变更（staged + unstaged）。

    保留仅为兼容 v0.2.x 调用方；新代码请使用 review_changes，它提供变更级信号、
    只报新增行、复杂度回归与风险焦点，且覆盖 working/staged/base 三种范围。

    本工具对 diff 新增行做以下检测（DIFF001-DIFF004）：
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

    适合在不熟悉的仓库里摸底，**不要把它当审查结论**。

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
