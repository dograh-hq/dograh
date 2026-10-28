"""Tests for position reconciliation after the LLM save round-trip."""

from __future__ import annotations

from api.services.workflow.layout import reconcile_positions


def _node(
    id: str,
    type: str,
    *,
    name: str | None = None,
    x: float = 0.0,
    y: float = 0.0,
) -> dict:
    data: dict = {}
    if name is not None:
        data["name"] = name
    return {"id": id, "type": type, "position": {"x": x, "y": y}, "data": data}


def _edge(src: str, tgt: str) -> dict:
    return {
        "id": f"{src}-{tgt}",
        "source": src,
        "target": tgt,
        "data": {"label": "x", "condition": "y"},
    }


def test_named_match_preserves_position():
    previous = {
        "nodes": [_node("99", "startCall", name="greeting", x=100, y=200)],
        "edges": [],
    }
    new = {
        "nodes": [_node("1", "startCall", name="greeting")],
        "edges": [],
    }
    out = reconcile_positions(new, previous)
    assert out["nodes"][0]["position"] == {"x": 100, "y": 200}


def test_existing_node_at_origin_is_not_repositioned_on_update():
    previous = {
        "nodes": [
            _node("10", "startCall", name="greeting", x=100, y=200),
            _node("20", "agentNode", name="reply", x=0, y=0),
        ],
        "edges": [_edge("10", "20")],
    }
    new = {
        "nodes": [
            _node("1", "startCall", name="greeting"),
            _node("2", "agentNode", name="reply"),
        ],
        "edges": [_edge("1", "2")],
    }
    out = reconcile_positions(new, previous)
    assert [node["position"] for node in out["nodes"]] == [
        {"x": 100, "y": 200},
        {"x": 0, "y": 0},
    ]


def test_unnamed_match_by_type_ordering():
    previous = {
        "nodes": [
            _node("7", "agentNode", x=-648, y=-158),
            _node("8", "agentNode", x=500, y=-100),
        ],
        "edges": [],
    }
    new = {
        "nodes": [
            _node("1", "agentNode"),
            _node("2", "agentNode"),
        ],
        "edges": [],
    }
    out = reconcile_positions(new, previous)
    assert out["nodes"][0]["position"] == {"x": -648, "y": -158}
    assert out["nodes"][1]["position"] == {"x": 500, "y": -100}


def test_new_node_placed_relative_to_incoming_neighbor():
    previous = {
        "nodes": [_node("99", "startCall", name="greeting", x=100, y=200)],
        "edges": [],
    }
    new = {
        "nodes": [
            _node("1", "startCall", name="greeting"),
            _node("2", "agentNode", name="new_node"),
        ],
        "edges": [_edge("1", "2")],
    }
    out = reconcile_positions(new, previous)
    # Start call keeps its previous position.
    assert out["nodes"][0]["position"] == {"x": 100, "y": 200}
    # New node offset from its incoming neighbor.
    assert out["nodes"][1]["position"] == {"x": 500, "y": 400}


def test_orphan_new_node_stays_at_origin():
    new = {
        "nodes": [_node("1", "agentNode", name="orphan")],
        "edges": [],
    }
    out = reconcile_positions(new, None)
    assert out["nodes"][0]["position"] == {"x": 0.0, "y": 0.0}


def test_named_wins_over_unnamed_ordering():
    previous = {
        "nodes": [
            _node("7", "agentNode", x=-648, y=-158),  # unnamed
            _node("8", "agentNode", name="qualify", x=900, y=900),
        ],
        "edges": [],
    }
    new = {
        "nodes": [
            _node("1", "agentNode", name="qualify"),  # matches named
            _node("2", "agentNode"),  # falls to unnamed queue
        ],
        "edges": [],
    }
    out = reconcile_positions(new, previous)
    assert out["nodes"][0]["position"] == {"x": 900, "y": 900}
    assert out["nodes"][1]["position"] == {"x": -648, "y": -158}


def test_no_previous_keeps_origin_for_all_matched_positions():
    new = {
        "nodes": [
            _node("1", "startCall", name="greeting"),
            _node("2", "agentNode", name="reply"),
        ],
        "edges": [_edge("1", "2")],
    }
    out = reconcile_positions(new, None)
    # No previous → first node stays at origin (no incoming), second
    # node placed relative to its incoming neighbor at origin.
    assert out["nodes"][0]["position"] == {"x": 0.0, "y": 0.0}
    assert out["nodes"][1]["position"] == {"x": 400.0, "y": 200.0}


async def test_layout_route_sends_lean_payload_and_maps_bridge_outages(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from httpx import ASGITransport, AsyncClient

    from api.app import app
    from api.mcp_server.ts_bridge import TsBridgeError
    from api.services.auth.depends import get_user

    tidy = AsyncMock(return_value=[{"id": "1", "x": 175, "y": 60}])
    monkeypatch.setattr("api.routes.workflow.tidy_positions", tidy)

    async def mock_get_user():
        return SimpleNamespace(id=1, selected_organization_id=7)

    app.dependency_overrides[get_user] = mock_get_user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Whatever the canvas sends, only ids, types and endpoints go on.
            response = await client.post(
                "/api/v1/workflow/layout",
                json={
                    "nodes": [
                        {
                            "id": "1",
                            "type": "startCall",
                            "position": {"x": 3, "y": 4},
                            "data": {"name": "Hello", "prompt": "..."},
                        }
                    ],
                    "edges": [{"id": "1-2", "source": "1", "target": "2", "data": {}}],
                },
            )
            assert response.status_code == 200, response.text
            assert response.json() == {
                "positions": [{"id": "1", "x": 175.0, "y": 60.0}]
            }
            tidy.assert_awaited_once_with(
                [{"id": "1", "type": "startCall"}],
                [{"source": "1", "target": "2"}],
                rankdir="TB",
            )

            tidy.side_effect = TsBridgeError("node exited 1")
            response = await client.post(
                "/api/v1/workflow/layout", json={"nodes": [], "edges": []}
            )
            assert response.status_code == 503
    finally:
        app.dependency_overrides.pop(get_user, None)
