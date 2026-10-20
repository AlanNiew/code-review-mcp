# Changelog

本项目所有重要变更都记录在此文件中。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [0.3.1] - 2026-10-20

### 修复：PyPI 发布链路

0.3.0 是首次通过 GitHub Actions 发布，踩了三个发布期的坑；本版本只修发布与打包，
**运行时代码与 0.3.0 完全一致**。

#### 🐛 修复

- **Trusted Publishing（OIDC）**：发布工作流不再依赖 `PYPI_API_TOKEN` secret，
  改为 PyPI Trusted Publishing；仓库 secret 可以删除。
- **GitHub Release 403**：工作流只声明了 `id-token: write`，创建 Release 还需
  `contents: write`，已补齐（此前 release 步骤在 PyPI 上传成功后失败）。
- **400 Bad Request**：`Topic :: Security :: Static Analysis` 不是合法的 PyPI
  classifier，上传被拒；改为合法的 `Topic :: Security`。
- 依赖上限 `mcp>=1.9.0,<2` 随 0.3.0 一并发布，干净安装不再解析到 mcp 2.x。

#### 🔧 内部

- 版本号 0.3.0 → 0.3.1。

## [0.3.0] - 2026-10-20

### 定位升级：从"lint 报告生成器"到"变更审查证据引擎"

v0.2 的核心问题是：审查的是"文件"而不是"变更"，输出的是"评分"而不是"判断"。
全文件扫描会把历史问题一起倒出来，评分又把风格小问题和一个越权漏洞算成同一量级。
v0.3 重新划分职责：**工具负责给事实，Skill 负责给判断**。

#### ✨ 新增

- **`review_changes` MCP 工具（旗舰）**：一次调用产出结构化的变更审查证据包
  - 三种范围：`working`（未提交 + 未跟踪）、`staged`、`base`（`git merge-base` 三点差异）
  - 只保留**新增行**上的规则命中，历史问题不混入本次审查
  - 文件级信号：`tests_missing`（改了 10+ 行却没有对应测试）、`protection_bypass`
    （`# type: ignore` / `noqa` / `@ts-ignore` / `pytest.mark.skip` / `--no-verify` 等）、
    `large_file_change`、`deleted_test_assertions`
  - 变更级信号：新增依赖、数据库迁移、CI/构建、API 契约（OpenAPI/proto/graphql）、
    测试变更、密钥、配置变更
  - Python 函数级复杂度回归：同一函数变更前后的圈复杂度与长度对比
  - `focus` 风险焦点 + `limits` 跳过/截断说明 + 文件 `priority`（high/medium/low）；
    `protection_bypass`、`tests_missing`、`large_file_change` 都会把文件提到 `medium`，
    保证它们进入深审队列。
  - 全程有界：每文件 ≤10 条命中、整体 ≤80 条、焦点 ≤12 条
- **`analyzers/change.py`**：unified diff 解析（added/deleted/renamed/binary/多 hunk）、
  git 目标采集、函数级复杂度对比、审查报文组装
- **`tests/test_change_analysis.py`**：用临时 git 仓库端到端覆盖三种 scope 与关键信号
- **`tests/test_engine_hardening.py`**：阈值接线与算法修复的回归测试

#### 🐛 修复

- **配置阈值之前根本没有生效**：`Config.thresholds` 从未传入规则引擎，
  COMPLEX001-005 与 STYLE001 全部硬编码。现在 `RuleContext` 携带 config，
  6 个阈值真正可配置（默认值不变）。
- **`elif` 被当成嵌套**：认知复杂度把 `elif` 计为嵌套层级，导致链式条件被严重高估；
  现在按 SonarSource 规则处理（结构 +1，无嵌套惩罚）。
- **`compute_max_nesting_depth` 重复递归**：同一子节点被递归两次，层级计算错误且指数级变慢。
- **`SEC005` 漏检**：硬编码密钥只查 `ast.Assign`，漏掉注解赋值（`token: str = "..."`）
  与关键字参数（`connect(password="...")`）。
