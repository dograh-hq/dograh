"""Sandboxed Jinja2 template engine for Dograh.

Provides safe, sandboxed template execution with:
- Zero-crash fallback degradation for telephony
- SSTI / RCE defense via ImmutableSandboxedEnvironment
- Dynamic clock resolution (current_time, current_weekday, timezone support)
- Seamless backward compatibility with Dograh's legacy {{ var | default }} syntax
"""

import json
import re
import zoneinfo
from datetime import datetime
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

import jinja2
from jinja2.exceptions import SecurityError, TemplateError, TemplateSyntaxError
from jinja2.sandbox import ImmutableSandboxedEnvironment
from loguru import logger

_CURRENT_TIME_PREFIX = "current_time"
_CURRENT_WEEKDAY_PREFIX = "current_weekday"
_AVAILABLE_ZONES: frozenset[str] = frozenset(zoneinfo.available_timezones())

# Standard Jinja filters to not mistake for legacy default values
_STANDARD_JINJA_FILTERS = {
    "default",
    "d",
    "fallback",
    "lower",
    "upper",
    "title",
    "capitalize",
    "trim",
    "round",
    "int",
    "float",
    "string",
    "tojson",
    "abs",
    "attr",
    "batch",
    "first",
    "last",
    "length",
    "count",
    "list",
    "max",
    "min",
    "random",
    "reverse",
    "sort",
    "sum",
    "unique",
    "urlencode",
    "wordcount",
    "replace",
    "format",
    "safe",
}


class DograhContext(jinja2.runtime.Context):
    """Dynamic Jinja context resolving clock variables and missing keys."""

    def resolve_or_missing(self, key: str) -> Any:
        # Resolve built-in clock variables: current_time[_<TZ>]
        if key == _CURRENT_TIME_PREFIX:
            tz_name = self.parent.get("_default_tz") or "UTC"
            if tz_name not in _AVAILABLE_ZONES:
                tz_name = "UTC"
            return datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M:%S %Z")

        if key.startswith(f"{_CURRENT_TIME_PREFIX}_"):
            tz_name = (
                key[len(_CURRENT_TIME_PREFIX) + 1 :]
                .replace("__slash__", "/")
                .replace("__hyphen__", "-")
            )
            if tz_name not in _AVAILABLE_ZONES:
                return ""
            try:
                return datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M:%S %Z")
            except Exception:  # noqa: BLE001
                return ""

        # Resolve built-in clock variables: current_weekday[_<TZ>]
        if key == _CURRENT_WEEKDAY_PREFIX:
            tz_name = self.parent.get("_default_tz") or "UTC"
            if tz_name not in _AVAILABLE_ZONES:
                tz_name = "UTC"
            return datetime.now(ZoneInfo(tz_name)).strftime("%A")

        if key.startswith(f"{_CURRENT_WEEKDAY_PREFIX}_"):
            tz_name = (
                key[len(_CURRENT_WEEKDAY_PREFIX) + 1 :]
                .replace("__slash__", "/")
                .replace("__hyphen__", "-")
            )
            if tz_name not in _AVAILABLE_ZONES:
                return ""
            try:
                return datetime.now(ZoneInfo(tz_name)).strftime("%A")
            except Exception:  # noqa: BLE001
                return ""

        if "__hyphen__" in key:
            raw_key = key.replace("__hyphen__", "-")
            if raw_key in self.parent:
                return self.parent[raw_key]

            # If raw hyphenated key is not in context, check for arithmetic subtraction
            # between context variable and a numeric literal (e.g. {{ total-1 }} where context has {"total": 10})
            parts = raw_key.split("-")
            if len(parts) == 2:
                left_str, right_str = parts[0].strip(), parts[1].strip()
                left_val = self.parent.get(left_str)
                if isinstance(left_val, (int, float)):
                    if right_str.isdigit():
                        return left_val - int(right_str)
                    right_val = self.parent.get(right_str)
                    if isinstance(right_val, (int, float)):
                        return left_val - right_val

        return super().resolve_or_missing(key)


MAX_RENDERED_PROMPT_CHARS = 65_536  # 64 KB memory bomb ceiling


