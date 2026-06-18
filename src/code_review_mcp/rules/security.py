"""安全规则包：借鉴 bandit 的 AST 节点分发模式，覆盖 Python 常见安全反模式。

规则编号 SEC001-SEC012，每条规则都是 PythonAstRule 的子类。
所有规则只针对 Python，零外部依赖。

参考：
- bandit 规则索引（B101/B102/B301-303/B324/B501/B506/B602 等）
- OWASP Top 10
- SonarSource Security Hotspot

设计原则：
- 每条规则只关心它要检测的节点类型，互不干扰
- 用 confidence 区分"明确命中"和"模式匹配可能误报"
- 每条 issue 都附上 suggestion（一句话修复建议）
"""

from __future__ import annotations

import ast
from typing import ClassVar

from ..models import Category, Confidence, Severity
from .base import (
    PythonAstRule,
    RuleContext,
    get_call_name,
    get_keyword_value,
    is_const_true,
)

# ==================== 辅助 ====================

# 内置危险函数名集合（不依赖 import 别名）
_EVAL_EXEC_FUNCS = {"eval", "exec", "compile", "__import__"}

# 反序列化相关函数全限定名
_UNSAFE_DESERIALIZE = {
    "pickle.loads",
    "pickle.load",
    "pickle.Unpickler",
    "cPickle.loads",
    "cPickle.load",
    "marshal.loads",
    "marshal.load",
    "shelve.open",
    "_pickle.loads",
    "_pickle.load",
}

# 弱哈希算法
_WEAK_HASHES = {
    "hashlib.md5",
    "hashlib.sha1",
    "hashlib.new",  # 需要 check 第一个参数
}

# 弱哈希算法名（用于 hashlib.new("md5") 的情况）
_WEAK_HASH_NAMES = {"md5", "sha1", "md4", "md2"}


def _is_test_file(file_path: str) -> bool:
    """判断是否是测试文件（测试文件里 assert / mock 是合理的）。

    匹配以下任意一种：
    - 路径中包含 /tests/ 或 /test/ 段（也匹配开头）
    - 文件名以 test.py / _test.py / conftest.py 结尾
    """
    if not file_path:
        return False
    name = file_path.replace("\\", "/").lower()
    # 切分为路径段，看是否含 tests / test 段
    parts = name.split("/")
    if "tests" in parts or "test" in parts:
        return True
    return name.endswith("test.py") or name.endswith("_test.py") or name.endswith("conftest.py")


# ==================== SEC001: eval / exec ====================


class EvalExecRule(PythonAstRule):
    """SEC001: 禁止使用 eval / exec / compile 进行动态代码执行。

    风险：任意代码执行漏洞（用户输入直接进 eval/exec 时尤其严重）。
    借鉴 bandit B102。
    """

    RULE_ID: ClassVar[str] = "SEC001"
    NAME: ClassVar[str] = "eval-exec"
    DESCRIPTION: ClassVar[str] = "禁止使用 eval/exec/compile 执行动态代码"
    CATEGORY: ClassVar[Category] = Category.SECURITY
    DEFAULT_SEVERITY: ClassVar[Severity] = Severity.ERROR

    def visit(self, ctx: RuleContext) -> list:
        issues = []
        for node in ast.walk(ctx.tree):
            if not isinstance(node, ast.Call):
                continue
            func_name = get_call_name(node)
            short_name = func_name.rsplit(".", 1)[-1]
            if short_name in _EVAL_EXEC_FUNCS and "." not in func_name:
                issues.append(
                    self.make_issue(
                        line=node.lineno,
                        message=f"使用 `{func_name}()` 存在任意代码执行风险",
                        confidence=Confidence.HIGH,
                        content=f"{func_name}(...)",
                        suggestion="避免动态代码执行；如必须用，对输入做严格白名单校验",
                    )
                )
        return issues


# ==================== SEC002: subprocess shell=True ====================