- **规则输出未去重/未排序**：同一行可能重复报同一规则，且顺序不稳定，影响报告可读性与
  按行过滤的正确性。
- **豁免解析双实现**：`utils` 与 `config` 各有一套 `codereview: ignore` 解析，行为可能漂移；
  统一为单一实现。
- **`SEC012` 死代码**：计算后未使用的 `sensitive_targets` 已清理。
- **依赖上限缺失导致干净安装崩溃**：`mcp` 依赖此前只写 `>=1.9.0`，而 mcp 2.x 移除了
  `mcp.server.fastmcp`（改名 `MCPServer`，API 也有变更），CI 从零安装拿到 2.x 后
  在收集阶段直接 `ModuleNotFoundError`。现锁定 `mcp>=1.9.0,<2`；迁移到 2.x 另行处理。

#### 🎯 信号精度（dogfood 自审修复）

用 `review_changes` 审查本仓库自己的改动时发现并修复了 4 处噪声：

- `protection_bypass` 此前会把文档（README/CHANGELOG 里提到 `noqa` 等字样）、
  测试文件（`pytest.skip`）甚至正则定义里出现的关键字都标成"绕过检查"；
  现在只对生产源码的**真实注释行**判定（Python 用 `tokenize` 区分注释与字符串），
  测试文件不计。
- `tests_missing` 此前按文件名逐文件匹配，`server.py`、`utils.py` 这类"测试文件名对不上"的
  文件会集中误报；现在本次变更集里只要动过测试，就不再逐文件报"缺测试"。
- 变更 packet 不再对测试文件输出 `COMPLEX*` 命中（测试天然长、分支多，属噪声）；
  `analyze_file` 单文件工具保持通用行为不变。
- `function_deltas` 的"长度回归"增加下限：只有多出 ≥10 行且超过原长度 1.5 倍才计入，
  避免 8→13 行这种小函数轻微变长也上报。
- `focus` 原来把只有长度增长的函数也写成 `complexity 1→1`；现在按真实指标输出
  `complexity a→b` 或 `length a→b`，并让复杂度真增长的排前面。

#### 🧪 QA 复审修复

对 `change.py` 做了一轮独立的只读复审（逐条 probe 三种 scope 的行号、rename/中文路径/
binary/多 hunk、错误路径与预算），修掉复审确认的问题：

- **目录误诊**：`directory` 不存在或指向文件时，`_git` 的 `OSError` 被归类成"git 环境不可用"，
  提示用户去装 git。现在先校验目录，返回明确的"目录不存在或不是目录"；"git 启动失败"
  也不再当作环境缺失。
- **子目录静默扩大范围**：`directory` 传仓库子目录时此前会审整仓。现在记录 `scope_prefix`，
  只审该子目录下的变更，并在 `limits.notes` 说明范围。
- **`secret_added` 语义过宽**：任意安全类 error（如新增 `eval`）都被标成"新增密钥"。
  现在只有 SEC005（硬编码密钥）叫 `secret_added`，其余归为 `security_finding`。
- **同行同规则去重过度**：去重键从 `(rule, line)` 改为 `(rule, line, message)`，
  一行里两个不同的硬编码密钥不再只报一个，完全相同的重复输出仍会去重。
- **`changed_test_files` 收集过早**：被 `ignore_paths` 忽略或被判定为 binary 的测试文件
  也会抑制全仓的 `tests_missing`；现在只统计真正进入审查的测试文件。
- **`protection_bypass` 超限静默**：超过 3 个文件后此前直接不再标注；现在补一条 note
  并置 `truncated`。
- **`ast.Match` 未计入嵌套**：嵌套深度规则与认知复杂度现在都统计 `match`
  （与上文"含 match"口径一致）。
