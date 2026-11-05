"""Unit tests for Dograh Sandboxed Jinja2 Engine."""

import pytest

from api.utils.jinja_engine import (
    render_jinja_string,
    validate_jinja_syntax,
)


def test_validate_valid_jinja_syntax():
    valid_template = (
        "Hello {{ name }}.\n"
        "{% if is_vip %}\n"
        "Welcome VIP!\n"
        "{% else %}\n"
        "Welcome guest!\n"
        "{% endif %}\n"
        "{% for item in items %}\n"
        "- {{ item }}\n"
        "{% endfor %}"
    )
    is_valid, error = validate_jinja_syntax(valid_template)
    assert is_valid is True
    assert error is None


def test_validate_unclosed_if_tag():
    invalid_template = "Hello {% if is_vip %} welcome VIP without closing"
    is_valid, error = validate_jinja_syntax(invalid_template)
    assert is_valid is False
    assert error is not None
    assert "Unexpected end of template" in error or "endif" in error.lower()


def test_validate_unclosed_for_tag():
    invalid_template = "Items: {% for item in items %}- {{ item }}"
    is_valid, error = validate_jinja_syntax(invalid_template)
    assert is_valid is False
    assert error is not None
    assert "Unexpected end of template" in error or "endfor" in error.lower()


def test_validate_legacy_syntax_is_accepted():
    legacy_template = "Hi {{ initial_context.first_name | fallback:there }}"
    is_valid, error = validate_jinja_syntax(legacy_template)
    assert is_valid is True
    assert error is None


def test_render_conditionals_and_loops():
    template = (
        "Hello {{ name }}.\n"
        "{% if is_vip %}"
        "Priority support."
        "{% else %}"
        "Standard support."
        "{% endif %}\n"
        "Orders:\n"
        "{% for order in orders %}"
        "- {{ order }}\n"
        "{% endfor %}"
    )
    context = {
        "name": "Sarah",
        "is_vip": True,
        "orders": ["Book", "Pen"],
    }
    rendered = render_jinja_string(template, context)
    assert "Hello Sarah." in rendered
    assert "Priority support." in rendered
    assert "Standard support." not in rendered
    assert "- Book" in rendered
    assert "- Pen" in rendered


def test_render_missing_variable_does_not_crash():
    template = "Hello {{ missing_var }}, welcome!"
    rendered = render_jinja_string(template, {})
    assert rendered == "Hello , welcome!"


def test_render_undefined_variable_in_condition_evaluates_falsy():
    # 1. Undefined in boolean check
    t_bool = "{% if uncollected_val %}Has val{% else %}No val{% endif %}"
    assert render_jinja_string(t_bool, {}) == "No val"

    # 2. Undefined in numeric comparison (> 500)
    t_comp = "{% if loan_amount > 500 %}Large loan{% else %}No/Small loan{% endif %}"
    assert render_jinja_string(t_comp, {}) == "No/Small loan"

    # 3. Undefined in equality check
    t_eq = "{% if tier == 'gold' %}VIP{% else %}Standard{% endif %}"
    assert render_jinja_string(t_eq, {}) == "Standard"


def test_render_legacy_fallback_syntax():
    template = "Hi {{ name | fallback:friend }}"
    assert render_jinja_string(template, {}) == "Hi friend"
    assert render_jinja_string(template, {"name": "Bob"}) == "Hi Bob"

    template_single_pipe = "Hi {{ name | friend }}"
    assert render_jinja_string(template_single_pipe, {}) == "Hi friend"
    assert render_jinja_string(template_single_pipe, {"name": "Bob"}) == "Hi Bob"


def test_render_clock_variables():
    template = "Today is {{ current_weekday }} at {{ current_time }}."
    rendered = render_jinja_string(template, {})
    assert "Today is " in rendered
    assert "{{" not in rendered


def test_render_initial_context_flat_and_nested():
    template = "Hi {{ initial_context.user_name }}"

    # When context is flat
    rendered_flat = render_jinja_string(template, {"user_name": "FlatUser"})
    assert rendered_flat == "Hi FlatUser"

    # When context is explicitly nested
    rendered_nested = render_jinja_string(
        template, {"initial_context": {"user_name": "NestedUser"}}
    )
    assert rendered_nested == "Hi NestedUser"


def test_ssti_security_exploit_is_blocked():
    """Verify that Python introspection / sandbox escape exploits fail."""
    exploit_template = "{{ ''.__class__.__mro__[1].__subclasses__() }}"
    # In our hardened sandbox, accessing __class__ is unsafe
    rendered = render_jinja_string(exploit_template, {})
    # It must not output subclasses or run code
    assert "subclasses" not in rendered
    assert "class" not in rendered


