from api.services.configuration.registry import OPENAI_MODELS

# Models OpenAI has shut down; the settings dropdown must not offer them.
RETIRED_OPENAI_MODELS = {"gpt-3.5-turbo", "gpt-4.1-nano"}


def test_openai_model_suggestions_exclude_retired_models():
    assert RETIRED_OPENAI_MODELS.isdisjoint(OPENAI_MODELS)