class ShellInjectionRule(PythonAstRule):
    """SEC002: subprocess 系列 + shell=True 存在命令注入风险。

    借鉴 bandit B602/B603。
    """

    RULE_ID: ClassVar[str] = "SEC002"
    NAME: ClassVar[str] = "shell-injection"
    DESCRIPTION: ClassVar[str] = "subprocess 调用不应使用 shell=True"
    CATEGORY: ClassVar[Category] = Category.SECURITY
    DEFAULT_SEVERITY: ClassVar[Severity] = Severity.ERROR

    _SUBPROCESS_FUNCS = {
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.run",
        "subprocess.check_call",
        "subprocess.check_output",
        "subprocess.getoutput",
        "subprocess.getstatusoutput",
        "os.system",
        "os.popen",
        "os.popen2",
        "os.popen3",
        "os.popen4",
        "commands.getoutput",
    }

    def visit(self, ctx: RuleContext) -> list:
        issues = []
        for node in ast.walk(ctx.tree):
            if not isinstance(node, ast.Call):
                continue
            func_name = get_call_name(node)
            if func_name not in self._SUBPROCESS_FUNCS:
                continue

            # os.system / os.popen 永远是 shell 调用
            if func_name.startswith("os.") or func_name.startswith("commands."):
                issues.append(
                    self.make_issue(
                        line=node.lineno,
                        message=f"`{func_name}()` 使用 shell 执行，存在命令注入风险",
                        confidence=Confidence.HIGH,
                        content=f"{func_name}(...)",
                        suggestion="使用 subprocess.run(..., shell=False) 并传列表参数",
                    )
                )
                continue

            # subprocess 系列：检查 shell=True
            shell_value = get_keyword_value(node, "shell")
            if shell_value is not None and is_const_true(shell_value):
                issues.append(
                    self.make_issue(
                        line=node.lineno,
                        message=f"`{func_name}(shell=True)` 存在命令注入风险",
                        confidence=Confidence.HIGH,
                        content=f"{func_name}(shell=True)",
                        suggestion="使用 shell=False 并以列表形式传参；如必须用 shell，对输入做 shlex.quote",
                    )
                )
        return issues


# ==================== SEC003: 反序列化 ====================


class UnsafeDeserializeRule(PythonAstRule):
    """SEC003: pickle / marshal / yaml.load 等不安全反序列化。

    风险：构造的 pickle/marshal 数据可在反序列化时执行任意代码。
    借鉴 bandit B301-303/B506。
    """

    RULE_ID: ClassVar[str] = "SEC003"
    NAME: ClassVar[str] = "unsafe-deserialize"
    DESCRIPTION: ClassVar[str] = "不安全的反序列化（pickle/marshal/yaml.load）"
    CATEGORY: ClassVar[Category] = Category.SECURITY
    DEFAULT_SEVERITY: ClassVar[Severity] = Severity.ERROR

    def visit(self, ctx: RuleContext) -> list:
        issues = []
        for node in ast.walk(ctx.tree):
            if not isinstance(node, ast.Call):
                continue
            func_name = get_call_name(node)

            # pickle / marshal
            if func_name in _UNSAFE_DESERIALIZE:
                issues.append(
                    self.make_issue(
                        line=node.lineno,
                        message=f"`{func_name}()` 反序列化不可信数据可导致任意代码执行",
                        confidence=Confidence.HIGH,
                        content=f"{func_name}(...)",
                        suggestion="使用 JSON 等安全格式；如必须 pickle，对来源做严格校验",
                    )
                )

            # yaml.load / yaml.unsafe_load（应使用 safe_load）
            elif func_name in ("yaml.load", "yaml.unsafe_load", "yaml.full_load"):
                # yaml.load 第二个参数是 Loader，如果是 SafeLoader 则放过
                loader_arg = None
                if len(node.args) >= 2:
                    loader_arg = node.args[1]
                loader_kw = get_keyword_value(node, "Loader")
                loader_node = loader_kw or loader_arg

                loader_name = ""
                if loader_node is not None:
                    loader_name = (
                        get_call_name(ast.Call(func=loader_node, args=[], keywords=[]))
                        if isinstance(loader_node, ast.Call)
                        else (loader_node.id if isinstance(loader_node, ast.Name) else "")
                    )

                if "SafeLoader" not in loader_name:
                    issues.append(
                        self.make_issue(
                            line=node.lineno,
                            message=f"`{func_name}()` 应改用 yaml.safe_load()",
                            confidence=Confidence.HIGH,
                            content=f"{func_name}(...)",
                            suggestion="使用 yaml.safe_load() 替代",
                        )
                    )
        return issues


