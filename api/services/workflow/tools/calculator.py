import ast
import operator
from typing import Any, Dict

# Mapping from AST operator types to Python operator functions
_op_map = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

# Allowlisted math functions (only builtins safe for arithmetic)
_math_funcs = frozenset({"abs", "round", "min", "max", "sum", "pow", "float", "int"})


def _eval_node(node: ast.AST) -> Any:
    """Recursively evaluate an AST node without using eval()."""
    # Numbers (int or float)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("Only numeric constants are allowed")
    if isinstance(node, ast.Num):  # Python < 3.8 compatibility
        return node.n  # noqa: N816

    # Binary operations: +, -, *, /, **, %
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        return _op_map[type(node.op)](left, right)

    # Unary operations: -x, +x
    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand)
        return _op_map[type(node.op)](operand)

    # Allowlisted function calls: abs(), round(), min(), max(), sum(), pow(), float(), int()
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only plain function calls are allowed")
        func_name = node.func.id
        if func_name not in _math_funcs:
            raise ValueError(f"Unsupported function: {func_name}")
        args = [_eval_node(arg) for arg in node.args]
        fn = _math_funcs
        # Map function name to actual callable
        _func_map = {"abs": abs, "round": round, "min": min, "max": max, "sum": sum, "pow": pow, "float": float, "int": int}
        return _func_map[func_name](*args)

    # Everything else is disallowed
    raise ValueError(f"Unsupported AST node type: {type(node).__name__}")


def safe_calculator(expr: str) -> float:
    """
    Parse and evaluate arithmetic expressions using pure AST interpretation.

    Supports: +, -, *, /, **, % and parentheses.

    This implementation avoids eval()/exec() entirely by walking the AST and
    evaluating nodes with a hand-written interpreter.

    Raises:
        ValueError: If the expression is invalid or contains disallowed constructs.
        ZeroDivisionError: If division by zero is attempted.
    """
    if not expr or not expr.strip():
        raise ValueError("Expression cannot be empty")

    if len(expr) > 500:
        raise ValueError("Expression too long")

    try:
        node = ast.parse(expr, mode="eval")
    except SyntaxError:
        raise ValueError("Invalid expression syntax")

    # Whitelist: only allow constant numbers, binary/unary ops, and allowlisted funcs
    for n in ast.walk(node):
        if not isinstance(n, tuple(_op_map.keys)) and not isinstance(n, ast.Constant) and not isinstance(n, ast.Call) and not isinstance(n, (ast.Expression, ast.Load)):
            raise ValueError(f"Unsupported AST node: {type(n).__name__}")

    try:
        result = _eval_node(node.body)
        if not isinstance(result, (int, float)):
            raise ValueError(f"Expression must evaluate to a number, got {type(result).__name__}")
        return float(result)
    except ZeroDivisionError:
        raise ValueError("Division by zero is not allowed")


def get_calculator_tools() -> list[Dict[str, Any]]:
    """Get calculator tool definitions for LLM function calling."""
    return [
        {
            "type": "function",
            "function": {
                "name": "safe_calculator",
                "description": "Perform simple arithmetic calculations",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "expression": {
                            "type": "string",
                            "description": "Arithmetic expression to evaluate (supports +, -, *, /, **, %, and parentheses). Example: 2000 + 5000",
                        }
                    },
                    "required": ["expression"],
                },
            },
        }
    ]