def _safe_mul(a: Any, b: Any) -> Any:
    """Multiplication operator that neutralizes memory bomb string/list amplification."""
    if isinstance(a, (str, bytes)) and isinstance(b, int):
        if b < 0:
            return a * b
        if len(a) * b > MAX_RENDERED_PROMPT_CHARS or b > 500:
            raise OverflowError("Multiplication factor/size exceeds allowable limit.")
    if isinstance(b, (str, bytes)) and isinstance(a, int):
        if a < 0:
            return a * b
        if len(b) * a > MAX_RENDERED_PROMPT_CHARS or a > 500:
            raise OverflowError("Multiplication factor/size exceeds allowable limit.")
    if isinstance(a, (list, tuple)) and isinstance(b, int):
        if b < 0:
            return a * b
        if b > 50 or len(a) * b > 50:
            raise OverflowError(
                "Collection multiplication factor/size exceeds allowable limit (50)."
            )
    if isinstance(b, (list, tuple)) and isinstance(a, int):
        if a < 0:
            return a * b
        if a > 50 or len(b) * a > 50:
            raise OverflowError(
                "Collection multiplication factor/size exceeds allowable limit (50)."
            )
    return a * b


def _disallow_pow(a: Any, b: Any) -> Any:
    """Block power operator to prevent CPU-hanging BigInt calculations."""
    raise SecurityError("Exponentiation ('**') is not permitted in prompt templates.")


class DograhSandboxedEnvironment(ImmutableSandboxedEnvironment):
    """Hardened sandboxed environment disallowing attribute/method traversal exploits and unbounded loops."""

    context_class = DograhContext
    MAX_RANGE = 50

    # Intercept binops to neutralize memory bombs (*) and CPU hangs (**)
    intercepted_binops: ClassVar[frozenset[str]] = frozenset(["*", "**"])
    default_binop_table: ClassVar[dict[str, Any]] = dict(
        ImmutableSandboxedEnvironment.default_binop_table
    )
    default_binop_table["*"] = _safe_mul
    default_binop_table["**"] = _disallow_pow

    def is_safe_attribute(self, obj: Any, attr: str, value: Any) -> bool:
        if attr.startswith("_") or attr in (
            "format",
            "format_map",
            "mro",
            "subclasses",
            "gi_frame",
            "cr_frame",
            "tb_frame",
        ):
            return False
        return super().is_safe_attribute(obj, attr, value)

    def is_safe_callable(self, obj: Any) -> bool:
        name = getattr(obj, "__name__", "")
        if name in ("eval", "exec", "compile", "open", "__import__"):
            return False
        return super().is_safe_callable(obj)

    def getattr(self, obj: Any, attribute: str) -> Any:
        if isinstance(attribute, str) and "__hyphen__" in attribute:
            raw_attr = attribute.replace("__hyphen__", "-")
            if isinstance(obj, dict):
                if raw_attr in obj:
                    return obj[raw_attr]
            elif hasattr(obj, raw_attr):
                return super().getattr(obj, raw_attr)
        return super().getattr(obj, attribute)

    def getitem(self, obj: Any, argument: Any) -> Any:
        if isinstance(argument, str) and "__hyphen__" in argument:
            raw_arg = argument.replace("__hyphen__", "-")
            if isinstance(obj, dict):
                if raw_arg in obj:
                    return obj[raw_arg]
        return super().getitem(obj, argument)

    def call(self, __context: Any, __obj: Any, *args: Any, **kwargs: Any) -> Any:
        # 1. Resolve loop range() without mutating context or leaking function repr into {{ range }}
        if (
            isinstance(__obj, jinja2.Undefined)
            and getattr(__obj, "_undefined_name", None) == "range"
        ) or (not callable(__obj) and __context.resolve_or_missing("range") is __obj):
            return _dograh_safe_range(*args, **kwargs)

        # 2. Defend against memory bomb string allocation methods like ljust(1000000000)
        bound_self = getattr(__obj, "__self__", None)
        if isinstance(bound_self, (str, bytes)):
            method_name = getattr(__obj, "__name__", "")
            if method_name in {"ljust", "rjust", "center", "zfill"}:
                width = kwargs.get("width", args[0] if args else 0)
                if isinstance(width, int) and abs(width) > MAX_RENDERED_PROMPT_CHARS:
                    raise OverflowError(
                        f"String method '{method_name}' width exceeds allowable limit."
                    )
            elif method_name == "expandtabs":
                tabsize = kwargs.get("tabsize", args[0] if args else 8)
                if isinstance(tabsize, int) and (
                    abs(tabsize) > MAX_RENDERED_PROMPT_CHARS
                    or len(bound_self) * tabsize > MAX_RENDERED_PROMPT_CHARS
                ):
                    raise OverflowError(
                        "String method 'expandtabs' exceeds allowable limit."
                    )
            elif method_name == "replace":
                old = args[0] if len(args) > 0 else kwargs.get("old", "")
                new = args[1] if len(args) > 1 else kwargs.get("new", "")
                count = args[2] if len(args) > 2 else kwargs.get("count", None)
                if isinstance(old, (str, bytes)) and isinstance(new, (str, bytes)):
                    occurrences = (
                        len(bound_self) + 1 if not old else bound_self.count(old)
                    )
                    if isinstance(count, int):
                        occurrences = min(occurrences, max(0, count))
                    projected_len = len(bound_self) + occurrences * (
                        len(new) - len(old)
                    )
                    if projected_len > MAX_RENDERED_PROMPT_CHARS:
                        raise OverflowError(
                            "String method 'replace' result exceeds allowable size."
                        )

        return super().call(__context, __obj, *args, **kwargs)