# ==================== SEC004: 弱哈希算法 ====================


class WeakHashRule(PythonAstRule):
    """SEC004: md5 / sha1 等弱哈希算法存在碰撞和预映攻击风险。

    借鉴 bandit B324。
    """

    RULE_ID: ClassVar[str] = "SEC004"
    NAME: ClassVar[str] = "weak-hash"
    DESCRIPTION: ClassVar[str] = "避免使用 md5 / sha1 等弱哈希"
    CATEGORY: ClassVar[Category] = Category.SECURITY
    DEFAULT_SEVERITY: ClassVar[Severity] = Severity.WARNING

    def visit(self, ctx: RuleContext) -> list:
        issues = []
        for node in ast.walk(ctx.tree):
            if not isinstance(node, ast.Call):
                continue
            func_name = get_call_name(node)

            if func_name in ("hashlib.md5", "hashlib.sha1"):
                # hashlib.md5(..., usedforsecurity=False) 是合法用法（Python 3.9+）
                used = get_keyword_value(node, "usedforsecurity")
                if used is not None and isinstance(used, ast.Constant) and used.value is False:
                    continue
                issues.append(
                    self.make_issue(
                        line=node.lineno,
                        message=f"`{func_name}()` 是弱哈希算法，不适用于安全场景",
                        confidence=Confidence.HIGH,
                        content=f"{func_name}(...)",
                        suggestion="使用 sha256 / sha3_256 等强哈希；如仅用于缓存，加 usedforsecurity=False",
                    )
                )
            elif func_name == "hashlib.new" and node.args:
                first_arg = node.args[0]
                if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                    algo = first_arg.value.lower().strip()
                    if algo in _WEAK_HASH_NAMES:
                        issues.append(
                            self.make_issue(
                                line=node.lineno,
                                message=f'hashlib.new("{algo}") 使用了弱哈希算法',
                                confidence=Confidence.HIGH,
                                content=f'hashlib.new("{algo}")',
                                suggestion=f"使用 sha256 / sha3_256 等强哈希替代 {algo}",
                            )
                        )
        return issues


# ==================== SEC005: 硬编码密码/密钥 ====================


class HardcodedSecretRule(PythonAstRule):
    """SEC005: 检测硬编码的密码/密钥（变量名 + 字符串字面量）。

    借鉴 bandit B105/B106/B107，但做了改良：
    - 变量名包含 password/secret/token/api_key/passwd 等敏感词
    - 赋值的是非空字符串字面量
    - 排除空字符串、明显占位符（<...>、xxx、changeme）
    """

    RULE_ID: ClassVar[str] = "SEC005"
    NAME: ClassVar[str] = "hardcoded-secret"
    DESCRIPTION: ClassVar[str] = "禁止硬编码密码/密钥"
    CATEGORY: ClassVar[Category] = Category.SECURITY
    DEFAULT_SEVERITY: ClassVar[Severity] = Severity.ERROR

    _SECRET_KEYWORDS = (
        "password",
        "passwd",
        "pwd",
        "secret",
        "api_key",
        "apikey",
        "access_key",
        "secret_key",
        "private_key",
        "token",
        "auth_token",
        "access_token",
        "refresh_token",
        "client_secret",
    )

    _PLACEHOLDERS = (
        "<",
        ">",
        "${",
        "$(",
        "%s",
        "xxx",
        "changeme",
        "your_",
        "example",
        "placeholder",
        "todo",
        "...",
    )

    def visit(self, ctx: RuleContext) -> list:
        issues = []
        for node in ast.walk(ctx.tree):
            if not isinstance(node, ast.Assign):
                continue
            # 只看赋值字符串字面量
            if not node.value or not isinstance(node.value, ast.Constant):
                continue
            if not isinstance(node.value.value, str):
                continue

            secret_value = node.value.value
            # 排除空字符串和占位符
            if not secret_value.strip():
                continue
            if any(p in secret_value.lower() for p in self._PLACEHOLDERS):
                continue
            # 太短的也排除（可能是变量名占位）
            if len(secret_value) < 4:
                continue

            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                var_lower = target.id.lower()
                if any(kw in var_lower for kw in self._SECRET_KEYWORDS):
                    issues.append(
                        self.make_issue(
                            line=node.lineno,
                            message=f"变量 `{target.id}` 可能硬编码了密码/密钥",
                            confidence=Confidence.MEDIUM,
                            name=target.id,
                            suggestion="从环境变量或密钥管理服务读取（os.environ / secret manager）",
                        )
                    )
                    break
        return issues


