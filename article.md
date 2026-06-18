---
title: 从零开发一个 MCP 服务器 + OpenCode Skill：让 AI 学会审查你的代码
description: 手把手教你开发一套 MCP + Skill 工具组合，为 AI 编码助手赋予代码质量审查能力，并发布到 PyPI 和 GitHub 供所有人使用。
tags: [MCP, AI, 代码审查, OpenCode, Python, Skill]
date: 2026-04-16
---

# 从零开发一个 MCP 服务器 + OpenCode Skill：让 AI 学会审查你的代码

> 你有没有想过，让 AI 不仅帮你写代码，还能像 Senior Engineer 一样帮你审查代码质量？
>
> 本文将带你从零开始，开发一套 **MCP 服务器 + OpenCode Skill** 工具组合，最终发布到 PyPI 和 GitHub，让所有开发者都能用上。

## 最终效果预览

安装完成后，在 OpenCode 中只需一句话：

```
帮我审查一下 src/main.py 的代码质量
```

AI 就会自动调用工具，返回一份结构化的审查报告：

```
## 代码审查报告

### 概况
- 文件：src/main.py
- 语言：Python
- 行数：350（代码 280 行，注释 35 行）
- 质量评分：82.5（等级：B）

### 🟡 建议修复（Warning）
1. L45: 函数 `process_data` 过长 (82 行)，建议不超过 50 行
   - 修复建议：拆分为多个子函数...
2. L45: 函数 `process_data` 分支复杂度过高 (15)，建议简化逻辑

### 🔵 可选优化（Info）
1. L120: 第 120 行包含 TODO 标记
```

---

## 什么是 MCP 和 Skill？

在开始之前，先搞清楚两个核心概念。

### MCP（Model Context Protocol）

MCP 是一个开放协议，让 AI 助手（如 OpenCode、Claude Desktop、Cursor）能够调用外部工具。

简单类比：

```
Skill = 给 AI 一本操作手册（文字指导）
MCP   = 给 AI 一个可调用的 API（真正执行操作）
```

### Skill

Skill 是 OpenCode 特有的功能，用 Markdown 文件定义一套工作流提示词，让 AI 按规范流程行事。

### 两者的关系

| 维度 | Skill | MCP |
|------|-------|-----|
| 本质 | Markdown 提示词模板 | 可执行的外部服务 |
| 能力 | 只能提供文字指导 | 能真正执行操作（读文件、调 API） |
| 门槛 | 极低（写 Markdown） | 较高（需要写代码） |
| 配合使用 | Skill 定义"怎么做" | MCP 提供"能做什么" |

---

## 整体架构设计

我们要开发一个**代码审查**主题的配套方案：

```
用户提问 → AI 加载 Skill → Skill 指导 AI 调用 MCP 工具 → MCP 执行分析 → AI 生成报告
```

**MCP 服务器提供 3 个工具：**

| 工具 | 功能 |
|------|------|
| `analyze_file` | 分析单个文件的代码质量、复杂度 |
| `review_diff` | 审查 git 未提交的变更 |
| `check_project` | 扫描项目整体代码质量 |

**Skill 定义审查流程：** 按严重程度分级输出 Error → Warning → Info。

---

## 第一步：搭建项目结构

```bash
mkdir code-review-mcp && cd code-review-mcp

# 创建标准 Python 包目录
mkdir -p src/code_review_mcp
mkdir skill

# 项目结构
code-review-mcp/
├── pyproject.toml
├── README.md
├── LICENSE
├── skill/
│   └── SKILL.md
└── src/
    └── code_review_mcp/
        ├── __init__.py
        └── server.py
```

`pyproject.toml` 配置：

