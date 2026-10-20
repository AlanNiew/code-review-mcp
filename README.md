# ai-code-review-mcp

面向 AI 编码助手的**变更驱动代码审查引擎**：MCP 服务器 + 配套 Skill。

它不是又一个 linter 报告生成器。`review_changes` 把一个 git 变更（分支、PR、未提交改动）变成一份**审查证据包**——改了哪些文件、哪些位置风险最高、新增行上有哪些规则命中、复杂度是否退化、依赖/迁移/接口/测试是否被动过——再由 skill 里的审查协议把它变成**按影响排序、可落到 `path:line`、并声明覆盖范围的审查意见**。

> **PyPI**: https://pypi.org/project/ai-code-review-mcp/
> **GitHub**: https://github.com/AlanNiew/code-review-mcp

## 设计原则

1. **审变更，不审整个文件。** 全文件扫描会把你早已存在的历史问题一起倒出来，淹没真正的新缺陷。
2. **工具只给事实，不给结论。** 工具输出 findings / signals / deltas；是否构成问题、什么级别，由审查者判断并验证。
3. **输出有界。** 默认每文件最多 10 条命中、整体最多 80 条、焦点最多 12 条，并用 `limits` 说明哪些被跳过或截断。
4. **静态命中是线索。** 每条 finding 带 confidence，误报（尤其测试夹具里的假密钥）由审查者排除。
5. **诚实优先。** 报告必须写明"没审什么"，而不是假装全覆盖。
6. **用自己审查自己。** v0.3.0 的改动本身由 `review_changes` 审过：初版旗舰文件被自己的复杂度规则命中，按发现拆函数后 findings 归零；唯一保留的参数个数提示用行级豁免标注并写明理由。

## 特性

- **`review_changes`（旗舰）** — 采集 `working` / `staged` / `base...HEAD` 三种范围的变更，产出结构化证据包
- **只报新增行** — 命中过滤到 diff 新增行，历史问题不混入本次审查
- **文件级风险信号** — `tests_missing`（改了 10+ 行却没有对应测试）、`protection_bypass`（`# type: ignore` / `noqa` / `@ts-ignore` / `--no-verify` 等）、`large_file_change`、`deleted_test_assertions`
- **变更级信号** — 新增依赖、数据库迁移、CI/构建、API 契约（OpenAPI/proto/graphql）、测试变更、密钥（SEC005）/ 其他安全类 error、配置变更
- **复杂度回归检测** — Python 变更文件的函数级圈复杂度与长度 before/after 对比
- **审查焦点** — 把 error 级命中、高风险信号、复杂度回归聚合成 `focus`，优先深审
- **22 条规则** — 12 条安全规则（SEC001-012）+ 5 条复杂度（COMPLEX001-005）+ 5 条风格（STYLE001-005）
- **阈值真正可配置** — `.code-review.yml` 里的 `thresholds` 现在会真正传入规则引擎
- **行级豁免** — `# codereview: ignore` / `# codereview: ignore=SEC005`
- **多语言** — Python AST 深度分析；JS/TS/Java/Go/Rust 等 12 种语言的通用检查
- **配套 Skill** — 一套可执行的审查协议：定范围 → 读证据 → 读上下文 → 维度检查 → 验证门槛 → 定级报告
- **零外部依赖** — 仅 `mcp`；YAML 配置可选装 PyYAML

## 安装

```bash
pip install ai-code-review-mcp
```

要求 Python 3.10+。

### OpenCode（推荐）

在项目根目录的 `opencode.json` 中启用 MCP：

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

安装配套 Skill（把审查协议交给模型）：

```bash
# 项目级
mkdir -p .opencode/skills/code-review
curl -o .opencode/skills/code-review/SKILL.md https://raw.githubusercontent.com/AlanNiew/code-review-mcp/main/skill/SKILL.md

# 全局级
mkdir -p ~/.config/opencode/skills/code-review
curl -o ~/.config/opencode/skills/code-review/SKILL.md https://raw.githubusercontent.com/AlanNiew/code-review-mcp/main/skill/SKILL.md
```

Windows PowerShell：

```powershell
New-Item -ItemType Directory -Path ".opencode\skills\code-review" -Force
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/AlanNiew/code-review-mcp/main/skill/SKILL.md" -OutFile ".opencode\skills\code-review\SKILL.md"
```

