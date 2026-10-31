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


def _loguru_calls_with_stdlib_keywords(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        target = node.func.value
        # logger.error(...) and logger.opt(...).error(...)
        if isinstance(target, ast.Call) and isinstance(target.func, ast.Attribute):
            target = target.func.value
        if isinstance(target, ast.Name) and target.id == "logger":
            for keyword in node.keywords:
                if keyword.arg in STDLIB_ONLY_KEYWORDS:
                    found.append(f"{path.relative_to(API_ROOT)}:{node.lineno} {keyword.arg}=")
    return found


def test_no_loguru_call_passes_stdlib_logging_keywords():
    offenders = [
        hit
        for path in sorted(API_ROOT.rglob("*.py"))
        if "tests" not in path.parts
        for hit in _loguru_calls_with_stdlib_keywords(path)
    ]
    assert offenders == []


def test_exception_logging_survives_braces_in_the_message():
    error = ValueError("[{'kind': 'node', 'message': 'Start Call cannot have incoming edges'}]")
    try:
        raise error
    except ValueError as e:
        logger.opt(exception=True).error(f"[run 1] Error in telnyx pipeline: {e}")
