import pytest

from api.services.filesystem.minio import _infer_url_scheme


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("localhost:9000", "http"),
        ("127.0.0.1:9000", "http"),
        ("minio:9000", "http"),
        ("host.docker.internal:9000", "http"),
        ("zoren-voice.ashtra.ai", "https"),
        ("http://zoren-voice.ashtra.ai", "http"),
        ("https://zoren-voice.ashtra.ai", "https"),
    ],
)
def test_infer_url_scheme(endpoint: str, expected: str) -> None:
    assert _infer_url_scheme(endpoint) == expected