def _custom_fallback_filter(val: Any, default_val: Any = "") -> Any:
    """Fallback filter matching Dograh legacy behavior."""
    if val is None or val == "" or isinstance(val, jinja2.Undefined):
        return default_val
    return val


def _custom_default_filter(
    val: Any, default_val: Any = "", boolean: bool = False
) -> Any:
    """Default filter that matches Jinja2 semantics while treating empty strings as missing."""
    if val is None or isinstance(val, jinja2.Undefined):
        return default_val
    if boolean and not val:
        return default_val
    if val == "":
        return default_val
    return val


def _dograh_safe_range(*args: Any) -> Any:
    """Runtime range enforcement capping total iterations at MAX_RANGE (50)."""
    r = range(*args)
    if len(r) > 50:
        raise OverflowError("range() exceeds maximum allowable limit of 50 iterations.")
    return r


MAX_SERIALIZABLE_ITEMS = 5000
MAX_SERIALIZABLE_DEPTH = 10


class SafeJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder mapping jinja2.Undefined instances to empty strings without crashing."""

    def default(self, o: Any) -> Any:
        if isinstance(o, jinja2.Undefined):
            return ""
        return super().default(o)


def _check_serializable_bounds(obj: Any, max_chars: int) -> None:
    """Pre-scan object to bound scalar encoding and collection size before materializing chunks."""
    if isinstance(obj, (str, bytes)):
        if len(obj) > max_chars:
            raise OverflowError(
                f"JSON serialization exceeds maximum allowable size of {max_chars} characters."
            )
        return

    if not isinstance(obj, (dict, list, tuple, set)):
        return

    stack: list[tuple[Any, int]] = [(obj, 0)]
    items_visited = 0
    total_str_chars = 0

    while stack:
        current, depth = stack.pop()
        items_visited += 1
        if items_visited > MAX_SERIALIZABLE_ITEMS:
            raise OverflowError(
                f"Collection exceeds maximum allowable serializable item limit ({MAX_SERIALIZABLE_ITEMS})."
            )
        if depth > MAX_SERIALIZABLE_DEPTH:
            raise OverflowError(
                f"Collection nesting exceeds maximum allowable depth limit ({MAX_SERIALIZABLE_DEPTH})."
            )

        if isinstance(current, (str, bytes)):
            s_len = len(current)
            if s_len > max_chars or total_str_chars + s_len > max_chars:
                raise OverflowError(
                    f"JSON serialization exceeds maximum allowable size of {max_chars} characters."
                )
            total_str_chars += s_len
        elif isinstance(current, jinja2.Undefined):
            continue
        elif isinstance(current, dict):
            child_count = len(current) * 2
            if (
                child_count > MAX_SERIALIZABLE_ITEMS
                or items_visited + child_count > MAX_SERIALIZABLE_ITEMS
            ):
                raise OverflowError(
                    f"Collection exceeds maximum allowable serializable item limit ({MAX_SERIALIZABLE_ITEMS})."
                )
            for k, v in current.items():
                stack.append((v, depth + 1))
                stack.append((k, depth + 1))
        elif isinstance(current, (list, tuple, set)):
            child_count = len(current)
            if (
                child_count > MAX_SERIALIZABLE_ITEMS
                or items_visited + child_count > MAX_SERIALIZABLE_ITEMS
            ):
                raise OverflowError(
                    f"Collection exceeds maximum allowable serializable item limit ({MAX_SERIALIZABLE_ITEMS})."
                )
            for item in current:
                stack.append((item, depth + 1))


def _safe_json_dumps(obj: Any, max_chars: int = MAX_RENDERED_PROMPT_CHARS) -> str:
    """Serialize object to JSON with a strict length ceiling to prevent memory exhaustion."""
    _check_serializable_bounds(obj, max_chars)
    encoder = SafeJSONEncoder()
    chunks: list[str] = []
    total_len = 0
    for chunk in encoder.iterencode(obj):
        chunk_len = len(chunk)
        if total_len + chunk_len > max_chars:
            raise OverflowError(
                f"JSON serialization exceeds maximum allowable size of {max_chars} characters."
            )
        chunks.append(chunk)
        total_len += chunk_len
    return "".join(chunks)


def _dograh_finalize(obj: Any) -> Any:
    """Format None as empty string and collections as JSON matching legacy prompt semantics."""
    if obj is None or isinstance(obj, jinja2.Undefined):
        return ""
    if isinstance(obj, (dict, list, tuple)):
        return _safe_json_dumps(obj)
    return obj


class SafeTelephonyUndefined(jinja2.Undefined):
    """Safe undefined object that avoids crashing on comparisons in voice prompts.

    Treats undefined variables as falsy in comparisons (<, >, <=, >=, ==, !=),
    allowing conditions like `{% if loan_amount > 500000 %}` or nested access
    like `{% if customer.tier == 'vip' %}` or uncollected loops `{% for i in items %}`
    to evaluate safely without crashing or leaking raw Jinja tags into the call.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if self._undefined_exception and issubclass(
            self._undefined_exception, jinja2.exceptions.SecurityError
        ):
            raise self._undefined_exception(self._undefined_message)

    def __getattr__(self, name: str) -> Any:
        return self

    def __getitem__(self, name: Any) -> Any:
        return self

    def __iter__(self) -> Any:
        return iter(())

    def __int__(self) -> int:
        return 0

    def __float__(self) -> float:
        return 0.0

    def __gt__(self, other: Any) -> bool:
        return False

    def __lt__(self, other: Any) -> bool:
        return False

    def __ge__(self, other: Any) -> bool:
        return False

    def __le__(self, other: Any) -> bool:
        return False

    def __eq__(self, other: object) -> bool:
        return other is None or isinstance(other, jinja2.Undefined) or other == ""

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)


