"""测试套件根目录的初始化文件与共享 fixtures。"""

import sys
from pathlib import Path

import pytest

# 把 src 目录加入 path，便于直接 pytest 运行（无需 pip install -e .）
_SRC = Path(__file__).parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


@pytest.fixture
def make_context():
    """构造 RuleContext 的辅助 fixture。

    用法：
        ctx = make_context("x = 1", "test.py")
    """
    from code_review_mcp.analyzers.context import build_context

    def _create(source: str, file_path: str = "test.py", language=None):
        return build_context(source, file_path, language)

    return _create


@pytest.fixture
def run_rules(make_context):
    """便捷运行所有规则并返回 issue 列表的 fixture。"""
    from code_review_mcp.rules import registry

    def _run(source: str, file_path: str = "test.py"):
        ctx = make_context(source, file_path)
        return registry.run_all(ctx)

    return _run


@pytest.fixture
def fresh_registry():
    """提供一个临时干净 registry 的 fixture（用于隔离测试）。

    注意：因为 registry 是全局单例，本 fixture 只返回当前 registry；
    如需清空请用 monkeypatch 直接替换。
    """
    from code_review_mcp.rules import registry

    return registry
