from api.services.workflow.jinja_validation import (
    validate_workflow_jinja_templates,
)


def test_validate_workflow_jinja_templates_accepts_valid():
    definition = {
        "nodes": [
            {
                "id": "node-1",
                "data": {
                    "prompt": "Hello {{ customer-name }}! It is {{ current_time_America/New_York }}",
                    "greeting": "Welcome {{ name | default('there') }}",
                    "extraction_prompt": "Extract details for {{ customer-name }}",
                    "announcement": "Connecting you to {{ agent_name }}",
                    "introduction": "Hello from {{ department }}",
                    "extraction_variables": [
                        {
                            "name": "account_id",
                            "prompt": "Look for account number for {{ customer-name }}",
                        }
                    ],
                    "custom_tools": [
                        {
                            "introduction": "Calling tool for {{ customer-name }}",
                            "announcement": "Please wait {{ wait_time }}",
                            "messages": [
                                {"type": "text", "text": "Searching for {{ query }}"}
                            ],
                        }
                    ],
                    "disposition_options": [
                        {
                            "code": "resolved",
                            "description": "Issue resolved for {{ customer-name }}",
                        }
                    ],
                },
            }
        ],
        "edges": [
            {
                "id": "edge-1",
                "transition_speech": "Moving on to step 2 for {{ customer-name }}",
            }
        ],
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
                    "extraction_prompt": "{{ ().__class__ }}",
                    "extraction_variables": [
                        {
                            "name": "bad_var",
                            "prompt": "{% for i in range(10) %}{% for j in range(10) %}{{ i }}{% endfor %}{% endfor %}",
                        }
                    ],
                    "custom_tools": [
                        {
                            "messages": [
                                {"type": "text", "text": "{{ [].__class__ }}"}
                            ],
                        }
                    ],
                    "disposition_options": [
                        {"code": "bad", "description": "{{ [].__subclasses__() }}"}
                    ],
                },
            }
        ],
        "edges": [
            {
                "id": "edge-1",
                "transition_speech": "{% unclosed_tag",
            }
        ],
    }
    errors = validate_workflow_jinja_templates(definition)
    assert len(errors) == 7
    # 1. prompt: security violation
    assert errors[0]["id"] == "node-1"
    assert errors[0]["field"] == "data.prompt"
    assert "not permitted" in errors[0]["message"]
    assert "__mro__" in errors[0]["message"]

    # 2. greeting: syntax error
    assert errors[1]["field"] == "data.greeting"
    assert "Line 1" in errors[1]["message"]

    # 3. extraction_prompt: security violation
    assert errors[2]["field"] == "data.extraction_prompt"
    assert "__class__" in errors[2]["message"]

    # 4. extraction_variable prompt: nested loop violation
    assert errors[3]["field"] == "data.extraction_variables[0].prompt"
    assert "Nested loops are not permitted" in errors[3]["message"]

    # 5. tool message text: security violation
    assert errors[4]["field"] == "data.custom_tools[0].messages[0].text"
    assert "__class__" in errors[4]["message"]

    # 6. disposition description: security violation
    assert errors[5]["field"] == "data.disposition_options[0].description"
    assert "__subclasses__" in errors[5]["message"]

    # 7. edge transition speech: syntax error
    assert errors[6]["id"] == "edge-1"
    assert errors[6]["field"] == "transition_speech"
    assert "Line 1" in errors[6]["message"]