```toml
[project]
name = "ai-code-review-mcp"
version = "0.1.0"
description = "MCP 服务器，为 AI 编码助手提供本地代码质量审查工具"
readme = "README.md"
license = "MIT"
requires-python = ">=3.10"
authors = [
    { name = "AlanNiew", email = "728097735@qq.com" },
]
dependencies = [
    "mcp>=1.9.0",
]

[project.scripts]
ai-code-review-mcp = "code_review_mcp.server:main"

[build-system]
requires = ["setuptools>=75.0"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]
```

关键点：
- `[project.scripts]` 注册 CLI 命令，安装后可直接运行 `ai-code-review-mcp`
- `src` layout 是现代 Python 包的标准结构

---

## 第二步：开发 MCP 服务器

### 核心依赖

```bash
pip install mcp
```

### 常量和数据结构

```python
# 文件扩展名 → 语言映射
EXT_LANGUAGE_MAP = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".java": "java", ".go": "go", ".rs": "rust",
    # ... 更多语言
}

# TODO 类标记
TODO_TAGS = ("TODO", "FIXME", "HACK", "XXX")

# 阈值配置
MAX_FUNCTION_LENGTH = 50
MAX_BRANCH_COMPLEXITY = 10
MAX_LINE_LENGTH = 200
```

用 dataclass 统一问题结构：

```python
from dataclasses import dataclass, asdict

@dataclass
class Issue:
    type: str        # 问题类型：function_too_long / high_complexity / ...
    message: str     # 人类可读的描述
    line: int        # 行号
    severity: str    # error / warning / info
    name: str | None = None
    length: int | None = None
    complexity: int | None = None
```

### 工具一：analyze_file（分析单个文件）

这是最核心的工具，对 Python 文件使用 AST 做深度分析，对其他语言做通用检查。

**Python 深度分析 — 按单一职责拆分为独立函数：**

```python
def _analyze_python_complexity(content: str) -> list[Issue]:
    """分析 Python 代码的函数级复杂度"""
    tree = _parse_python_ast(content)
    if isinstance(tree, list):
        return tree  # 语法错误

    issues = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _check_function_length(node, issues)
            _check_branch_complexity(node, issues)
            _check_param_count(node, issues)
    return issues
```

每个检查函数职责单一，不超过 20 行：

```python
def _check_function_length(node, issues):
    end_line = node.end_lineno or node.lineno
    func_length = end_line - node.lineno + 1
    if func_length > MAX_FUNCTION_LENGTH:
        issues.append(Issue(
            type="function_too_long",
            message=f"函数 `{node.name}` 过长 ({func_length} 行)",
            line=node.lineno, severity="warning",
            name=node.name, length=func_length,
        ))
```

**通用质量检查 — 同样拆分为独立检查函数：**

```python
def _analyze_generic_quality(content, language):
    issues = []
    for i, line in enumerate(content.split("\n"), 1):
        _check_line_length(line, i, issues)
        _check_todo_in_line(line, i, issues)
        _check_trailing_whitespace(line, i, language, issues)
    return issues
```

**质量评分算法：**

```python
def _compute_quality_score(issues, total_lines):
    errors = sum(1 for i in issues if i.severity == "error")
    warnings = sum(1 for i in issues if i.severity == "warning")
    infos = sum(1 for i in issues if i.severity == "info")

    penalty = errors * 10 + warnings * 3 + infos * 0.5
    score = max(0, min(100, 100 - penalty))

    grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D"
    return {"score": round(score, 1), "grade": grade, ...}
```

**MCP 工具入口 — 加上 `@mcp.tool()` 装饰器即可：**

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("code-review-mcp")

@mcp.tool()
def analyze_file(file_path: str) -> dict:
    """分析单个文件的代码质量和复杂度。

    Args:
        file_path: 要分析的文件路径
    """
    path = Path(file_path)
    content = path.read_text(encoding="utf-8")

    issues = []
    if _detect_language(str(path)) == "python":
        issues.extend(_analyze_python_complexity(content))
    issues.extend(_analyze_generic_quality(content, language))

    return {
        "file": str(path),
        "language": language,
        "lines": _count_lines(content),
        "issues": [i.to_dict() for i in issues],
        "quality": _compute_quality_score(issues, total_lines),
    }
