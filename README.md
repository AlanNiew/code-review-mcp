# ai-code-review-mcp

一个基于 MCP（Model Context Protocol）的代码质量审查服务器，为 AI 编码助手（如 OpenCode、Claude Desktop、Cursor 等）提供本地代码分析能力。

> **PyPI**: https://pypi.org/project/ai-code-review-mcp/
> **GitHub**: https://github.com/AlanNiew/code-review-mcp

## 特性

- **文件分析** — 检测函数复杂度、行数统计、代码风格问题
- **Diff 审查** — 审查 git 未提交的变更，发现调试代码和潜在密钥泄露
- **项目扫描** — 一键扫描整个项目的代码质量概况
- **质量评分** — 多维评分（总分 / 安全 / 可维护性）A-F 等级
- **21 条规则** — 12 条安全规则（SEC001-SEC012）+ 5 条复杂度规则（COMPLEX001-005）+ 4 条风格规则（STYLE001-004）
- **认知复杂度** — 业界领先的人脑理解难度算法（SonarSource 白皮书实现）
- **配置文件** — `.code-review.yml` 自定义规则开关、阈值、忽略路径
- **行级豁免** — `# codereview: ignore` 灵活豁免误报
- **多语言支持** — Python 深度分析（AST）+ 通用质量检查（JS/TS/Java/Go/Rust 等）
- **配套 Skill** — 提供标准化的代码审查工作流提示词
- **零外部依赖** — 仅依赖 `mcp` 包；YAML 配置可选装 PyYAML

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

## 提供的工具

> v0.2.0 新增了 `list_rules` 工具，可在对话中查询所有已注册规则。

### 1. `analyze_file` — 分析单个文件

分析文件的代码质量、复杂度和安全问题。

**示例对话：**
```
帮我分析 src/main.py 的代码质量
```

**返回内容：**
- 语言类型、代码/注释/空白行数
- 问题列表（21 条规则适用时全部触发）
- 多维质量评分：overall / security / maintainability（A-F 等级）

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
- 文件总数和总行数、错误/警告/提示计数
- 语言分布统计
- 问题最多的前 10 个文件
- 项目整体质量评分（A-F 等级）

---

### 4. `list_rules` — 查询规则清单（v0.2.0 新增）

返回所有已注册规则的元信息（ID、名称、描述、类别、严重级别、适用语言）。

**示例对话：**
```
列出所有代码审查规则
```

---

## 规则参考

### 安全规则（SEC001-SEC012）

