"""rules 子包：所有代码审查规则。

子模块：
- base：Rule / PythonAstRule / TextRule 基类
- registry：规则注册表（全局单例）
- security：安全规则（SEC001-SEC012）
- complexity：复杂度规则（COMPLEX001-005）
- style：风格规则（STYLE001-004）
"""

from .registry import register_all_default_rules, registry

__all__ = ["registry", "register_all_default_rules"]
