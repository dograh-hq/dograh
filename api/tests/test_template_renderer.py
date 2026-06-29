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
