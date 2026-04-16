# ai-code-review-mcp

一个基于 MCP（Model Context Protocol）的代码质量审查服务器，为 AI 编码助手（如 OpenCode、Claude Desktop、Cursor 等）提供本地代码分析能力。

## 特性

- **文件分析** — 检测函数复杂度、行数统计、代码风格问题
- **Diff 审查** — 审查 git 未提交的变更，发现调试代码和潜在密钥泄露
- **项目扫描** — 一键扫描整个项目的代码质量概况
- **质量评分** — 为每个文件/项目计算 A-D 等级的质量评分
- **多语言支持** — Python 深度分析（AST）+ 通用质量检查（JS/TS/Java/Go/Rust 等）
- **配套 Skill** — 提供标准化的代码审查工作流提示词

## 快速开始

### 安装

```bash
pip install ai-code-review-mcp
```

### 配置 OpenCode

在项目的 `opencode.json` 中添加：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "code-review-mcp": {
      "type": "local",
      "command": ["ai-code-review-mcp"],
      "enabled": true
    }
  }
}
```

### 配置 Claude Desktop

在 `claude_desktop_config.json` 中添加：

```json
{
  "mcpServers": {
    "code-review-mcp": {
      "command": "ai-code-review-mcp"
    }
  }
}
```

### 安装配套 Skill

将 `skill/` 目录复制到你的项目中：

```bash
cp -r skill/ .opencode/skills/code-review/
```

或者复制到全局目录：

```bash
cp -r skill/ ~/.config/opencode/skills/code-review/
```

## 提供的工具

### `analyze_file` — 分析单个文件

分析文件的代码质量、复杂度和行数统计。

```
帮我分析 src/main.py 的代码质量
```

返回内容：
- 语言类型
- 代码行数 / 注释行数 / 空白行数
- 问题列表（函数过长、复杂度过高、参数过多等）
- 质量评分（A/B/C/D）

### `review_diff` — 审查 git 变更

审查当前仓库中未提交的变更（staged + unstaged）。

```
审查一下当前未提交的代码变更
```

检测内容：
- 硬编码的密钥或密码
- 遗留的 `print()` / `console.log()` 调试语句
- 新增的 TODO/FIXME 标记
- 过长的代码行

### `check_project` — 扫描项目概况

扫描整个项目的代码质量。

```
扫描一下项目整体代码质量
```

返回内容：
- 文件总数和总行数
- 语言分布统计
- 问题最多的前 10 个文件
- 项目整体质量评分

## 使用 uvx 运行（无需安装）

如果你使用 [uv](https://github.com/astral-sh/uv)，可以直接运行：

```json
{
  "mcp": {
    "code-review-mcp": {
      "type": "local",
      "command": ["uvx", "ai-code-review-mcp"],
      "enabled": true
    }
  }
}
```

## 从源码运行

```bash
git clone https://github.com/AlanNiew/code-review-mcp.git
cd code-review-mcp
pip install -e .
```

然后在配置中使用：

```json
{
  "mcp": {
    "code-review-mcp": {
      "type": "local",
      "command": ["python", "-m", "code_review_mcp.server"],
      "enabled": true
    }
  }
}
```

## 质量评分算法

| 严重程度 | 单项扣分 |
|----------|----------|
| Error    | 10 分    |
| Warning  | 3 分     |
| Info     | 0.5 分   |

基础分 100，扣完为止。等级划分：

| 评分 | 等级 |
|------|------|
| ≥ 90 | A    |
| ≥ 75 | B    |
| ≥ 60 | C    |
| < 60 | D    |

## 项目结构

```
code-review-mcp/
├── pyproject.toml                    # Python 包配置
├── README.md                         # 本文件
├── LICENSE                           # MIT 许可证
├── skill/
│   └── SKILL.md                      # 配套的 OpenCode Skill
└── src/
    └── code_review_mcp/
        ├── __init__.py               # 包入口
        └── server.py                 # MCP 服务器主程序
```

## 开发

```bash
# 克隆仓库
git clone https://github.com/AlanNiew/code-review-mcp.git
cd code-review-mcp

# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest
```

## License

MIT
