from api.services.configuration.registry import AWS_BEDROCK_MODELS

# Bedrock reached end-of-life for these models on 2026-10-14, so requests to them fail.
RETIRED_BEDROCK_MODELS = {
    "us.anthropic.claude-sonnet-4-20250514-v1:0",
    "anthropic.claude-sonnet-4-20250514-v1:0",
}


def test_bedrock_model_suggestions_exclude_retired_models():
    assert not RETIRED_BEDROCK_MODELS.intersection(AWS_BEDROCK_MODELS)