def _safe_replace_filter(s: Any, old: Any, new: Any, count: int | None = None) -> str:
    """Safe replace filter bounded by MAX_RENDERED_PROMPT_CHARS to prevent memory exhaustion."""
    s_str = str(s)
    old_str = str(old)
    new_str = str(new)
    occurrences = len(s_str) + 1 if not old_str else s_str.count(old_str)
    if count is not None and isinstance(count, int):
        occurrences = min(occurrences, max(0, count))
    projected_len = len(s_str) + occurrences * (len(new_str) - len(old_str))
    if projected_len > MAX_RENDERED_PROMPT_CHARS:
        raise OverflowError("String 'replace' filter result exceeds allowable size.")
    return s_str.replace(old_str, new_str, -1 if count is None else count)


def _safe_tojson_filter(val: Any) -> str:
    """Safe tojson filter that strictly enforces MAX_RENDERED_PROMPT_CHARS without exposing override kwargs."""
    if isinstance(val, jinja2.Undefined):
        return ""
    return _safe_json_dumps(val, MAX_RENDERED_PROMPT_CHARS)


def create_jinja_environment() -> DograhSandboxedEnvironment:
    """Instantiate and configure the Dograh Jinja2 sandboxed environment."""
    env = DograhSandboxedEnvironment(
        autoescape=False,
        undefined=jinja2.make_logging_undefined(base=SafeTelephonyUndefined),
        finalize=_dograh_finalize,
        cache_size=1000,
    )
    # Clear default Jinja reflection helpers (lipsum, cycler, joiner, namespace)
    env.globals.clear()

    env.filters["fallback"] = _custom_fallback_filter
    env.filters["default"] = _custom_default_filter
    env.filters["replace"] = _safe_replace_filter
    env.filters["tojson"] = _safe_tojson_filter
    return env


