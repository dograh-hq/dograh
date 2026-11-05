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