- **中文路径解析（`core.quotepath=true` 默认值）**：`_unescape_git_path` 此前只处理
  `\"` 与 `\\`，八进制字节 `\346\226\207` 无法还原；现在按 C 转义解码后整体 UTF-8 还原。
- **BOM 文件误报语法错误**：带 UTF-8 BOM 的合法 Python 文件会被补 `SYNTAX001`；
  现在解析前去掉行首 BOM（不影响行号）。
- **测试的 git 调用不再依赖宿主机配置**：`tests/test_tools.py` 统一用
  `commit.gpgsign=false`、`core.autocrlf=false`、显式 `user.*` 与 `check=True`，
  避免宿主 git 配置或静默失败影响结果。

#### 🔄 变更

- **`review_diff` 标记弃用**，保留为兼容 shim（行为与 v0.2.x 一致）；
  新代码请用 `review_changes`。
- **规则输出口径**：按 `(line, rule_id, message)` 去重完全相同的命中，
  保留同一行同一规则的多条不同消息；最终按 `(line, rule_id)` 稳定排序。
  依赖 issue 条数的下游统计（评分）会随之变化，属接口行为变化。
- **行级豁免语义收敛**（以 `config.py` 为唯一实现）：
  `# codereview: ignore=`（等号后无 ID）现在表示豁免该行全部规则；
  小写规则 ID（`ignore=sec001`）现在正确解析为指定规则，此前会退化为"豁免全部"。
- **嵌套深度口径**：`compute_max_nesting_depth` 只统计控制流嵌套
  （if/for/while/try/except/match，含 AsyncFor），`with` / `async with` 不再计入；
  无嵌套函数由 1 改为 0。仅影响 COMPLEX005 的触发边界，默认阈值 4 无回归。
- **认知复杂度计数**：`elif` 只加 1 且不计嵌套惩罚；`BoolOp` 只在 `visit_BoolOp`
  计一次（消除 `if` 处的重复计数）；三元表达式计入嵌套层级。
- **Skill 重写为审查协议**：不再要求固定报告模板，而是定义
  定范围 → 读证据 → 读上下文 → 维度检查 → **验证门槛** → 定级 → 报告 + 覆盖声明的流程，
  并给出阻塞/重要/次要定级标准与反模式清单。
- README 重写：说明"工具给事实、审查者给判断"的边界，补充 packet 结构与使用方式。

#### 🔧 内部

- `analyzers/__init__.py` 导出变更分析 API：`review_change_set`、`collect_review_target`、
  `parse_unified_diff`、`python_function_deltas`。
- **`change.py` 自身通过本工具的复杂度检查（dogfood）**：初版 `review_change_set` 293 行、
  圈复杂度 26，`parse_unified_diff` 126 行、圈复杂度 33，会被自己的 COMPLEX002/003 报出。
  重构为 `_DiffCursor`（diff 逐行状态机）、`_ReviewSession` / `_FileReview`（审查上下文）、
  `_ReviewAccumulator`（预算与统计）、`_review_changed_file` / `_finalize_packet` 等小函数后，
  用 `review_changes` 审本仓库改动，findings 为 0。
  唯一保留的参数个数提示（`review_change_set` 的 6 个参数都是公开入口要用的预算旋钮）
  用本工具自己的行级豁免 `# codereview: ignore=COMPLEX004` 标注，顺带验证了豁免链路。
- **QA 复审后的第二轮拆分**（自审再次归零）：`_unescape_git_path` 拆出
  `_read_escape` / `_decode_c_escapes`；`collect_review_target` 拆出
  `_resolve_repo_root` / `_collect_parts`；`review_change_set` 拆出
  `_load_target_diffs` / `_reviewed_test_paths` / `_run_file_reviews`。
  最终 `analyze_file_content(change.py)` issues=0（score 100 / A 级）。
- 版本号 0.2.0 → 0.3.0。

---

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