# Singleton engine instance
_JINJA_ENV: DograhSandboxedEnvironment | None = None


def get_jinja_env() -> DograhSandboxedEnvironment:
    """Retrieve the shared singleton Jinja environment."""
    global _JINJA_ENV
    if _JINJA_ENV is None:
        _JINJA_ENV = create_jinja_environment()
    return _JINJA_ENV


def _extract_tz_from_str(template_str: str) -> str | None:
    match = re.search(r"\{\{\s*current_time_([A-Za-z0-9_\/\-]+)", template_str)
    if match:
        tz = match.group(1).replace("__slash__", "/").replace("__hyphen__", "-")
        if tz in _AVAILABLE_ZONES:
            return tz
    return None


def _prepare_context(
    context: dict[str, Any] | None,
    template_str: str = "",
) -> dict[str, Any]:
    """Prepare context dict so flat and nested initial_context access works symmetrically."""
    enriched = dict(context or {})

    default_tz = _extract_tz_from_str(template_str)
    if default_tz:
        enriched["_default_tz"] = default_tz

    if "initial_context" in enriched and isinstance(enriched["initial_context"], dict):
        # Expose nested initial_context fields at top-level if not already shadowed
        for k, v in enriched["initial_context"].items():
            enriched.setdefault(k, v)
    else:
        # Context is flat: alias initial_context to the dictionary itself
        enriched["initial_context"] = dict(enriched)

    return enriched


def preprocess_legacy_template_with_symbols(
    template_str: str,
) -> tuple[str, dict[str, str]]:
    """Rewrite Dograh legacy template syntax into valid Jinja2 syntax and track rewritten symbol mappings.

    Returns:
        tuple[str, dict[str, str]]: (preprocessed_template, alias_to_original_map)
    """
    if not template_str or "{{" not in template_str:
        return template_str, {}

    # 0. Handle timezone slashes in clock variables (e.g. current_time_America/New_York)
    # to avoid Jinja interpreting '/' as a division operator
    def _replace_clock_tz_slash(match: re.Match[str]) -> str:
        prefix = match.group(1)
        tz_part = match.group(2).replace("/", "__slash__")
        return f"{prefix}_{tz_part}"

    clock_tz_pattern = r"(current_time|current_weekday)_([A-Za-z0-9_\/]+)"
    template_str = re.sub(clock_tz_pattern, _replace_clock_tz_slash, template_str)

    # 1. Handle {{ var | fallback:default_val }}
    def _replace_fallback_colon(match: re.Match[str]) -> str:
        var_expr = match.group(1).strip()
        fallback_val = match.group(2).strip()
        return f"{{{{ {var_expr} | fallback('{fallback_val}') }}}}"

    pattern_colon = r"\{\{\s*([^|}\s]+)\s*\|\s*fallback:([^}]+)\s*\}\}"
    result = re.sub(pattern_colon, _replace_fallback_colon, template_str)

    # 2. Handle {{ var | default_val }} where default_val is not a standard Jinja filter
    def _replace_legacy_default(match: re.Match[str]) -> str:
        var_expr = match.group(1).strip()
        filter_expr = match.group(2).strip()

        filter_name = filter_expr.split("(")[0].strip()
        if filter_name in _STANDARD_JINJA_FILTERS or "(" in filter_expr:
            return match.group(0)

        return f"{{{{ {var_expr} | default('{filter_expr}') }}}}"

    pattern_legacy_filter = r"\{\{\s*([^|}\s]+)\s*\|\s*([a-zA-Z0-9_\-\.\/]+)\s*\}\}"
    result = re.sub(pattern_legacy_filter, _replace_legacy_default, result)

    # 3. Handle unquoted legacy hyphenated variable names (e.g. {{ customer-name }})
    # to avoid Jinja compiling them as binary subtraction (customer - name).
    # Tokenize strings with escape awareness and restrict hyphenated segments to valid
    # identifier starts ([A-Za-z_]) so arithmetic like {{ total-1 }} is preserved.
    # Maintain an explicit symbol table mapping {alias: original} for root-level variables,
    # keeping nested field renames (.customer-name) separate from top-level variables.
    alias_to_original: dict[str, str] = {}

    def _replace_hyphenated_identifiers(match: re.Match[str]) -> str:
        tag_content = match.group(1)

        # If tag_content contains arithmetic operators (*, +, /, %, //, **) outside quotes,
        # or chained numeric subtractions like total-1-2, it is a math expression,
        # not a legacy hyphenated variable. Preserve Jinja arithmetic.
        stripped_expr = re.sub(
            r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')', "", tag_content
        )
        expr_before_pipe = stripped_expr.split("|")[0]
        if any(op in expr_before_pipe for op in ("*", "+", "/", "%", "**", "//")):
            return match.group(0)
        if re.search(r"-\s*\d+\s*-\s*\d+", expr_before_pipe):
            return match.group(0)

        def _replace_token(token_match: re.Match[str]) -> str:
            token = token_match.group(0)
            if (token.startswith('"') and token.endswith('"')) or (
                token.startswith("'") and token.endswith("'")
            ):
                return token

            is_nested = token_match.group(2) == "."
            raw_identifier = token_match.group(3)
            alias = raw_identifier.replace("-", "__hyphen__")

            if not is_nested and not raw_identifier.startswith(
                ("current_time_", "current_weekday_")
            ):
                alias_to_original[alias] = raw_identifier

            return f".{alias}" if is_nested else alias

        pattern = r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|(\.)?([A-Za-z_][A-Za-z0-9_]*(?:-[A-Za-z0-9_]+)+))'
        rewritten = re.sub(pattern, _replace_token, tag_content)
        return f"{{{{ {rewritten} }}}}"

    result = re.sub(r"\{\{([^{}]+?)\}\}", _replace_hyphenated_identifiers, result)

    return result, alias_to_original


