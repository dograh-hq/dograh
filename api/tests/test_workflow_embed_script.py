"""Tests for the copy-paste embed snippet."""

from types import SimpleNamespace

from api.routes.workflow_embed import generate_embed_script


def _script() -> str:
    return generate_embed_script(SimpleNamespace(token="emb_TEST"))


def test_script_loads_the_widget_with_the_token():
    script = _script()
    assert "/embed/dograh-widget.js?token=emb_TEST" in script


def test_script_seeds_the_context_attribute():
    """The snippet teaches the context mechanism with editable sample values."""
    script = _script()
    assert "js.setAttribute('data-dograh-context', JSON.stringify({" in script
    assert "page_url: window.location.href" in script
    # The prompt reference is a comment, and the f-string must not leak its
    # own brace escaping into the copied snippet.
    assert "{{initial_context.page_url}}" in script
    assert "{{{" not in script and "}}}" not in script