装好后直接说：

```
审查一下我这个分支相对 main 的改动
审查已暂存的变更
帮我看看这个 PR
```

### Claude Desktop / Cursor

```json
{
  "mcpServers": {
    "code-review-mcp": {
      "command": "ai-code-review-mcp"
    }
  }
}
```

Cursor：Settings → MCP → 新增 command 类型服务器，命令 `ai-code-review-mcp`。

### uvx（免安装）

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

### 从源码

```bash
git clone https://github.com/AlanNiew/code-review-mcp.git
cd code-review-mcp
pip install -e ".[dev]"
```

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

## 工具

### 1. `review_changes` — 变更审查证据包（v0.3.0）

```python
review_changes(scope="working", base=None, directory=".")
```

`directory` 可以是仓库内任意子目录：此时只审该子目录下的变更（会自动取仓库根做 diff，再按子目录过滤），不会静默扩大到整仓。目录不存在或不是目录时返回明确的 `error`，而不是误报"git 不可用"。

| scope | 含义 |
|---|---|
| `working`（默认） | 未提交的全部改动：未暂存 + 已暂存 + 未跟踪文件（含源码与配置） |
| `staged` | 只审已暂存（`git add` 过的） |
| `base` | 审 `base...HEAD`，用 `git merge-base` 取三点差异；`base` 必填，如 `"main"` |

返回结构（省略部分字段）：

```json
{
  "scope": "base",
  "range": "main...HEAD",
  "base_commit": "a1b2c3d",
  "head_commit": "e4f5g6h",
  "stats": {"files": 5, "additions": 120, "deletions": 30,
            "by_status": {"modified": 3, "added": 1, "deleted": 1},
            "languages": {"python": 4, "typescript": 1}},
  "files": [
    {
      "path": "src/auth/session.py",
      "status": "modified",
      "additions": 20, "deletions": 4, "language": "python",
      "priority": "high",
      "signals": ["tests_missing", "protection_bypass"],
      "findings": [
        {"rule": "SEC005", "severity": "error", "category": "security",
         "line": 42, "confidence": "medium",
         "message": "可能的硬编码密钥: token"}
      ],
      "function_deltas": [
        {"name": "Session.verify", "line": 12,
         "complexity_before": 6, "complexity_after": 11,
         "length_before": 30, "length_after": 55}
      ]
    }
  ],
  "signals": [
    {"kind": "dependency_added", "path": "requirements.txt",
     "line": 7, "detail": "pyyaml==6.0", "priority": "high"}
  ],
  "focus": [
    "src/auth/session.py:42 — SEC005 hardcoded-secret (error)",
    "src/auth/session.py:12 — complexity 6→11 in Session.verify"
  ],
  "limits": {"skipped": [{"path": "logo.png", "reason": "binary"}],
             "truncated": false, "notes": []}
}
```

`priority` 规则：有 error 命中 / 有 high 级 signal / 复杂度增长 ≥5 为 `high`；有 warning、或 `tests_missing` / `protection_bypass` / `large_file_change`、或有复杂度/长度增长为 `medium`；其余 `low`。

`signals[].kind` 取值：`dependency_added`、`database_schema`、`ci_or_build`、`api_contract`、`test_change`、`secret_added`（只有硬编码密钥规则 SEC005）、`security_finding`（其他安全类 error，如 eval / pickle / shell 注入）、`large_change`、`config_change`。

文件级 `signals` 的判定：

- `tests_missing`：源码文件新增 ≥10 行、仓库里找不到对应测试，**且**本次变更集没有改动任何会被审查的测试文件（已经动过测试就不再逐文件断言"缺测试"；被 `.gitignore` / `ignore_paths` 排除的测试文件不算）。
- `protection_bypass`：新增行出现 `# type: ignore` / `noqa` / `@ts-ignore` / `pytest.mark.skip` / `--no-verify` 等审查规避标记。只对生产源码判定：测试文件不算（测试里 skip / type: ignore 是常规操作），文档和配置里提到这些字样也不算；Python 只认真正的注释行，字符串字面量里出现不算。最多标注 3 个文件，超出部分在 `limits.notes` 说明并置 `truncated`。
- `large_file_change`：单文件新增 >400 行。
- `deleted_test_assertions`：删除的是测试文件，或删掉的行里含断言。

