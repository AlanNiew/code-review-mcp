# ai-code-review-mcp

一个基于 MCP（Model Context Protocol）的代码质量审查服务器，为 AI 编码助手（如 OpenCode、Claude Desktop、Cursor 等）提供本地代码分析能力。

> **PyPI**: https://pypi.org/project/ai-code-review-mcp/
> **GitHub**: https://github.com/AlanNiew/code-review-mcp

## 特性

- **文件分析** — 检测函数复杂度、行数统计、代码风格问题
- **Diff 审查** — 审查 git 未提交的变更，发现调试代码和潜在密钥泄露
- **项目扫描** — 一键扫描整个项目的代码质量概况
- **质量评分** — 为每个文件/项目计算 A-D 等级的质量评分
- **多语言支持** — Python 深度分析（AST）+ 通用质量检查（JS/TS/Java/Go/Rust 等）
- **配套 Skill** — 提供标准化的代码审查工作流提示词

---

## 安装

```bash
pip install ai-code-review-mcp
```

> 要求 Python 3.10+

---

## 使用教程

### 一、OpenCode（推荐）

OpenCode 是一个开源 AI 编码助手，原生支持 MCP 和 Skill。

#### 步骤 1：安装 MCP 服务器

```bash
pip install ai-code-review-mcp
```

#### 步骤 2：配置 OpenCode

在项目根目录创建或编辑 `opencode.json`，添加以下内容：

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

#### 步骤 3：安装配套 Skill（可选，推荐）

Skill 提供标准化的代码审查工作流，让 AI 按规范流程调用 MCP 工具。

从 GitHub 下载 Skill 文件：

```bash
# 方式一：复制到项目级目录（仅当前项目生效）
mkdir -p .opencode/skills/code-review
curl -o .opencode/skills/code-review/SKILL.md https://raw.githubusercontent.com/AlanNiew/code-review-mcp/main/skill/SKILL.md

# 方式二：复制到全局目录（所有项目生效）
mkdir -p ~/.config/opencode/skills/code-review
curl -o ~/.config/opencode/skills/code-review/SKILL.md https://raw.githubusercontent.com/AlanNiew/code-review-mcp/main/skill/SKILL.md
```

Windows 用户手动创建：

```powershell
# 项目级
New-Item -ItemType Directory -Path ".opencode\skills\code-review" -Force
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/AlanNiew/code-review-mcp/main/skill/SKILL.md" -OutFile ".opencode\skills\code-review\SKILL.md"

# 全局级
New-Item -ItemType Directory -Path "$env:USERPROFILE\.config\opencode\skills\code-review" -Force
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/AlanNiew/code-review-mcp/main/skill/SKILL.md" -OutFile "$env:USERPROFILE\.config\opencode\skills\code-review\SKILL.md"
```

#### 步骤 4：开始使用

启动 OpenCode 后，直接在对话中使用：

```
帮我分析 src/main.py 的代码质量
```

```
审查一下当前未提交的代码变更
```

```
使用 code-review 技能，扫描项目整体代码质量
```

---

### 二、Claude Desktop

#### 步骤 1：安装 MCP 服务器

```bash
pip install ai-code-review-mcp
```

#### 步骤 2：编辑配置文件

打开 Claude Desktop 配置文件：

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

添加以下内容：

```json
{
  "mcpServers": {
    "code-review-mcp": {
      "command": "ai-code-review-mcp"
    }
  }
}
```

#### 步骤 3：重启 Claude Desktop

重启后，Claude 会自动加载 MCP 工具，你可以在对话中直接使用：

```
帮我审查一下 src/utils.py 的代码质量
```

---

### 三、Cursor

#### 步骤 1：安装 MCP 服务器

```bash
pip install ai-code-review-mcp
```

#### 步骤 2：配置 Cursor

打开 Cursor 设置 → MCP，添加一个新的 MCP Server：

- **Type**: command
- **Command**: `ai-code-review-mcp`

或者在 `.cursor/mcp.json` 中添加：

