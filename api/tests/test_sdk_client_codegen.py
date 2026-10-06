"""SDK client codegen must send optional request models.

FastAPI types ``Model | None`` as an ``anyOf`` of the schema ref and null.
The generator used to drop that body, so publish could not send release notes.
A required ``$ref`` body must stay required.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sdk.codegen.client_codegen import (  # noqa: E402
    _collect,
    emit_python,
    emit_typescript,
)


def _spec() -> dict:
    return {
        "paths": {
            "/api/v1/workflow/{workflow_id}/publish": {
                "post": {
                    "x-sdk-method": "publish_workflow",
                    "x-sdk-description": "Publish the draft.",
                    "parameters": [
                        {
                            "name": "workflow_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "anyOf": [
                                        {
                                            "$ref": "#/components/schemas/PublishWorkflowRequest"
                                        },
                                        {"type": "null"},
                                    ]
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/PublishWorkflowResponse"
                                    }
                                }
                            }
                        }
                    },
                }
            },
            "/api/v1/example/required-nullable": {
                "post": {
                    "x-sdk-method": "required_nullable",
                    "x-sdk-description": "Nullable schema, but the body is required.",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "anyOf": [
                                        {
                                            "$ref": "#/components/schemas/PublishWorkflowRequest"
                                        },
                                        {"type": "null"},
                                    ]
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/PublishWorkflowResponse"
                                    }
                                }
                            }
                        }
                    },
                }
            },
            "/api/v1/campaign/create": {
                "post": {
                    "x-sdk-method": "create_campaign",
                    "x-sdk-description": "Create a campaign.",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/CreateCampaignRequest"
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/CampaignResponse"
                                    }
                                }
                            }
                        }
                    },
                }
            },
        }
    }


def test_nullable_body_is_optional_and_required_body_stays_required():
    ops, _models = _collect(_spec())
    by_name = {op.method: op for op in ops}
    assert by_name["publish_workflow"].request_class == "PublishWorkflowRequest"
    assert by_name["publish_workflow"].body_optional is True
    assert by_name["publish_workflow"].response.class_name == "PublishWorkflowResponse"
    assert by_name["create_campaign"].request_class == "CreateCampaignRequest"
    assert by_name["create_campaign"].body_optional is False
    assert by_name["required_nullable"].request_class == "PublishWorkflowRequest"
    assert by_name["required_nullable"].body_optional is False

    python = emit_python(ops, [])
    typescript = emit_typescript(ops, [])
    publish_method = python.split("def publish_workflow", 1)[1].split("\n    def ", 1)[
        0
    ]
    assert (
        "def publish_workflow(self, workflow_id: int, *, "
        "body: PublishWorkflowRequest | None = None) -> PublishWorkflowResponse:"
        in python
    )
    assert "if body is not None:" in publish_method
    assert (
        'kwargs["json"] = body.model_dump(mode="json", exclude_none=True)'
        in publish_method
    )
    assert (
        "def create_campaign(self, *, body: CreateCampaignRequest) -> CampaignResponse:"
        in python
    )
    assert (
        "def required_nullable(self, *, body: PublishWorkflowRequest) "
        "-> PublishWorkflowResponse:" in python
    )
    assert (
        "async publishWorkflow(workflowId: number, "
        "opts: { body?: PublishWorkflowRequest } = {}): Promise<PublishWorkflowResponse>"
        in typescript
    )
    assert "json: opts.body" in typescript
    assert (
        "async createCampaign(opts: { body: CreateCampaignRequest }): Promise<CampaignResponse>"
        in typescript
    )
