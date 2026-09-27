"""宏曦标书 - 未定义全局名静态检查.

**为什么需要它**：2026-09-27 上线后「确认并继续」直接 500，根因是
`chapters.py` 里 `_materialise_chapters` 引用了一个已经被提升/改名掉的
`_build_meta`。645 条测试全绿也没拦住 —— 因为那条路径在**评标办法补全**
分支里，要跑到它得有 DB 会话 + 带缺口的评标办法，没有任何单测走到。

这类 `NameError` 是纯静态可判的：函数体里的 `LOAD_GLOBAL`/`LOAD_NAME`
如果既不在模块全局、也不在内置里，运行时必炸。不需要装 linter，
Python 自己的 `dis` 就够了。

只检查**本模块定义的**函数（`__globals__ is vars(module)`）—— 导入进来的
函数其全局命名空间属于别的模块，拿本模块的命名空间去比会满屏误报。
"""

from __future__ import annotations

import builtins
import dis
import importlib
import pkgutil
import types

import app


def _walk_code(code: types.CodeType):
    yield code
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            yield from _walk_code(const)


def _undefined_globals(mod: types.ModuleType) -> list[str]:
    """返回该模块里引用到的、既非模块全局也非内置的名字."""
    module_globals = vars(mod)
    known = set(module_globals) | set(dir(builtins))
    bad: list[str] = []

    for name, obj in list(module_globals.items()):
        if not isinstance(obj, types.FunctionType):
            continue
        if getattr(obj, "__globals__", None) is not module_globals:
            continue  # 不是本模块定义的
        for code in _walk_code(obj.__code__):
            for instr in dis.get_instructions(code):
                if instr.opname not in ("LOAD_GLOBAL", "LOAD_NAME"):
                    continue
                target = instr.argval
                if isinstance(target, str) and target not in known:
                    bad.append(f"{name}() -> {target}")
    return sorted(set(bad))


def test_no_undefined_globals_anywhere_in_app():
    """整个 app 包不得有引用未定义全局名的函数.

    这条测试的价值完全取决于它对真 bug 的敏感度：把 `_materialise_chapters` 里
    任意一个模块级名字写错，它必须立刻变红。
    """
    problems: list[str] = []
    for info in pkgutil.walk_packages(app.__path__, "app."):
        try:
            mod = importlib.import_module(info.name)
        except Exception as exc:  # noqa: BLE001 - 导入不了本身就是问题
            problems.append(f"{info.name}: 导入失败 {type(exc).__name__}: {exc}")
            continue
        for hit in _undefined_globals(mod):
            problems.append(f"{info.name}: {hit}")

    assert problems == [], "以下函数引用了未定义的全局名（运行时必 NameError）：\n" + "\n".join(
        f"  - {p}" for p in problems
    )