```

就这么简单！FastMCP 框架自动处理 JSON-RPC 协议，你只需要写普通的 Python 函数。

### 工具二：review_diff（审查 git 变更）

```python
@mcp.tool()
def review_diff() -> dict:
    """审查当前 git 仓库中未提交的变更"""
    staged, unstaged = _get_git_diffs()

    staged_review = _build_diff_review(staged)
    unstaged_review = _build_diff_review(unstaged)

    return {
        "staged_changes": staged_review,
        "unstaged_changes": unstaged_review,
        "overall_quality": overall,
    }
```

核心检查逻辑同样拆分为小函数：

```python
def _check_diff_secrets(added_line, line_num, issues):
    """检测可能硬编码的密钥（排除环境变量引用）"""
    lower = added_line.lower()
    has_keyword = "password" in lower or "secret" in lower or "api_key" in lower
    uses_env = "env" in lower or "os.getenv" in added_line

    if has_keyword and not uses_env:
        issues.append(Issue(type="potential_secret", severity="error", ...))
```

### 工具三：check_project（扫描项目概况）

```python
@mcp.tool()
def check_project(directory: str = ".") -> dict:
    """扫描项目目录的代码质量概况"""
    file_stats = []
    for root, dirs, files in os.walk(dir_path):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for fname in files:
            result = _scan_single_file(Path(root) / fname, dir_path)
            if result:
                file_stats.append(result)

    return {
        "summary": {"total_files": ..., "total_lines": ...},
        "languages": language_stats,
        "top_issues_files": file_stats[:10],
        "overall_quality": overall,
    }
```

### 启动入口

```python
def main():
    mcp.run(transport="stdio")

if __name__ == "__main__":
    main()
```

`transport="stdio"` 表示通过标准输入输出与 AI 助手通信，这是 MCP 本地服务器的标准方式。

---

## 第三步：开发配套 Skill

Skill 是一个 `SKILL.md` 文件，放在 `.opencode/skills/<name>/` 目录下：

```yaml
---
name: code-review
description: 标准化的代码质量审查工作流，配合 code-review-mcp 工具使用
metadata:
  audience: developers
  workflow: code-quality
---
```

然后定义审查流程和输出格式规范：

```markdown
## 审查流程

### 场景一：审查特定文件
1. 调用 `analyze_file` 分析目标文件
2. 检查质量评分、函数复杂度、行数统计、代码风格
3. 针对每个问题提供修复建议

### 输出格式
## 代码审查报告
### 概况
### 🔴 必须修复（Error）
### 🟡 建议修复（Warning）
### 🔵 可选优化（Info）
```

OpenCode 会自动发现这个 Skill，AI 在需要时会加载它并按流程执行。

---

## 第四步：测试验证

写一个简单的测试脚本，通过 JSON-RPC 协议与 MCP 服务器通信：

```python
import subprocess, json, time