借鉴 [bandit](https://github.com/PyCQA/bandit) 的 AST 节点分发模式，针对 Python。

| 规则 ID | 名称 | 严重级别 | 检测目标 |
|---------|------|---------|---------|
| SEC001 | eval-exec | 🔴 error | `eval()` / `exec()` 任意代码执行 |
| SEC002 | shell-injection | 🔴 error | `subprocess(shell=True)` / `os.system` 命令注入 |
| SEC003 | unsafe-deserialize | 🔴 error | `pickle.loads` / `yaml.load` 不安全反序列化 |
| SEC004 | weak-hash | 🟡 warning | `hashlib.md5` / `sha1` 弱哈希 |
| SEC005 | hardcoded-secret | 🔴 error | 硬编码密码 / API Key |
| SEC006 | ssl-verify-false | 🔴 error | `requests(verify=False)` 关闭 SSL 校验 |
| SEC007 | bare-except | 🟡 warning | bare except / `except: pass` 吞异常 |
| SEC008 | sql-injection | 🟡 warning | SQL 字符串拼接 |
| SEC009 | assert-in-production | 🔵 info | 生产代码中的 `assert` |
| SEC010 | flask-debug | 🔴 error | Flask `debug=True` |
| SEC011 | jwt-no-verify | 🔴 error | JWT 关闭签名校验 |
| SEC012 | weak-random | 🟡 warning | 安全场景使用 `random` 而非 `secrets` |

### 复杂度规则（COMPLEX001-005）

| 规则 ID | 名称 | 严重级别 | 默认阈值 |
|---------|------|---------|---------|
| COMPLEX001 | function-too-long | 🟡 warning | 函数 > 50 行 |
| COMPLEX002 | cyclomatic-complexity | 🟡 warning | 圈复杂度 > 10 |
| COMPLEX003 | cognitive-complexity | 🟡 warning | 认知复杂度 > 15（[SonarSource 白皮书](https://www.sonarsource.com/docs/CognitiveComplexity.pdf)）|
| COMPLEX004 | too-many-params | 🔵 info | 参数 > 5 个 |
| COMPLEX005 | nesting-too-deep | 🟡 warning | 嵌套 > 4 层 |

### 风格规则（STYLE001-004）

| 规则 ID | 名称 | 严重级别 | 检测目标 |
|---------|------|---------|---------|
| STYLE001 | line-too-long | 🟡 warning | 单行 > 120 字符 |
| STYLE002 | todo-comment | 🔵 info | 注释中的 TODO/FIXME/HACK/XXX |
| STYLE003 | trailing-whitespace | 🔵 info | 行末空白字符 |
| STYLE004 | debug-print | 🔵 info | `print` / `console.log` 调试残留 |

---

## 配置文件（v0.2.0 新增）

在项目根目录创建 `.code-review.yml`、`.code-review.yaml` 或 `.code-review.json` 即可自定义行为。
（YAML 需要可选依赖 `pip install ai-code-review-mcp[yaml]`）

**`.code-review.json` 示例：**

```json
{
  "rules": {
    "security": { "enabled": true },
    "complexity": { "enabled": true },
    "style": { "enabled": true }
  },
  "thresholds": {
    "max_function_length": 80,
    "max_cognitive_complexity": 20,
    "max_line_length": 100
  },
  "ignore_paths": [
    "tests/*",
    "vendor/",
    "migrations/",
    "*_pb2.py"
  ],
  "severity_overrides": {
    "SEC009": "warning"
  }
}
```

**配置项说明：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `rules.<category>.enabled` | bool | 关闭整类规则（如 `rules.style.enabled = false`）|
| `thresholds.<key>` | int | 自定义阈值（见复杂度规则表）|
| `ignore_paths` | list | 路径忽略列表（fnmatch glob + 目录前缀）|
| `severity_overrides` | dict | 覆盖单条规则的默认严重级别 |

**配置查找顺序：**
1. 环境变量 `CODE_REVIEW_CONFIG` 指定的路径（最高优先级）
2. 从当前目录向上递归查找 `.code-review.{yml,yaml,json}`
3. 内置默认配置

---

## 行级豁免（v0.2.0 新增）

借鉴 `# noqa` / `# nosec` 的设计，你可以针对单行豁免规则：

```python
# 豁免当前行的所有规则
token = "hardcoded-for-dev-only-1234567890"  # codereview: ignore

# 只豁免指定规则
password = "test1234"  # codereview: ignore=SEC005
```

豁免标记大小写不敏感，可以放在行内任意位置。

---

## 质量评分算法

### 多维评分（v0.2.0 新增）

每个分析结果包含 3 个独立维度的评分：

| 维度 | 包含的类别 | 用途 |
|------|-----------|------|
| **overall** | 所有 issue | 综合质量画像 |
| **security** | 仅 `security` 类 | 安全风险隔离（"代码漂亮但有 SQL 注入"也能被识别）|
| **maintainability** | `complexity` / `style` / `debug_code` / `duplication` | 可维护性画像 |

### 严重级别权重

| 严重程度 | 单项扣分 | 说明 |
|----------|----------|------|
| Error    | 10 分    | 必须修复（如硬编码密钥） |
| Warning  | 3 分     | 建议修复（如函数过长） |
| Info     | 0.5 分   | 可选优化（如 TODO 标记） |

基础分 100，扣完为止。等级划分（借鉴 DeepSource）：

| 评分 | 等级 | 含义 |
|------|------|------|
| ≥ 90 | A    | 优秀 |
| ≥ 80 | B    | 良好 |
| ≥ 70 | C    | 合格 |
| ≥ 60 | D    | 较差 |
| < 60 | F    | 不合格 |

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
├── pyproject.toml                    # Python 包配置（含 ruff/pytest 配置）
├── README.md                         # 本文件
├── CHANGELOG.md                      # 变更记录
├── LICENSE                           # MIT 许可证
├── .github/workflows/
│   ├── ci.yml                        # CI：多版本矩阵 + ruff + pytest
│   └── publish.yml                   # 自动发布 PyPI + GitHub Release
├── skill/
│   └── SKILL.md                      # 配套的 OpenCode Skill
├── tests/                            # 测试套件（100 个用例）
│   ├── conftest.py                   # 共享 fixtures
│   ├── test_security_rules.py        # SEC001-SEC012 正反例
│   ├── test_complexity.py            # 认知复杂度算法 + 规则
│   ├── test_style_rules.py           # 风格规则
│   ├── test_config.py                # 配置加载 + 行级豁免
│   ├── test_scoring.py               # 多维评分
│   └── test_tools.py                 # MCP 工具端到端
└── src/
    └── code_review_mcp/
        ├── __init__.py
        ├── server.py                 # MCP 服务器主入口（4 个工具）
        ├── models.py                 # Issue + Severity + Category 枚举
        ├── config.py                 # 配置加载 + 行级豁免
        ├── scoring.py                # 多维评分算法
        ├── utils.py                  # 通用工具函数
        ├── analyzers/
        │   ├── __init__.py
        │   ├── context.py            # 规则运行入口
        │   ├── diff.py               # Git diff 分析
        │   └── project.py            # 项目扫描
        └── rules/
            ├── __init__.py
            ├── base.py               # Rule / PythonAstRule / TextRule 基类
            ├── registry.py           # 规则注册表
            ├── security.py           # SEC001-SEC012
            ├── complexity.py         # COMPLEX001-005（含认知复杂度算法）
            └── style.py              # STYLE001-004
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
