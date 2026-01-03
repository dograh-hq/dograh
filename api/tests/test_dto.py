import pytest

from api.services.workflow.dto import ReactFlowDTO


@pytest.mark.asyncio
async def test_dto():
    # assert no exceptions are raised
    with open("tests/definitions/rf-1.json", "r") as f:
        dto = ReactFlowDTO.model_validate_json(f.read())
    assert dto is not None
