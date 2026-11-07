from api.services.workflow.jinja_validation import (
    validate_workflow_jinja_templates,
)


def test_validate_workflow_jinja_templates_accepts_valid():
    definition = {
        "nodes": [
            {
                "id": "node-1",
                "data": {
                    "prompt": "Hello {{ name }}! {% if is_vip %}VIP{% endif %}",
                    "greeting": "Welcome {{ name | default('there') }}",
                },
            }
        ]
    }
    errors = validate_workflow_jinja_templates(definition)
    assert errors == []


def test_validate_workflow_jinja_templates_rejects_unsafe_and_invalid():
    definition = {
        "nodes": [
            {
                "id": "node-1",
                "data": {
                    "prompt": "{{ [].__class__.__mro__ }}",
                    "greeting": "{% if %}Missing condition{% endif %}",
                },
            }
        ]
    }
    errors = validate_workflow_jinja_templates(definition)
    assert len(errors) == 2
    assert errors[0]["id"] == "node-1"
    assert errors[0]["field"] == "data.prompt"
    assert errors[1]["field"] == "data.greeting"
