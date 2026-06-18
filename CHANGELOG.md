# Changelog

本项目所有重要变更都记录在此文件中。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [0.2.0] - 2026-06-18

### 重磅升级：从"基础规则集"升级为"模块化规则引擎"

本次版本是一次重要架构升级，借鉴了 [bandit](https://github.com/PyCQA/bandit)、
[SonarSource](https://www.sonarsource.com/)、[fossil-mcp](https://github.com/yfedoseev/fossil-mcp)、
[open-code-review](https://github.com/raye-deng/open-code-review) 等优秀开源项目的设计。

#### ✨ 新增

- **12 条安全规则（SEC001-SEC012）**：借鉴 bandit 的 AST 节点分发模式
  - SEC001: `eval` / `exec` 任意代码执行
  - SEC002: `subprocess(..., shell=True)` 命令注入
  - SEC003: `pickle.loads` / `yaml.load` 不安全反序列化
  - SEC004: `hashlib.md5` / `sha1` 弱哈希算法
  - SEC005: 硬编码密码 / API Key
  - SEC006: `requests(..., verify=False)` 关闭 SSL 校验
  - SEC007: bare except / 吞异常
  - SEC008: SQL 字符串拼接（注入风险）
  - SEC009: 生产代码中的 `assert`（在 `-O` 模式失效）
  - SEC010: Flask `debug=True`
  - SEC011: JWT 关闭签名校验
  - SEC012: 安全场景使用 `random` 而非 `secrets`

- **认知复杂度算法（COMPLEX003）**：实现 SonarSource 的
  [Cognitive Complexity](https://www.sonarsource.com/docs/CognitiveComplexity.pdf) 白皮书算法，
  比圈复杂度更准确反映"人脑理解难度"（嵌套加权）

- **5 条复杂度规则（COMPLEX001-005）**：函数过长 / 圈复杂度 / 认知复杂度 / 参数过多 / 嵌套过深

- **多维质量评分**：从单一分数升级为
  - 总分（overall）
  - 安全分（security）
  - 可维护性分（maintainability）
  - 每个维度独立 A-F 评级，便于区分"代码漂亮但有 SQL 注入"和"安全没问题但代码烂"

- **配置文件支持**：自动查找 `.code-review.yml` / `.code-review.yaml` / `.code-review.json`
  - 规则开关：`rules: { security: { enabled: false } }`
  - 自定义阈值：`thresholds: { max_function_length: 80 }`
  - 路径忽略：`ignore_paths: [tests/*, vendor/]`
  - 严重级别覆盖：`severity_overrides: { SEC009: warning }`

- **行级豁免**：`# codereview: ignore`（全部豁免）或 `# codereview: ignore=SEC001,SEC005`（指定规则）

- **`list_rules` MCP 工具**：列出所有已注册规则的元信息，便于 AI 选择性调用

- **完整测试套件**：100 个测试用例覆盖每条规则的正反例 + 复杂度算法 + 配置加载 + 端到端

- **CI/CD 流水线**：GitHub Actions 多版本矩阵（Python 3.10-3.13 × Ubuntu/Windows/macOS）
  + ruff lint + 自动发布 PyPI

#### 🔄 变更

- **架构重构**：单文件 `server.py` 拆分为模块化结构
  ```
  src/code_review_mcp/
  ├── server.py              # MCP 入口（瘦身）
  ├── models.py              # Issue + Severity + Category 枚举
  ├── config.py              # 配置加载 + 行级豁免
  ├── scoring.py             # 多维评分
  ├── utils.py               # 通用工具
  ├── analyzers/             # 分析器层
  │   ├── context.py         # 规则运行入口
  │   ├── diff.py            # Git diff 分析
  │   └── project.py         # 项目扫描
  └── rules/                 # 规则引擎
      ├── base.py            # Rule / PythonAstRule / TextRule 基类
      ├── registry.py        # 规则注册表
      ├── security.py        # SEC001-SEC012
      ├── complexity.py      # COMPLEX001-005
      └── style.py           # STYLE001-004
  ```

- **行长默认值从 200 收紧为 120**（符合业界主流 PEP 8 + Black 风格）

- **新增 dev 依赖组**：`pip install -e ".[dev]"` 一键装好 pytest + ruff

- **新增可选 yaml 依赖组**：`pip install -e ".[yaml]"` 或 `pip install ai-code-review-mcp[yaml]`

#### 🛠 工程化

- 加入 [ruff](https://docs.astral.sh/ruff/) 作为 lint + format 工具（替代 black + isort + flake8）
- 加入 pytest 配置（自动 src 入 path + 严格 markers）
- 100% 类型注解（Python 3.10+ 风格的 `X | None`）

---

## [0.1.0] - 2026-04-16

### 首次发布

- MCP 服务器，提供 3 个工具：`analyze_file` / `review_diff` / `check_project`
- Python AST 复杂度分析（函数长度、分支复杂度、参数数量）
- 通用质量检查（行长、TODO、末尾空白）
- Git diff 审查（调试代码、密钥、TODO）
- 质量评分（A/B/C/D 等级）
- 12 种语言支持
- 配套 OpenCode Skill
