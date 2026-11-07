from api.services.workflow.workflow_graph import extract_template_variables
from api.utils.template_renderer import render_template


def test_initial_context_prefix_resolves_against_flat_context():
    context = {
        "first_name": "Abhishek",
        "runtime_configuration": {
            "realtime_model": "gpt-realtime-2",
        },
    }

    assert (
        render_template("Hi {{initial_context.first_name | there}}", context)
        == "Hi Abhishek"
    )
    assert (
        render_template(
            "Model {{initial_context.runtime_configuration.realtime_model}}", context
        )
        == "Model gpt-realtime-2"
    )


def test_initial_context_prefix_prefers_explicit_initial_context():
    context = {
        "first_name": "Flat",
        "initial_context": {
            "first_name": "Nested",
        },
    }

    assert render_template("Hi {{initial_context.first_name}}", context) == "Hi Nested"


def test_initial_context_prefix_uses_fallback_when_missing_from_both_contexts():
    assert (
        render_template("Hi {{initial_context.first_name | there}}", {}) == "Hi there"
    )


def test_clock_variables_are_not_asked_of_source_data():
    """A campaign's contact file cannot supply what the clock supplies.

    ``{{current_time_<TZ>}}`` is resolved here at render time, so a workflow
    using one is complete without a column named after it. Treating it as a
    required variable rejected contact files that had everything they could
    have.
    """
    required = extract_template_variables(
        "Hi {{customer_name}}, it is {{current_time_America/New_York}} "
        "on {{current_weekday_America/New_York}}. {{current_time}} "
        "{{current_weekday}}"
    )

    assert required == {"customer_name"}


def test_clock_variables_still_render():
    rendered = render_template("It is {{current_weekday_America/New_York}}", {})

    assert rendered != "It is "
    assert "{{" not in rendered


def test_template_renderer_runtime_error_falls_back_gracefully():
    """Verify that runtime errors (like TypeError on arithmetic with None) do not crash live calls."""
    context = {"amount": None, "greeting": "Hello"}
    template = "Greeting: {{ greeting }}. Math: {{ amount + 1 }}"
    rendered = render_template(template, context)
    assert "Greeting: Hello" in rendered


def test_template_renderer_security_error_blocks_exploit():
    """Verify that SecurityError does not fall back to legacy regex and returns empty string."""
    template = "{{ [].__class__.__mro__ }}"
    rendered = render_template(template, {})
    assert rendered == ""


def test_extract_template_variables_excludes_jinja_locals():
    """Verify that loop targets and set variables are excluded from campaign required variables."""
    # Loop over range has no source data requirements
    assert (
        extract_template_variables("{% for i in range(3) %}{{ i }}{% endfor %}")
        == set()
    )

    # Local set assignment is not required from contact CSV
    assert extract_template_variables("{% set x = 123 %}{{ x }}") == set()

    # Loop with parenthesized and unparenthesized tuple unpack targets
    assert extract_template_variables(
        "{% for (k, v) in pairs %}{{ k }}: {{ v }}{% endfor %}"
    ) == {"pairs"}
    assert extract_template_variables(
        "{% for k, v in pairs %}{{ k }}: {{ v }}{% endfor %}"
    ) == {"pairs"}

    # Loop target is local, but iterable is required
    assert extract_template_variables(
        "{% for item in items %}{{ item }}{% endfor %}"
    ) == {"items"}

    # Conditions require their test variables
    assert extract_template_variables(
        "{% if is_vip %}VIP{% else %}Standard{% endif %}"
    ) == {"is_vip"}

    # Variables with fallback/default are optional
    assert extract_template_variables("Hi {{ name | default('there') }}") == set()
    assert extract_template_variables("Hi {{ name | fallback('there') }}") == set()

    # Variable used bare without fallback is required even if elsewhere used with fallback
    assert extract_template_variables(
        "Hi {{ name | default('there') }}. Your name is {{ name }}."
    ) == {"name"}

    # Nested attributes are runtime-resolved and skipped
    assert (
        extract_template_variables("{{ user.address }} {{ gathered_context.city }}")
        == set()
    )