变更审查只保留**新增行**上的规则命中；测试文件不报 `COMPLEX*` 复杂度命中——测试天然长、分支多，这类提示在审查里只是噪声。`function_deltas` 里的"长度回归"要求函数至少多出 10 行、且超过原长度 1.5 倍，避免小函数轻微变长就上报。

**怎么用**：先看 `focus` 和 `priority=high` 的文件，再按 `signals` 决定是否要读迁移/依赖/接口定义，最后回源码逐条验证。工具结果不是结论。

### 2. `analyze_file` — 单文件分析

```python
analyze_file(file_path="src/main.py")
```

返回语言、代码/注释/空白行数、规则命中列表、多维评分（overall / security / maintainability，A-F）。用于单文件深挖，或非 git 仓库场景。

### 3. `check_project` — 项目概况

```python
check_project(directory=".")
```

扫描项目内所有支持的语言文件，汇总文件数、行数、语言分布、问题最多的前 10 个文件、整体评分。用于不熟悉的仓库摸底，**不是审查结论**。

### 4. `list_rules` — 规则清单

返回所有规则的 ID、名称、描述、类别、默认严重级别、适用语言。用于向用户解释某条规则命中的含义。

### 5. `review_diff` — 已弃用

保留为兼容 shim，行为与 v0.2.x 一致（staged + unstaged 的 DIFF001-004 检查）。新代码请用 `review_changes`。

## 规则参考

### 安全规则（SEC001-SEC012，Python AST）