def test_format_prompt_dynamic_context(monkeypatch):
    """Verify PipecatEngine prompt formatting dynamically reflects updated call context vars."""
    import speechmatics.agent_stt

    monkeypatch.setattr(
        speechmatics.agent_stt,
        "SUPPORTED_SAMPLE_RATES",
        (8000, 16000, 44100, 48000),
        raising=False,
    )

    from api.services.workflow.pipecat_engine import PipecatEngine

    engine = PipecatEngine.__new__(PipecatEngine)
    engine._call_context_vars = {"user_name": "InitialUser", "is_vip": False}

    template = "Hello {{ user_name }}! {% if is_vip %}VIP{% else %}Standard{% endif %}"
    rendered = engine._format_prompt(template)
    assert rendered == "Hello InitialUser! Standard"

    # Context updates dynamically when variables are extracted
    engine._call_context_vars = {"user_name": "UpdatedUser", "is_vip": True}
    assert engine._format_prompt(template) == "Hello UpdatedUser! VIP"


def test_validate_blocks_nested_loops():
    """Verify that nested for loops are rejected at validation time."""
    template = (
        "{% for a in items %}{% for b in a.subs %}{{ b }}{% endfor %}{% endfor %}"
    )
    is_valid, error = validate_jinja_syntax(template)
    assert is_valid is False
    assert "Nested loops are not permitted" in error


def test_validate_blocks_large_ranges():
    """Verify that loop ranges exceeding 50 are rejected at validation time."""
    template = "{% for i in range(100) %}{{ i }}{% endfor %}"
    is_valid, error = validate_jinja_syntax(template)
    assert is_valid is False
    assert "Loop range exceeds maximum limit of 50" in error


def test_validate_blocks_private_attributes():
    """Verify that private or dangerous attribute traversals are rejected at validation time."""
    template = "{{ user._secret_token }}"
    is_valid, error = validate_jinja_syntax(template)
    assert is_valid is False
    assert "Accessing system attribute '_secret_token' is not permitted" in error


def test_validate_blocks_forbidden_functions():
    """Verify that reflective or code execution functions are rejected at validation time."""
    template = "{{ eval('1 + 1') }}"
    is_valid, error = validate_jinja_syntax(template)
    assert is_valid is False
    assert "Function 'eval' is not permitted" in error


def test_validate_blocks_exponentiation():
    """Verify that ** exponentiation is blocked at validation time to prevent BigInt CPU hangs."""
    template = "{{ 2 ** 100 }}"
    is_valid, error = validate_jinja_syntax(template)
    assert is_valid is False
    assert "Exponentiation ('**') is not permitted" in error


def test_validate_blocks_file_inclusion_and_inheritance():
    """Verify that include, import, and extends are blocked at validation time."""
    for tag in (
        '{% include "/etc/passwd" %}',
        '{% import "os" as os %}',
        '{% extends "base.html" %}',
    ):
        is_valid, error = validate_jinja_syntax(tag)
        assert is_valid is False
        assert "File and module inclusion tags" in error


def test_validate_blocks_macros():
    """Verify that macro definitions are blocked to prevent infinite recursion."""
    template = "{% macro bomb() %}{{ bomb() }}{% endmacro %}"
    is_valid, error = validate_jinja_syntax(template)
    assert is_valid is False
    assert "Macro and function definitions are not permitted" in error


def test_validate_blocks_indirect_attribute_access():
    """Verify that attr filter and item subscript private lookups are blocked."""
    t1 = '{{ user | attr("_secret") }}'
    is_valid1, error1 = validate_jinja_syntax(t1)
    assert is_valid1 is False
    assert "Accessing system attribute via attr filter" in error1

    t2 = '{{ user["_token"] }}'
    is_valid2, error2 = validate_jinja_syntax(t2)
    assert is_valid2 is False
    assert "Accessing system attribute via item subscript" in error2


def test_validate_blocks_large_multiplication():
    """Verify that large string/list multiplication is rejected to prevent memory bombs."""
    template = '{{ "A" * 1000 }}'
    is_valid, error = validate_jinja_syntax(template)
    assert is_valid is False
    assert "Multiplication factor exceeds maximum allowable limit of 500" in error


def test_render_truncates_output_exceeding_max_chars():
    """Verify that outputs exceeding 64KB are capped to protect worker memory."""
    # 50 iterations * 1500 chars = 75,000 chars (> 65,536)
    template = "{% for i in range(50) %}" + ("A" * 1500) + "{% endfor %}"
    rendered = render_jinja_string(template, {})
    assert len(rendered) == 65536


def test_runtime_sandbox_blocks_memory_bomb_multiplication():
    """Verify that runtime binop interceptor blocks string multiplication exceeding 500."""
    template = '{{ "A" * factor }}'
    rendered = render_jinja_string(template, {"factor": 10000})
    assert rendered == template


def test_validate_blocks_range_with_start_and_stop():
    """Verify that range(start, stop) exceeding 50 iterations is blocked in AST."""
    is_valid, error = validate_jinja_syntax(
        "{% for i in range(0, 100) %}{{ i }}{% endfor %}"
    )
    assert is_valid is False
    assert "Loop range exceeds maximum limit of 50" in error


def test_runtime_sandbox_blocks_range_exceeding_max():
    """Verify that runtime safe_range blocks dynamic ranges exceeding 50 iterations."""
    template = "{% for i in range(n) %}{{ i }}{% endfor %}"
    rendered = render_jinja_string(template, {"n": 100})
    assert rendered == template