proc = subprocess.Popen(
    ["python", "-m", "code_review_mcp.server"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
)

# 初始化握手
proc.stdin.write(json.dumps({
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2024-11-05", ...}
}).encode() + b"\n")
proc.stdin.flush()

# 调用工具
proc.stdin.write(json.dumps({
    "jsonrpc": "2.0", "id": 10, "method": "tools/call",
    "params": {"name": "analyze_file", "arguments": {"file_path": "server.py"}}
}).encode() + b"\n")
proc.stdin.flush()
```

测试结果——**用自己的代码审查自己**：

```
文件: server.py
语言: Python
行数: 737（代码 522 行，注释 76 行）
质量评分: 95.5（等级：A）
问题数: 1（仅 1 个 info 级别的提示）
```

---

## 第五步：发布到 PyPI 和 GitHub

### 1. 推送到 GitHub

```bash
git init
git add .
git commit -m "feat: initial release v0.1.0"
git remote add origin git@github.com:AlanNiew/code-review-mcp.git
git push -u origin main
```

### 2. 发布到 PyPI

```bash
pip install build twine
python -m build
twine upload dist/*
```

发布成功后，所有人都可以：

```bash
pip install ai-code-review-mcp
```

### 3. 验证发布

```bash
pip index versions ai-code-review-mcp
# 输出：ai-code-review-mcp (0.1.0)
```

---

## 用户如何使用

### OpenCode 用户

```json
// opencode.json
{
  "mcp": {
    "code-review-mcp": {
      "type": "local",
      "command": ["ai-code-review-mcp"],
      "enabled": true
    }
  }
}
```

### Claude Desktop 用户

```json
// claude_desktop_config.json
{
  "mcpServers": {
    "code-review-mcp": {
      "command": "ai-code-review-mcp"
    }
  }
}
```

### 免安装运行（uvx）

```json
{
  "mcp": {
    "code-review-mcp": {
      "command": ["uvx", "ai-code-review-mcp"]
    }
  }
}
```

---

## MCP 服务器的运行原理

很多人好奇 MCP 是怎么工作的，这里画个流程图：

```
┌─────────┐    提问     ┌─────────┐   JSON-RPC   ┌──────────────┐
│  用户    │───────────→│  AI/LLM │─────────────→│  server.py   │
│         │            │         │   (stdin)     │  (常驻进程)   │
│         │←───────────│         │←─────────────│              │
│         │  审查报告   │         │  分析结果     │  读文件/AST   │
└─────────┘            └─────────┘   (stdout)    └──────────────┘
```

1. **OpenCode 启动时**，根据配置拉起 `server.py` 进程，通过 stdin/stdout 通信
2. **握手阶段**，OpenCode 询问"你有哪些工具？"，server.py 返回工具列表
3. **用户提问时**，AI 根据工具描述决定是否调用，发送 JSON-RPC 请求
4. **server.py 执行**实际的分析逻辑（读文件、解析 AST、统计行数），返回 JSON 结果
5. **AI 拿到结果**，组织成人类可读的审查报告

FastMCP 框架自动处理了步骤 2-4 的协议细节，你只需要写普通的 Python 函数。

---

## 开发过程中的经验总结

### 1. 函数拆分是关键

最初的 `review_diff` 函数有 160 行、复杂度 22，经过拆分后变为 6 个小函数，每个不超过 20 行。质量评分从 **78.5（B）** 提升到 **95.5（A）**。

### 2. 用 dataclass 替代松散 dict

```python
# Before: 结构松散，字段不确定
{"type": "...", "message": "...", "severity": "..."}

# After: 类型明确，IDE 有提示
@dataclass
class Issue:
    type: str
    message: str
    severity: str
```

### 3. 共享逻辑要提取

TODO 标记检测逻辑最初出现了 3 次，提取为 `_detect_todo_tag()` 后消除了重复。

### 4. Skill 和 MCP 的配合

- **MCP** 解决"能做什么"——读文件、解析代码、计算评分
- **Skill** 解决"怎么做"——按什么流程审查、输出什么格式

两者配合，AI 既有能力又有规范。

---

## 项目链接

- **GitHub**: https://github.com/AlanNiew/code-review-mcp
- **PyPI**: https://pypi.org/project/ai-code-review-mcp/
- **License**: MIT（可自由使用和修改）

欢迎 Star、Fork、提交 Issue 和 PR！

---

## 写在最后

MCP 是 AI 编程工具生态的重要基础设施。开发一个 MCP 服务器并不复杂——核心就是**写 Python 函数 + 加装饰器**。配合 Skill，你可以让 AI 按照你定义的工作流来工作。

希望这篇文章能帮你理解 MCP + Skill 的开发流程，也欢迎试试 `ai-code-review-mcp`，给我反馈！

---

> 作者：AlanNiew
> GitHub：https://github.com/AlanNiew
> 发布日期：2026-04-16