```json
{
  "mcpServers": {
    "code-review-mcp": {
      "command": "ai-code-review-mcp"
    }
  }
}
```

---

### 四、使用 uvx 运行（无需 pip install）

如果你使用 [uv](https://github.com/astral-sh/uv)，可以跳过安装步骤，直接运行：

**OpenCode 配置：**

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

**Claude Desktop 配置：**

```json
{
  "mcpServers": {
    "code-review-mcp": {
      "command": "uvx",
      "args": ["ai-code-review-mcp"]
    }
  }
}
```

---

### 五、从源码运行（开发者）

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

---

## 提供的 3 个工具

### 1. `analyze_file` — 分析单个文件

分析文件的代码质量、复杂度和行数统计。

**示例对话：**
```
帮我分析 src/main.py 的代码质量
```

**返回内容：**
- 语言类型
- 代码行数 / 注释行数 / 空白行数
- 问题列表（函数过长、复杂度过高、参数过多等）
- 质量评分（A/B/C/D）

---

### 2. `review_diff` — 审查 git 变更

审查当前仓库中未提交的变更（staged + unstaged）。

**示例对话：**
```
审查一下当前未提交的代码变更
```

**检测内容：**
- 硬编码的密钥或密码（严重）
- 遗留的 `print()` / `console.log()` 调试语句
- 新增的 TODO/FIXME 标记
- 过长的代码行

---

### 3. `check_project` — 扫描项目概况

扫描整个项目的代码质量。

**示例对话：**
```
扫描一下项目整体代码质量
```

**返回内容：**
- 文件总数和总行数
- 语言分布统计
- 问题最多的前 10 个文件
- 项目整体质量评分

---

## 质量评分算法

| 严重程度 | 单项扣分 | 说明 |
|----------|----------|------|
| Error    | 10 分    | 必须修复（如硬编码密钥） |
| Warning  | 3 分     | 建议修复（如函数过长） |
| Info     | 0.5 分   | 可选优化（如 TODO 标记） |

基础分 100，扣完为止。等级划分：

| 评分 | 等级 | 含义 |
|------|------|------|
| ≥ 90 | A    | 优秀 |
| ≥ 75 | B    | 良好 |
| ≥ 60 | C    | 一般 |
| < 60 | D    | 需改进 |

---

## 支持的语言

| 语言 | 文件分析 | 复杂度分析 |
|------|----------|------------|
| Python (.py) | ✅ | ✅ AST 深度分析 |
| JavaScript (.js/.jsx) | ✅ | 通用检查 |
| TypeScript (.ts/.tsx) | ✅ | 通用检查 |
| Java (.java) | ✅ | 通用检查 |
| Go (.go) | ✅ | 通用检查 |
| Rust (.rs) | ✅ | 通用检查 |
| C/C++ (.c/.cpp/.h) | ✅ | 通用检查 |
| Ruby (.rb) | ✅ | 通用检查 |
| PHP (.php) | ✅ | 通用检查 |
| Swift (.swift) | ✅ | 通用检查 |
| Kotlin (.kt) | ✅ | 通用检查 |
| Scala (.scala) | ✅ | 通用检查 |

---

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

---

## 常见问题

### Q: 启动时报错 "command not found: ai-code-review-mcp"

确保 Python 的 Scripts 目录在系统 PATH 中：

```bash
# 检查安装位置
pip show ai-code-review-mcp

# 查找可执行文件位置
where ai-code-review-mcp    # Windows
which ai-code-review-mcp    # macOS/Linux
```

### Q: MCP 工具没有出现在 AI 助手中

1. 确认配置文件路径正确
2. 确认 `ai-code-review-mcp` 命令可以在终端直接运行
3. 重启 AI 助手应用

### Q: 只想用 MCP，不想装 Skill 可以吗？

可以。Skill 是可选的增强功能，不装也能使用所有 3 个 MCP 工具。Skill 的作用是让 AI 按标准流程输出格式化的审查报告。

---

## License

MIT © AlanNiew
