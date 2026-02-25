from api.services.configuration.masking import (
    mask_key,
    mask_workflow_definition,
    merge_workflow_api_keys,
)


def _make_workflow_def(nodes):
    """Helper to build a minimal workflow definition dict."""
    return {"nodes": nodes, "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}}


def _qa_node(node_id="qa-1", api_key="", **extra_data):
    """Helper to build a QA node."""
    data = {"name": "QA Analysis", "qa_enabled": True, **extra_data}
    if api_key:
        data["qa_api_key"] = api_key
    return {"id": node_id, "type": "qa", "position": {"x": 0, "y": 0}, "data": data}


def _agent_node(node_id="agent-1"):
    """Helper to build a non-QA node."""
    return {
        "id": node_id,
        "type": "agentNode",
        "position": {"x": 0, "y": 0},
        "data": {"name": "Agent", "prompt": "hello"},
    }


# ---------------------------------------------------------------------------
# mask_workflow_definition
# ---------------------------------------------------------------------------


class TestMaskWorkflowDefinition:
    def test_masks_qa_api_key(self):
        """QA node api_key is masked, showing only last 4 chars."""
        real_key = "sk-proj-abcdefghijklmnop"
        wf = _make_workflow_def([_qa_node(api_key=real_key)])

        masked = mask_workflow_definition(wf)

        masked_key = masked["nodes"][0]["data"]["qa_api_key"]
        assert masked_key == mask_key(real_key)
        assert masked_key.endswith("mnop")
        assert masked_key.startswith("*")
        assert real_key not in str(masked)

    def test_does_not_mutate_original(self):
        """The original workflow definition is not modified."""
        real_key = "sk-proj-abcdefghijklmnop"
        wf = _make_workflow_def([_qa_node(api_key=real_key)])

        mask_workflow_definition(wf)

        assert wf["nodes"][0]["data"]["qa_api_key"] == real_key

    def test_non_qa_nodes_untouched(self):
        """Non-QA nodes are not modified."""
        wf = _make_workflow_def([_agent_node(), _qa_node(api_key="sk-secret1234")])

        masked = mask_workflow_definition(wf)

        assert masked["nodes"][0]["type"] == "agentNode"
        assert "qa_api_key" not in masked["nodes"][0]["data"]
        assert masked["nodes"][1]["data"]["qa_api_key"] == mask_key("sk-secret1234")

    def test_qa_node_without_api_key(self):
        """QA node with no api_key is left as-is."""
        wf = _make_workflow_def([_qa_node()])

        masked = mask_workflow_definition(wf)

        assert "qa_api_key" not in masked["nodes"][0]["data"]

    def test_qa_node_with_empty_api_key(self):
        """QA node with empty string api_key is left as-is."""
        node = _qa_node()
        node["data"]["qa_api_key"] = ""
        wf = _make_workflow_def([node])

        masked = mask_workflow_definition(wf)

        assert masked["nodes"][0]["data"]["qa_api_key"] == ""

    def test_multiple_qa_nodes(self):
        """All QA nodes in a definition are masked."""
        wf = _make_workflow_def(
            [
                _qa_node(node_id="qa-1", api_key="key-aaaa1111"),
                _qa_node(node_id="qa-2", api_key="key-bbbb2222"),
            ]
        )

        masked = mask_workflow_definition(wf)

        assert masked["nodes"][0]["data"]["qa_api_key"] == mask_key("key-aaaa1111")
        assert masked["nodes"][1]["data"]["qa_api_key"] == mask_key("key-bbbb2222")

    def test_none_definition(self):
        """None input returns None."""
        assert mask_workflow_definition(None) is None

    def test_empty_definition(self):
        """Empty dict returns empty dict."""
        assert mask_workflow_definition({}) == {}

    def test_definition_without_nodes(self):
        """Definition with no nodes key is returned as-is."""
        wf = {"edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}}
        result = mask_workflow_definition(wf)
        assert result == wf


# ---------------------------------------------------------------------------
# merge_workflow_api_keys
# ---------------------------------------------------------------------------


class TestMergeWorkflowApiKeys:
    def test_masked_key_is_restored(self):
        """When incoming key matches the mask of the existing key, real key is preserved."""
        real_key = "sk-proj-abcdefghijklmnop"
        masked_val = mask_key(real_key)

        existing = _make_workflow_def([_qa_node(api_key=real_key)])
        incoming = _make_workflow_def([_qa_node(api_key=masked_val)])

        result = merge_workflow_api_keys(incoming, existing)

        assert result["nodes"][0]["data"]["qa_api_key"] == real_key

    def test_new_key_is_accepted(self):
        """When user provides a brand new key, it replaces the old one."""
        old_key = "sk-proj-abcdefghijklmnop"
        new_key = "sk-proj-zyxwvutsrqponmlk"

        existing = _make_workflow_def([_qa_node(api_key=old_key)])
        incoming = _make_workflow_def([_qa_node(api_key=new_key)])

        result = merge_workflow_api_keys(incoming, existing)

        assert result["nodes"][0]["data"]["qa_api_key"] == new_key

    def test_no_existing_qa_node(self):
        """New QA node with no prior existing node keeps incoming key."""
        new_key = "sk-brand-new-key1234"

        existing = _make_workflow_def([_agent_node()])
        incoming = _make_workflow_def([_qa_node(api_key=new_key)])

        result = merge_workflow_api_keys(incoming, existing)

        assert result["nodes"][0]["data"]["qa_api_key"] == new_key

    def test_no_incoming_api_key(self):
        """QA node without api_key in incoming is left alone."""
        existing = _make_workflow_def([_qa_node(api_key="sk-existing-key1")])
        incoming = _make_workflow_def([_qa_node()])

        result = merge_workflow_api_keys(incoming, existing)

        assert "qa_api_key" not in result["nodes"][0]["data"]

    def test_multiple_qa_nodes_matched_by_id(self):
        """Multiple QA nodes are matched by node ID, not position."""
        key_1 = "sk-first-key-abcd1234"
        key_2 = "sk-second-key-efgh5678"

        existing = _make_workflow_def(
            [
                _qa_node(node_id="qa-1", api_key=key_1),
                _qa_node(node_id="qa-2", api_key=key_2),
            ]
        )
        incoming = _make_workflow_def(
            [
                _qa_node(node_id="qa-2", api_key=mask_key(key_2)),
                _qa_node(node_id="qa-1", api_key=mask_key(key_1)),
            ]
        )

        result = merge_workflow_api_keys(incoming, existing)

        node_map = {n["id"]: n for n in result["nodes"]}
        assert node_map["qa-1"]["data"]["qa_api_key"] == key_1
        assert node_map["qa-2"]["data"]["qa_api_key"] == key_2

    def test_none_incoming_returns_none(self):
        existing = _make_workflow_def([_qa_node(api_key="sk-key")])
        assert merge_workflow_api_keys(None, existing) is None

    def test_none_existing_returns_incoming(self):
        incoming = _make_workflow_def([_qa_node(api_key="sk-key")])
        result = merge_workflow_api_keys(incoming, None)
        assert result["nodes"][0]["data"]["qa_api_key"] == "sk-key"

    def test_non_qa_nodes_not_affected(self):
        """Agent nodes pass through without modification."""
        existing = _make_workflow_def([_agent_node()])
        incoming = _make_workflow_def([_agent_node()])

        result = merge_workflow_api_keys(incoming, existing)

        assert result["nodes"][0]["type"] == "agentNode"

    def test_existing_node_has_no_key(self):
        """If existing QA node had no key, incoming key is kept."""
        new_key = "sk-new-key-abcd1234"

        existing = _make_workflow_def([_qa_node()])
        incoming = _make_workflow_def([_qa_node(api_key=new_key)])

        result = merge_workflow_api_keys(incoming, existing)

        assert result["nodes"][0]["data"]["qa_api_key"] == new_key


# ---------------------------------------------------------------------------
# Round-trip: mask then merge
# ---------------------------------------------------------------------------


class TestMaskAndMergeRoundTrip:
    def test_full_round_trip_preserves_key(self):
        """Simulates: save real key → GET masks it → PUT sends masked → merge restores."""
        real_key = "sk-proj-WZRTVpVvZEXF5s0H4y8N5n2BF6lRZhC79Zq"

        # 1. Real key stored in DB
        stored = _make_workflow_def(
            [
                _qa_node(api_key=real_key, qa_provider="openai", qa_model="gpt-4.1"),
            ]
        )

        # 2. GET response masks it
        fetched = mask_workflow_definition(stored)
        masked_key = fetched["nodes"][0]["data"]["qa_api_key"]
        assert masked_key != real_key
        assert masked_key.endswith(real_key[-4:])

        # 3. User saves without changing the key (sends masked value back)
        incoming = fetched  # same as what was fetched

        # 4. PUT merges — real key is restored
        merged = merge_workflow_api_keys(incoming, stored)
        assert merged["nodes"][0]["data"]["qa_api_key"] == real_key

    def test_round_trip_with_key_change(self):
        """User changes the key mid-round-trip — new key is accepted."""
        old_key = "sk-old-key-abcdefgh"
        new_key = "sk-new-key-zyxwvuts"

        stored = _make_workflow_def([_qa_node(api_key=old_key)])
        fetched = mask_workflow_definition(stored)

        # User replaces the masked key with a new one
        fetched["nodes"][0]["data"]["qa_api_key"] = new_key

        merged = merge_workflow_api_keys(fetched, stored)
        assert merged["nodes"][0]["data"]["qa_api_key"] == new_key
