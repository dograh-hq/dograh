import pytest


@pytest.fixture
def settings():
    from app.config import Settings
    return Settings(
        livekit_url="ws://test.livekit.io",
        livekit_api_key="test-key",
        livekit_api_secret="test-secret",
        dograh_api_url="http://test-dograh:8000",
        dograh_internal_token="test-token",
    )
