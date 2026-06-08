from api.services.pipecat.pre_call_fetch import _extract_initial_context


class TestExtractInitialContext:
    """Tests for _extract_initial_context, the pre-call fetch response parser."""

    def test_initial_context_nested_under_call_inbound(self):
        """The canonical `initial_context` key nested under `call_inbound`."""
        response = {"call_inbound": {"initial_context": {"customer_name": "Jane"}}}
        assert _extract_initial_context(response) == {"customer_name": "Jane"}

    def test_initial_context_at_top_level(self):
        """The canonical `initial_context` key at the top level."""
        response = {"initial_context": {"customer_name": "Jane"}}
        assert _extract_initial_context(response) == {"customer_name": "Jane"}

    def test_legacy_dynamic_variables_nested(self):
        """The legacy `dynamic_variables` key still works nested under `call_inbound`."""
        response = {"call_inbound": {"dynamic_variables": {"customer_name": "Jane"}}}
        assert _extract_initial_context(response) == {"customer_name": "Jane"}

    def test_legacy_dynamic_variables_at_top_level(self):
        """The legacy `dynamic_variables` key still works at the top level."""
        response = {"dynamic_variables": {"customer_name": "Jane"}}
        assert _extract_initial_context(response) == {"customer_name": "Jane"}

    def test_initial_context_takes_precedence_over_legacy(self):
        """When both keys are present, `initial_context` wins."""
        response = {
            "call_inbound": {
                "initial_context": {"source": "new"},
                "dynamic_variables": {"source": "legacy"},
            }
        }
        assert _extract_initial_context(response) == {"source": "new"}

    def test_falls_back_to_legacy_when_initial_context_not_a_dict(self):
        """A non-dict `initial_context` falls back to `dynamic_variables`."""
        response = {
            "initial_context": None,
            "dynamic_variables": {"customer_name": "Jane"},
        }
        assert _extract_initial_context(response) == {"customer_name": "Jane"}

    def test_nested_values_preserved(self):
        """Nested objects pass through untouched for dot-notation access."""
        response = {
            "call_inbound": {
                "initial_context": {"customer": {"address": {"city": "LA"}}}
            }
        }
        assert _extract_initial_context(response) == {
            "customer": {"address": {"city": "LA"}}
        }

    def test_empty_when_no_known_keys(self):
        """A response with neither key yields an empty dict."""
        assert _extract_initial_context({"call_inbound": {"agent_id": 1}}) == {}

    def test_empty_when_call_inbound_missing(self):
        """No `call_inbound` and no top-level keys yields an empty dict."""
        assert _extract_initial_context({}) == {}

    def test_non_dict_vars_yield_empty(self):
        """A non-dict value under a known key yields an empty dict."""
        assert _extract_initial_context({"initial_context": "nope"}) == {}