借鉴 [bandit](https://github.com/PyCQA/bandit) 的 AST 节点分发模式。

| 规则 ID | 名称 | 严重级别 | 检测目标 |
|---------|------|---------|---------|
| SEC001 | eval-exec | 🔴 error | `eval()` / `exec()` 任意代码执行 |
| SEC002 | shell-injection | 🔴 error | `subprocess(shell=True)` / `os.system` 命令注入 |
| SEC003 | unsafe-deserialize | 🔴 error | `pickle.loads` / `yaml.load` 不安全反序列化 |
| SEC004 | weak-hash | 🟡 warning | `hashlib.md5` / `sha1` 弱哈希 |
| SEC005 | hardcoded-secret | 🔴 error | 硬编码密码 / API Key（含注解赋值与关键字参数）|
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

### 风格规则（STYLE001-005）

| 规则 ID | 名称 | 严重级别 | 检测目标 |
|---------|------|---------|---------|
| STYLE001 | line-too-long | 🟡 warning | 单行 > 120 字符 |
| STYLE002 | todo-comment | 🔵 info | 注释中的 TODO/FIXME/HACK/XXX |
| STYLE003 | trailing-whitespace | 🔵 info | 行末空白字符 |
| STYLE004 | debug-print | 🔵 info | Python `print` 调试残留 |
| STYLE005 | console-log | 🔵 info | JS/TS `console.log` 调试残留 |

## 配置

在项目根目录创建 `.code-review.yml`、`.code-review.yaml` 或 `.code-review.json`。
（YAML 需要 `pip install ai-code-review-mcp[yaml]`）

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
  "ignore_paths": ["tests/*", "vendor/", "migrations/", "*_pb2.py"],
  "severity_overrides": { "SEC009": "warning" }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `rules.<category>.enabled` | bool | 关闭整类规则 |
| `thresholds.<key>` | int | 自定义阈值，**v0.3.0 起真正生效** |
| `ignore_paths` | list | 路径忽略（fnmatch glob + 目录前缀）；`review_changes` 同样遵守 |
| `severity_overrides` | dict | 覆盖单条规则的默认严重级别 |

可用阈值键：`max_function_length`、`max_cyclomatic_complexity`、`max_cognitive_complexity`、`max_param_count`、`max_line_length`、`max_nesting_depth`。

查找顺序：`CODE_REVIEW_CONFIG` 环境变量 → 从当前目录向上查找 `.code-review.*`（遇 `.git` 停）→ 内置默认。

## 行级豁免

```python
# 豁免当前行所有规则
token = "hardcoded-for-dev-only-1234567890"  # codereview: ignore

# 只豁免指定规则
password = "test1234"  # codereview: ignore=SEC005
```

标记大小写不敏感，可出现在行内任意位置。

## 评分说明

`analyze_file` / `check_project` 仍返回 A-F 评分，用于快速了解文件/项目健康度：

| 维度 | 包含类别 |
|------|---------|
| overall | 全部 issue |
| security | `security` |
| maintainability | `complexity` / `style` / `debug_code` / `duplication` |

扣分权重：error 10 分、warning 3 分、info 0.5 分，基础 100，扣完为止；A ≥ 90、B ≥ 80、C ≥ 70、D ≥ 60、其余 F。

**评分是健康度参考，不是审查结论。** 审查结论来自 `review_changes` 证据 + skill 里的验证与定级流程。

## 审查协议（skill/SKILL.md）

配套 Skill 不是报告模板，而是一套审查协议：

1. **定范围** — 明确是 working / staged / 哪个 base，把范围写进报告
2. **读证据** — 按 `focus` → high 文件 → `signals` → `limits` 的顺序读 packet
3. **读上下文** — 必须打开源码看改动周围的逻辑、调用方、类型、测试
4. **维度检查** — 意图完整性 / 正确性 / 错误路径 / 安全 / 并发资源 / 数据兼容 / 性能 / 测试 / 可观测性 / 依赖 / 风格
5. **验证门槛** — 每条候选必须说清触发条件、影响、为什么别处拦不住、是否本次引入；答不全就降级为"待确认"或丢弃
6. **定级报告** — 阻塞 / 重要 / 次要，默认最多 8 条；附"已检查未发现问题"与"覆盖范围"

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

## 项目结构

```
code-review-mcp/
├── pyproject.toml
├── README.md
├── CHANGELOG.md
├── .github/workflows/           # CI（多版本矩阵 + ruff + pytest）与 PyPI 发布
├── skill/
│   └── SKILL.md                 # 审查协议（OpenCode Skill）
├── tests/
│   ├── conftest.py
│   ├── test_security_rules.py   # SEC001-SEC012 正反例
│   ├── test_complexity.py       # 认知/圈复杂度算法 + 规则
│   ├── test_style_rules.py
│   ├── test_config.py           # 配置加载 + 行级豁免
│   ├── test_scoring.py
│   ├── test_engine_hardening.py # 阈值接线 + 算法修复回归
│   ├── test_change_analysis.py  # 变更证据引擎（临时 git 仓库）
│   └── test_tools.py            # MCP 工具端到端
└── src/code_review_mcp/
    ├── server.py                # MCP 入口（5 个工具）
    ├── models.py                # Issue / Severity / Category / Confidence
    ├── config.py                # 配置加载 + 行级豁免
    ├── scoring.py               # 多维评分
    ├── utils.py
    ├── analyzers/
    │   ├── context.py           # 规则运行入口
    │   ├── change.py            # 变更证据引擎（v0.3.0）
    │   ├── diff.py              # 旧版 diff 检查（review_diff 兼容）
    │   └── project.py           # 项目扫描
    └── rules/
        ├── base.py
        ├── registry.py
        ├── security.py
        ├── complexity.py
        └── style.py
```

## 常见问题

### Q: 启动时报错 "command not found: ai-code-review-mcp"

确认 Python 的 Scripts 目录在 PATH 中：

```bash
pip show ai-code-review-mcp
where ai-code-review-mcp    # Windows
which ai-code-review-mcp    # macOS/Linux
```

### Q: MCP 工具没有出现在 AI 助手中

1. 确认配置文件路径正确
2. 确认 `ai-code-review-mcp` 能在终端直接运行
3. 重启 AI 助手应用

### Q: `review_changes` 报不是 git 仓库 / 没有 commit

`review_changes` 依赖 git。非 git 仓库请对单文件用 `analyze_file`；空仓库（还没有 commit）只支持 `working`/`staged`，`base` 模式需要至少一个 commit。

### Q: 只想用 MCP，不装 Skill 可以吗？

可以。Skill 是可选的：MCP 工具本身就能返回完整的变更证据包；Skill 的价值是让模型按审查协议验证、定级并如实报告覆盖范围。

### Q: 工具报的命中一定是问题吗？

不一定。规则是启发式的，每条命中都带 confidence。测试夹具里的假密钥、确实需要 `shell=True` 的场景都可能命中。skill 的验证门槛要求逐条回源码确认。

## License

MIT © AlanNiew
