"""Loguru calls must not pass stdlib logging keywords.

Loguru treats extra keyword arguments as ``str.format`` arguments, so
``logger.error(f"... {e}", exc_info=True)`` formats the already-interpolated
message. An exception whose text contains braces (a pydantic or workflow
validation error listing dicts) then raises ``KeyError`` from inside the
``except`` block, replacing the real error and skipping the handler's cleanup.
Use ``logger.opt(exception=True)`` or ``logger.exception`` for tracebacks.
"""

import ast
from pathlib import Path

from loguru import logger

API_ROOT = Path(__file__).resolve().parents[1]
STDLIB_ONLY_KEYWORDS = {"exc_info", "stack_info", "stacklevel", "extra"}
LOGGING_METHODS = {
    "trace",
    "debug",
    "info",
    "success",
    "warning",
    "error",
    "critical",
    "exception",
    "log",
}


def _loguru_names(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Names bound to ``loguru.logger`` and to the ``loguru`` module."""
    logger_names, module_names = {"logger"}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "loguru":
            logger_names |= {
                a.asname or a.name for a in node.names if a.name == "logger"
            }
        elif isinstance(node, ast.Import):
            module_names |= {
                a.asname or a.name for a in node.names if a.name == "loguru"
            }
    return logger_names, module_names


def _is_loguru_logger(
    node: ast.expr, logger_names: set[str], module_names: set[str]
) -> bool:
    """Whether ``node`` is the logger, through any ``.opt(...)``/``.bind(...)`` chain."""
    while isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        node = node.func.value
    if isinstance(node, ast.Name):
        return node.id in logger_names
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "logger"
        and isinstance(node.value, ast.Name)
        and node.value.id in module_names
    )


def _loguru_calls_with_stdlib_keywords(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(), filename=str(path))
    logger_names, module_names = _loguru_names(tree)
    found = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in LOGGING_METHODS
            and _is_loguru_logger(node.func.value, logger_names, module_names)
        ):
            continue
        for keyword in node.keywords:
            if keyword.arg in STDLIB_ONLY_KEYWORDS:
                found.append((node.lineno, keyword.arg))
    return found


def test_no_loguru_call_passes_stdlib_logging_keywords():
    offenders = [
        f"{path.relative_to(API_ROOT)}:{line} {keyword}="
        for path in sorted(API_ROOT.rglob("*.py"))
        if "tests" not in path.parts
        for line, keyword in _loguru_calls_with_stdlib_keywords(path)
    ]
    assert offenders == []


def test_guard_sees_qualified_aliased_and_chained_calls(tmp_path):
    source = tmp_path / "sample.py"
    source.write_text(
        "import loguru\n"
        "import loguru as lg\n"
        "from loguru import logger as log\n"
        "loguru.logger.error('a', exc_info=True)\n"
        "lg.logger.warning('b', stack_info=True)\n"
        "log.opt(depth=1).bind(run_id=1).error('c', exc_info=True)\n"
        "log.opt(exception=True).error('fine')\n"
    )
    assert _loguru_calls_with_stdlib_keywords(source) == [
        (4, "exc_info"),
        (5, "stack_info"),
        (6, "exc_info"),
    ]


def test_exception_logging_keeps_braces_and_traceback():
    records = []
    sink_id = logger.add(records.append, level="ERROR", format="{message}")
    text = "[{'kind': 'node', 'message': 'Start Call cannot have incoming edges'}]"
    try:
        try:
            raise ValueError(text)
        except ValueError as e:
            logger.opt(exception=True).error(f"[run 1] Error in telnyx pipeline: {e}")
    finally:
        logger.remove(sink_id)

    [message] = records
    record = message.record
    assert record["message"] == f"[run 1] Error in telnyx pipeline: {text}"
    assert record["exception"] is not None
    assert record["exception"].type is ValueError
    assert record["exception"].traceback is not None