# ==================== SEC006: SSL 证书校验关闭 ====================


class SslVerifyFalseRule(PythonAstRule):
    """SEC006: requests/httpx 等 verify=False 关闭 SSL 证书校验。

    借鉴 bandit B501。
    """

    RULE_ID: ClassVar[str] = "SEC006"
    NAME: ClassVar[str] = "ssl-verify-false"
    DESCRIPTION: ClassVar[str] = "禁止关闭 SSL 证书校验"
    CATEGORY: ClassVar[Category] = Category.SECURITY
    DEFAULT_SEVERITY: ClassVar[Severity] = Severity.ERROR

    _HTTP_FUNCS = {
        "requests.get",
        "requests.post",
        "requests.put",
        "requests.delete",
        "requests.patch",
        "requests.head",
        "requests.options",
        "requests.request",
        "httpx.get",
        "httpx.post",
        "httpx.request",
        "httpx.Client",
        "httpx.AsyncClient",
    }

    def visit(self, ctx: RuleContext) -> list:
        issues = []
        for node in ast.walk(ctx.tree):
            if not isinstance(node, ast.Call):
                continue
            func_name = get_call_name(node)
            if func_name not in self._HTTP_FUNCS:
                continue
            verify_value = get_keyword_value(node, "verify")
            if (
                verify_value is not None
                and isinstance(verify_value, ast.Constant)
                and verify_value.value is False
            ):
                issues.append(
                    self.make_issue(
                        line=node.lineno,
                        message=f"`{func_name}(verify=False)` 关闭了 SSL 证书校验",
                        confidence=Confidence.HIGH,
                        content=f"{func_name}(verify=False)",
                        suggestion="移除 verify=False；测试场景请用 CA bundle 而非关闭校验",
                    )
                )
        return issues


# ==================== SEC007: bare except / 吞异常 ====================


class BareExceptRule(PythonAstRule):
    """SEC007: bare except (无类型) 和空 except 块（pass/continue）会吞掉所有异常。

    借鉴 bandit B110/B112 + Sonar python:S5754。
    """

    RULE_ID: ClassVar[str] = "SEC007"
    NAME: ClassVar[str] = "bare-except"
    DESCRIPTION: ClassVar[str] = "禁止 bare except 和吞掉异常"
    CATEGORY: ClassVar[Category] = Category.ERROR_HANDLING
    DEFAULT_SEVERITY: ClassVar[Severity] = Severity.WARNING

    def visit(self, ctx: RuleContext) -> list:
        issues = []
        for node in ast.walk(ctx.tree):
            if not isinstance(node, ast.ExceptHandler):
                continue

            # bare except（没有类型）
            if node.type is None:
                issues.append(
                    self.make_issue(
                        line=node.lineno,
                        message="bare `except:` 会捕获所有异常（包括 KeyboardInterrupt/SystemExit）",
                        confidence=Confidence.HIGH,
                        suggestion="指定具体异常类型，如 `except ValueError:` 或至少 `except Exception:`",
                    )
                )

            # except: pass / except: continue（吞异常）
            body = node.body or []
            if len(body) == 1:
                stmt = body[0]
                if isinstance(stmt, (ast.Pass, ast.Continue)):
                    # pass in except Exception as e 仍然是吞异常
                    issues.append(
                        self.make_issue(
                            line=node.lineno,
                            message=f"except 块只有 `{type(stmt).__name__.lower()}`，异常被吞掉",
                            confidence=Confidence.HIGH,
                            suggestion="至少记录日志（logger.exception）或重新抛出",
                        )
                    )
        return issues


# ==================== SEC008: SQL 字符串拼接 ====================