def preprocess_legacy_template(template_str: str) -> str:
    """Rewrite Dograh legacy template syntax into valid Jinja2 syntax."""
    rewritten, _ = preprocess_legacy_template_with_symbols(template_str)
    return rewritten


def validate_jinja_syntax(template_str: str) -> tuple[bool, str | None]:
    """Validate Jinja syntax and enforce platform safety constraints statically.

    Enforces:
    - Well-formed tags and balanced blocks
    - No nested loops (prevents Cartesian CPU starvation)
    - Loop range <= 500 (prevents unbounded iteration)
    - No access to private/dunder attributes or reflection functions

    Returns:
        (True, None) if syntax is valid and meets safety policy.
        (False, error_message) if syntax contains errors or policy violations.
    """
    if not template_str or ("{{" not in template_str and "{%" not in template_str):
        return True, None

    preprocessed = preprocess_legacy_template(template_str)
    env = get_jinja_env()

    try:
        parsed = env.parse(preprocessed)

        # 1. Block nested loops (CPU denial-of-service defense)
        for for_node in parsed.find_all(jinja2.nodes.For):
            if any(isinstance(child, jinja2.nodes.For) for child in for_node.body):
                return False, "Nested loops are not permitted in prompt templates."

        # 2. Block exponentiation (** operator) to prevent BigInt CPU-hangs
        if any(parsed.find_all(jinja2.nodes.Pow)):
            return False, "Exponentiation ('**') is not permitted in prompt templates."

        # 3. Block template inclusion and inheritance tags (prevents file/module traversal)
        if any(
            parsed.find_all(
                (
                    jinja2.nodes.Include,
                    jinja2.nodes.Import,
                    jinja2.nodes.FromImport,
                    jinja2.nodes.Extends,
                )
            )
        ):
            return (
                False,
                "File and module inclusion tags ('include', 'import', 'extends') are not permitted in prompt templates.",
            )

        # 4. Block macro definitions (prevents infinite recursion stack exhaustion)
        if any(parsed.find_all((jinja2.nodes.Macro, jinja2.nodes.CallBlock))):
            return (
                False,
                "Macro and function definitions are not permitted in prompt templates.",
            )

        # 5. Block range() calls exceeding 50 iterations (voice prompts never require more)
        for call_node in parsed.find_all(jinja2.nodes.Call):
            if (
                isinstance(call_node.node, jinja2.nodes.Name)
                and call_node.node.name == "range"
                and call_node.args
            ):
                iterations: int | None = None
                if len(call_node.args) == 1 and isinstance(
                    call_node.args[0], jinja2.nodes.Const
                ):
                    val = call_node.args[0].value
                    if isinstance(val, int):
                        iterations = val
                elif (
                    len(call_node.args) >= 2
                    and isinstance(call_node.args[0], jinja2.nodes.Const)
                    and isinstance(call_node.args[1], jinja2.nodes.Const)
                ):
                    start = call_node.args[0].value
                    stop = call_node.args[1].value
                    step = 1
                    if len(call_node.args) >= 3 and isinstance(
                        call_node.args[2], jinja2.nodes.Const
                    ):
                        step_val = call_node.args[2].value
                        if isinstance(step_val, int) and step_val != 0:
                            step = step_val
                    if isinstance(start, int) and isinstance(stop, int):
                        iterations = max(
                            0, (stop - start + (step - (1 if step > 0 else -1))) // step
                        )

                if iterations is not None and iterations > 50:
                    return (
                        False,
                        f"Loop range exceeds maximum limit of 50 for voice prompts: range({iterations} iterations)",
                    )

            # Block calls to dangerous or reflective functions
            if isinstance(call_node.node, jinja2.nodes.Name):
                func_name = call_node.node.name
                if func_name in (
                    "eval",
                    "exec",
                    "open",
                    "compile",
                    "__import__",
                    "lipsum",
                    "cycler",
                    "joiner",
                ):
                    return (
                        False,
                        f"Function '{func_name}' is not permitted in templates.",
                    )

            # Block memory bomb string methods like ljust(1000000000)
            if isinstance(call_node.node, jinja2.nodes.Getattr):
                method_name = call_node.node.attr
                if (
                    method_name in ("ljust", "rjust", "center", "zfill", "expandtabs")
                    and call_node.args
                ):
                    first_arg = call_node.args[0]
                    if (
                        isinstance(first_arg, jinja2.nodes.Const)
                        and isinstance(first_arg.value, int)
                        and abs(first_arg.value) > MAX_RENDERED_PROMPT_CHARS
                    ):
                        return (
                            False,
                            f"String method '{method_name}' argument exceeds maximum allowable limit of {MAX_RENDERED_PROMPT_CHARS}.",
                        )
                elif method_name == "replace" and len(call_node.args) >= 2:
                    old_arg, new_arg = call_node.args[0], call_node.args[1]
                    if (
                        isinstance(old_arg, jinja2.nodes.Const)
                        and isinstance(new_arg, jinja2.nodes.Const)
                        and isinstance(new_arg.value, str)
                        and len(new_arg.value) > 1000
                    ):
                        return (
                            False,
                            "String replacement argument exceeds safe maximum limit of 1000 characters.",
                        )

        # 6. Block private or dangerous attribute access (_*, format, mro, subclasses)
        for get_attr in parsed.find_all(jinja2.nodes.Getattr):
            attr_name = get_attr.attr
            if attr_name.startswith("_") or attr_name in (
                "format",
                "format_map",
                "mro",
                "subclasses",
            ):
                return (
                    False,
                    f"Accessing system attribute '{attr_name}' is not permitted.",
                )

        # 7. Block indirect attribute access via attr filter or dictionary subscript
        for filter_node in parsed.find_all(jinja2.nodes.Filter):
            if filter_node.name == "attr" and filter_node.args:
                arg = filter_node.args[0]
                if (
                    isinstance(arg, jinja2.nodes.Const)
                    and isinstance(arg.value, str)
                    and (
                        arg.value.startswith("_")
                        or arg.value in ("format", "format_map", "mro", "subclasses")
                    )
                ):
                    return (
                        False,
                        f"Accessing system attribute via attr filter '{arg.value}' is not permitted.",
                    )

        for get_item in parsed.find_all(jinja2.nodes.Getitem):
            arg = get_item.arg
            if (
                isinstance(arg, jinja2.nodes.Const)
                and isinstance(arg.value, str)
                and arg.value.startswith("_")
            ):
                return (
                    False,
                    f"Accessing system attribute via item subscript '{arg.value}' is not permitted.",
                )

        # 8. Block memory bomb string multiplication (> 500) and collection multiplication (> 50)
        for mul_node in parsed.find_all(jinja2.nodes.Mul):
            left_const = isinstance(mul_node.left, jinja2.nodes.Const)
            right_const = isinstance(mul_node.right, jinja2.nodes.Const)
            left_list_tuple = isinstance(
                mul_node.left, (jinja2.nodes.List, jinja2.nodes.Tuple)
            )
            right_list_tuple = isinstance(
                mul_node.right, (jinja2.nodes.List, jinja2.nodes.Tuple)
            )

            if left_const and right_const:
                if (
                    isinstance(mul_node.left.value, (str, bytes))
                    and isinstance(mul_node.right.value, int)
                    and mul_node.right.value > 500
                ):
                    return (
                        False,
                        "Multiplication factor exceeds maximum allowable limit of 500.",
                    )
                if (
                    isinstance(mul_node.right.value, (str, bytes))
                    and isinstance(mul_node.left.value, int)
                    and mul_node.left.value > 500
                ):
                    return (
                        False,
                        "Multiplication factor exceeds maximum allowable limit of 500.",
                    )
                if (
                    isinstance(mul_node.left.value, (list, tuple))
                    and isinstance(mul_node.right.value, int)
                    and (
                        mul_node.right.value > 50
                        or len(mul_node.left.value) * mul_node.right.value > 50
                    )
                ):
                    return (
                        False,
                        "Collection multiplication factor/size exceeds maximum allowable limit of 50.",
                    )
                if (
                    isinstance(mul_node.right.value, (list, tuple))
                    and isinstance(mul_node.left.value, int)
                    and (
                        mul_node.left.value > 50
                        or len(mul_node.right.value) * mul_node.left.value > 50
                    )
                ):
                    return (
                        False,
                        "Collection multiplication factor/size exceeds maximum allowable limit of 50.",
                    )

            if (
                left_list_tuple
                and right_const
                and isinstance(mul_node.right.value, int)
                and (
                    mul_node.right.value > 50
                    or len(mul_node.left.items) * mul_node.right.value > 50
                )
            ):
                return (
                    False,
                    "Collection multiplication factor/size exceeds maximum allowable limit of 50.",
                )

            if (
                right_list_tuple
                and left_const
                and isinstance(mul_node.left.value, int)
                and (
                    mul_node.left.value > 50
                    or len(mul_node.right.items) * mul_node.left.value > 50
                )
            ):
                return (
                    False,
                    "Collection multiplication factor/size exceeds maximum allowable limit of 50.",
                )

        return True, None
    except TemplateSyntaxError as e:
        return False, f"Line {e.lineno}: {e.message}"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def render_jinja_string(
    template_str: str,
    context: dict[str, Any] | None = None,
    replace_escaped_newlines: bool = True,
    raise_on_error: bool = False,
) -> str:
    """Render a template string using the Sandboxed Jinja environment.

    Guarantees:
    - Never raises an unhandled exception during telephony (zero call drop).
    - Hard ceiling of 64 KB on rendered output (memory bomb defense).
    - Sub-millisecond performance on cached compiled templates.
    - Fast path if no Jinja brackets exist.
    """
    if not template_str or ("{{" not in template_str and "{%" not in template_str):
        if template_str and replace_escaped_newlines:
            return template_str.replace("\\n", "\n")
        return template_str or ""

    env = get_jinja_env()
    preprocessed = preprocess_legacy_template(template_str)
    enriched_context = _prepare_context(context, template_str=template_str)

    try:
        template = env.from_string(preprocessed)
        chunks: list[str] = []
        total_len = 0
        truncated = False
        for chunk in template.generate(enriched_context):
            chunk_len = len(chunk)
            if total_len + chunk_len > MAX_RENDERED_PROMPT_CHARS:
                remaining = MAX_RENDERED_PROMPT_CHARS - total_len
                if remaining > 0:
                    chunks.append(chunk[:remaining])
                truncated = True
                break
            chunks.append(chunk)
            total_len += chunk_len

        if truncated:
            logger.warning(
                f"Template output exceeded {MAX_RENDERED_PROMPT_CHARS} characters. Truncating."
            )
        rendered = "".join(chunks)
        if replace_escaped_newlines:
            rendered = rendered.replace("\\n", "\n")
        return rendered
    except SecurityError as e:
        if raise_on_error:
            raise
        logger.error(f"SSTI / Security exploit blocked in template: {e}")
        return ""
    except (TemplateError, OverflowError) as e:
        if raise_on_error:
            raise
        logger.warning(
            f"Jinja template error in live render: {e}. Falling back to raw string."
        )
        return template_str
    except Exception as e:
        if raise_on_error:
            raise
        logger.error(
            f"Unexpected error rendering Jinja template: {e}. Falling back to raw string."
        )
        return template_str
