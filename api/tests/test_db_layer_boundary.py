"""Architecture guard for the runtime database-access boundary."""

import ast
import re
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
NON_RUNTIME_TOP_LEVEL = {"alembic", "db", "tests"}
NON_RUNTIME_FILES = {Path("conftest.py")}
INTERNAL_TOOLING_PREFIXES = {("services", "admin_utils")}
DATABASE_IMPORT_ROOTS = {"asyncpg", "psycopg", "psycopg2", "sqlalchemy"}
DIRECT_DATABASE_CALLS = {"async_session", "execute_raw_query"}
SQL_LITERAL = re.compile(
    r"^\s*(?:"
    r"SELECT\b.+\bFROM\b|"
    r"INSERT\s+INTO\b|"
    r"UPDATE\s+[A-Za-z_]\w*\s+SET\b|"
    r"DELETE\s+FROM\b|"
    r"WITH\s+[A-Za-z_]\w*\s+AS\s*\("
    r")",
    re.IGNORECASE | re.DOTALL,
)


def _runtime_python_files():
    for path in API_ROOT.rglob("*.py"):
        relative = path.relative_to(API_ROOT)
        if relative in NON_RUNTIME_FILES:
            continue
        if relative.parts[0] in NON_RUNTIME_TOP_LEVEL:
            continue
        if any(
            relative.parts[: len(prefix)] == prefix
            for prefix in INTERNAL_TOOLING_PREFIXES
        ):
            continue
        yield path, relative


def test_runtime_database_access_is_confined_to_db_clients():
    violations: list[str] = []

    for path, relative in _runtime_python_files():
        tree = ast.parse(path.read_text(), filename=str(relative))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".", 1)[0] in DATABASE_IMPORT_ROOTS:
                        violations.append(f"{relative}:{node.lineno}: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if (
                    node.module
                    and node.module.split(".", 1)[0] in DATABASE_IMPORT_ROOTS
                ):
                    violations.append(f"{relative}:{node.lineno}: {node.module}")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in DIRECT_DATABASE_CALLS
            ):
                violations.append(
                    f"{relative}:{node.lineno}: direct {node.func.attr}() access"
                )
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and SQL_LITERAL.search(node.value)
            ):
                violations.append(f"{relative}:{node.lineno}: raw SQL literal")

    assert violations == [], (
        "Runtime database imports, SQL, and direct session/query access must live in "
        f"api/db/: {violations}"
    )