class SqlInjectionRule(PythonAstRule):
    """SEC008: SQL 语句字符串拼接（% / + / f-string）有注入风险。

    借鉴 bandit B608。
    """

    RULE_ID: ClassVar[str] = "SEC008"
    NAME: ClassVar[str] = "sql-injection"
    DESCRIPTION: ClassVar[str] = "SQL 语句不应使用字符串拼接"
    CATEGORY: ClassVar[Category] = Category.SECURITY
    DEFAULT_SEVERITY: ClassVar[Severity] = Severity.WARNING

    _SQL_KEYWORDS = ("select ", "insert ", "update ", "delete ", "drop ", "create ", "alter ")
    _EXEC_FUNCS = {"execute", "executemany", "executescript"}

    def visit(self, ctx: RuleContext) -> list:
        issues = []
        for node in ast.walk(ctx.tree):
            # 形如 cursor.execute("..." % args) / cursor.execute("..." + x)
            if isinstance(node, ast.Call):
                func_name = get_call_name(node)
                short_name = func_name.rsplit(".", 1)[-1]
                if short_name in self._EXEC_FUNCS and node.args:
                    first = node.args[0]
                    if isinstance(first, (ast.BinOp, ast.JoinedStr)):  # noqa: SIM102
                        if self._looks_like_sql(first):
                            issues.append(
                                self.make_issue(
                                    line=node.lineno,
                                    message=f"`{short_name}()` 的 SQL 使用字符串拼接，存在注入风险",
                                    confidence=Confidence.MEDIUM,
                                    suggestion="使用参数化查询，如 cursor.execute('WHERE id=?', (id,))",
                                )
                            )
        return issues

    def _looks_like_sql(self, node) -> bool:
        """判断 BinOp/JoinedStr 是否包含 SQL 关键字。"""
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                lower = sub.value.lower()
                if any(kw in lower for kw in self._SQL_KEYWORDS):
                    return True
        return False


# ==================== SEC009: 生产代码中的 assert ====================


class AssertInProductionRule(PythonAstRule):
    """SEC009: 生产代码中的 assert 在 python -O 模式下会被剔除，不能用于业务校验。

    借鉴 bandit B101。测试文件豁免。
    """

    RULE_ID: ClassVar[str] = "SEC009"
    NAME: ClassVar[str] = "assert-in-production"
    DESCRIPTION: ClassVar[str] = "生产代码不应依赖 assert 做校验"
    CATEGORY: ClassVar[Category] = Category.SECURITY
    DEFAULT_SEVERITY: ClassVar[Severity] = Severity.INFO

    def visit(self, ctx: RuleContext) -> list:
        # 测试文件豁免
        if _is_test_file(ctx.file_path):
            return []

        issues = []
        for node in ast.walk(ctx.tree):
            if isinstance(node, ast.Assert):
                issues.append(
                    self.make_issue(
                        line=node.lineno,
                        message="assert 在 -O 模式下会被跳过，不能用于业务校验",
                        confidence=Confidence.MEDIUM,
                        suggestion="使用 if not cond: raise ValueError(...) 做校验",
                    )
                )
        return issues


# ==================== SEC010: Flask debug=True ====================


class FlaskDebugRule(PythonAstRule):
    """SEC010: Flask app.run(debug=True) 在生产环境会暴露 Werkzeug 调试器（可执行任意代码）。

    借鉴 bandit B201。
    """

    RULE_ID: ClassVar[str] = "SEC010"
    NAME: ClassVar[str] = "flask-debug"
    DESCRIPTION: ClassVar[str] = "Flask 不应在生产启用 debug 模式"
    CATEGORY: ClassVar[Category] = Category.SECURITY
    DEFAULT_SEVERITY: ClassVar[Severity] = Severity.ERROR

    def visit(self, ctx: RuleContext) -> list:
        issues = []
        for node in ast.walk(ctx.tree):
            if not isinstance(node, ast.Call):
                continue
            func_name = get_call_name(node)
            if func_name.split(".")[-1] != "run":
                continue

            debug_value = get_keyword_value(node, "debug")
            if debug_value is not None and is_const_true(debug_value):
                issues.append(
                    self.make_issue(
                        line=node.lineno,
                        message=f"`{func_name}(debug=True)` 在生产环境会暴露调试器",
                        confidence=Confidence.HIGH,
                        content=f"{func_name}(debug=True)",
                        suggestion="debug 应通过环境变量控制，生产环境必须为 False",
                    )
                )
        return issues