def test_runtime_sandbox_blocks_chained_multiplication():
    """Verify that chained multiplication exceeding 64KB is intercepted."""
    template = "{{ ('A' * 500) * 500 }}"
    rendered = render_jinja_string(template, {})
    assert rendered == template


def test_default_filter_accepts_boolean_flag():
    """Verify that the default filter supports Jinja's boolean=True parameter."""
    template = "{{ empty_list | default('fallback', true) }}"
    rendered = render_jinja_string(template, {"empty_list": []})
    assert rendered == "fallback"


def test_current_weekday_inherits_template_timezone():
    """Verify that bare current_weekday inherits timezone from current_time in same template."""
    template = "{{ current_time_America/New_York }} on a {{ current_weekday }}"
    rendered = render_jinja_string(template, {})
    assert "EDT" in rendered or "EST" in rendered
    assert "on a " in rendered


def test_context_variable_named_range_does_not_crash_loops():
    """Verify that CRM variables named 'range' preserve both direct {{ range }} access and loops."""
    template = (
        "Direct range: {{ range }}\n"
        "InitialContext range: {{ initial_context.range }}\n"
        "Items: {% for i in range(3) %}{{ i }}{% endfor %}"
    )
    context = {"range": "$100k-$150k"}
    rendered = render_jinja_string(template, context)
    assert "Direct range: $100k-$150k" in rendered
    assert "InitialContext range: $100k-$150k" in rendered
    assert "Items: 012" in rendered


def test_template_renderer_no_double_expansion():
    """Verify that templates do not double-expand {{ }} present inside supplied context values."""
    from api.utils.template_renderer import render_template

    template = "Message: {{ message }}"
    context = {"message": "Use {{ code }}", "code": "SECRET_123"}
    rendered = render_template(template, context)
    assert rendered == "Message: Use {{ code }}"


def test_template_renderer_legacy_token_hyphen_fallback():
    """Verify that hyphenated tokens fall back to legacy renderer and resolve correctly."""
    from api.utils.template_renderer import render_template

    template = "Hello {{ customer-name }}"
    context = {"customer-name": "Alice"}
    rendered = render_template(template, context)
    assert rendered == "Hello Alice"


def test_invalid_timezone_fails_silently():
    """Verify that non-existent timezone strings return empty string without raising."""
    template = "Time: '{{ current_time_Invalid_Zone_12345 }}'"
    rendered = render_jinja_string(template, {})
    assert rendered == "Time: ''"


def test_validate_and_runtime_block_allocating_string_methods():
    """Verify that string methods with huge width/tabsize are rejected at save and runtime, and replace expansion is blocked at runtime."""
    # 1. ljust width check
    huge_template = "{{ 'x'.ljust(1000000000) }}"
    is_valid, err = validate_jinja_syntax(huge_template)
    assert not is_valid
    assert err is not None
    assert "exceeds maximum allowable limit" in err

    with pytest.raises(OverflowError):
        render_jinja_string(huge_template, {}, raise_on_error=True)

    # 2. expandtabs tabsize check
    huge_tab_template = "{{ 'x\t'.expandtabs(1000000000) }}"
    is_valid_tab, err_tab = validate_jinja_syntax(huge_tab_template)
    assert not is_valid_tab
    assert err_tab is not None
    assert "exceeds maximum allowable limit" in err_tab

    with pytest.raises(OverflowError):
        render_jinja_string(huge_tab_template, {}, raise_on_error=True)

    # 3. replace expansion memory bomb check
    huge_replace_template = "{{ ('x' * 500).replace('x', 'y' * 500) }}"
    with pytest.raises(OverflowError):
        render_jinja_string(huge_replace_template, {}, raise_on_error=True)


def test_range_preserves_native_types_and_resolves_cleanly():
    """Verify range resolves correctly when missing, numeric, string, None, or in loops."""
    # 1. Missing from context: renders empty and triggers fallback without leaking function repr
    t_missing = "{{ range }} | {{ range | fallback:N/A }} | {% for i in range(3) %}{{ i }}{% endfor %}"
    res_missing = render_jinja_string(t_missing, {})
    assert res_missing == " | N/A | 012"

    # 2. Integer in context: preserves numeric arithmetic while safe range() loop still works
    t_int = "{{ range + 15 }} | {% for i in range(2) %}{{ i }}{% endfor %}"
    res_int = render_jinja_string(t_int, {"range": 100})
    assert res_int == "115 | 01"

    # 3. None in context: renders empty string without leaking None literal
    t_none = "'{{ range }}' | {% for i in range(2) %}{{ i }}{% endfor %}"
    res_none = render_jinja_string(t_none, {"range": None})
    assert res_none == "'' | 01"


def test_security_error_reraised_when_raise_on_error_true():
    """Verify that SSTI security violations are raised when raise_on_error=True."""
    from jinja2.exceptions import SecurityError

    malicious = "{{ [].__class__.__mro__ }}"
    with pytest.raises(SecurityError):
        render_jinja_string(malicious, {}, raise_on_error=True)
