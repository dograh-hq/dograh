import ast
from typing import Any, Dict


def safe_calculator(expr: str) -> float:
    """
    Parse arithmetic expressions using ast and support + - * / ** and parentheses.
    """
    allowed_nodes = {
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Pow,
        ast.USub,
        ast.UAdd,
        ast.Constant,
        ast.Load,
        ast.Mod,
    }

    # Parse the expression into an AST and validate against whitelist.
    node = ast.parse(expr, mode="eval")
    if not all(isinstance(n, tuple(allowed_nodes)) for n in ast.walk(node)):
        raise ValueError("Unsupported expression")

    # Whitelist validation: compile the AST node to bytecode and evaluate
    # only when all nodes are in the allowed set. AST parsing prevents code
    # injection (no arbitrary Python execution), while the compile+eval
    # boundary ensures only the resulting bytecode runs.
    compiled = compile(node, "<safe_calculator>", mode="eval")
    return eval(compiled)  # nosec: B307 - node is already validated via AST whitelist


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