# ==================== SEC011: JWT 不验签 ====================


class JwtNoVerifyRule(PythonAstRule):
    """SEC011: jwt.decode(..., options={"verify_signature": False}) 关闭签名校验。

    这是 JWT 库的常见误用，攻击者可伪造任意 token。
    """

    RULE_ID: ClassVar[str] = "SEC011"
    NAME: ClassVar[str] = "jwt-no-verify"
    DESCRIPTION: ClassVar[str] = "JWT 不应关闭签名校验"
    CATEGORY: ClassVar[Category] = Category.SECURITY
    DEFAULT_SEVERITY: ClassVar[Severity] = Severity.ERROR

    def visit(self, ctx: RuleContext) -> list:
        issues = []
        for node in ast.walk(ctx.tree):
            if not isinstance(node, ast.Call):
                continue
            func_name = get_call_name(node)
            if func_name != "jwt.decode":
                continue
            options = get_keyword_value(node, "options")
            if not isinstance(options, ast.Dict):
                continue
            for key, value in zip(options.keys, options.values, strict=False):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "verify_signature"
                    and isinstance(value, ast.Constant)
                    and value.value is False
                ):
                    issues.append(
                        self.make_issue(
                            line=node.lineno,
                            message="jwt.decode 关闭了签名校验，攻击者可伪造任意 token",
                            confidence=Confidence.HIGH,
                            suggestion="移除 options={'verify_signature': False}",
                        )
                    )
        return issues


# ==================== SEC012: 不安全的 random（密码学场景）====================


class WeakRandomRule(PythonAstRule):
    """SEC012: 密码学场景（变量名含 token/password/key/secret）使用 random 而非 secrets。

    random 模块的输出可预测，不适用于安全令牌生成。
    """

    RULE_ID: ClassVar[str] = "SEC012"
    NAME: ClassVar[str] = "weak-random"
    DESCRIPTION: ClassVar[str] = "安全场景应使用 secrets 而非 random"
    CATEGORY: ClassVar[Category] = Category.SECURITY
    DEFAULT_SEVERITY: ClassVar[Severity] = Severity.WARNING

    _RANDOM_FUNCS = {"random.random", "random.randint", "random.choice", "random.uniform"}

    def visit(self, ctx: RuleContext) -> list:
        issues = []
        # 先找出所有"赋值给敏感变量"的赋值
        sensitive_targets: set[int] = set()  # 节点 id
        for node in ast.walk(ctx.tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        name_lower = target.id.lower()
                        if any(
                            kw in name_lower
                            for kw in (
                                "token",
                                "password",
                                "secret",
                                "api_key",
                                "passwd",
                                "session",
                            )
                        ):
                            sensitive_targets.add(id(target))

        # 再找对敏感变量的赋值是 random 调用
        for node in ast.walk(ctx.tree):
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.Call):
                continue
            func_name = get_call_name(node.value)
            if func_name not in self._RANDOM_FUNCS and not func_name.startswith("random."):
                continue

            # 任意目标变量名包含敏感词都报
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                name_lower = target.id.lower()
                if any(
                    kw in name_lower
                    for kw in (
                        "token",
                        "password",
                        "secret",
                        "api_key",
                        "passwd",
                        "session",
                        "nonce",
                    )
                ):
                    issues.append(
                        self.make_issue(
                            line=node.lineno,
                            message=f"为 `{target.id}` 使用 `random` 模块生成的值可预测",
                            confidence=Confidence.MEDIUM,
                            name=target.id,
                            suggestion="使用 secrets 模块（secrets.token_urlsafe / secrets.token_hex）生成安全令牌",
                        )
                    )
                    break
        return issues


# ==================== 导出 ====================

SECURITY_RULES = [
    EvalExecRule,
    ShellInjectionRule,
    UnsafeDeserializeRule,
    WeakHashRule,
    HardcodedSecretRule,
    SslVerifyFalseRule,
    BareExceptRule,
    SqlInjectionRule,
    AssertInProductionRule,
    FlaskDebugRule,
    JwtNoVerifyRule,
    WeakRandomRule,
]
