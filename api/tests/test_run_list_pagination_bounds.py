"""Pagination bounds for the workflow-run and campaign-run list endpoints.

Regression for issue #553: `limit=0` raised an unhandled ZeroDivisionError
(HTTP 500) in the `total_pages` computation, and negative `limit`/`page`
produced nonsensical pagination. Both endpoints now validate the params
(`limit` in [1, 100], `page` >= 1), matching the sibling list endpoints.
"""

import pytest


async def _make_user(db_session, slug: str):
    user, _ = await db_session.get_or_create_user_by_provider_id(f"{slug}_user")
    org, _ = await db_session.get_or_create_organization_by_provider_id(
        f"{slug}_org", user.id
    )
    await db_session.update_user_selected_organization(user.id, org.id)
    return await db_session.get_user_by_id(user.id)


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/workflow/1/runs",
        "/api/v1/campaign/1/runs",
    ],
)
@pytest.mark.parametrize("query", ["limit=0", "limit=-5", "limit=101", "page=0"])
async def test_run_list_rejects_out_of_range_pagination(
    test_client_factory, db_session, path, query
):
    """Out-of-range limit/page is a 422 validation error, never a 500."""
    user = await _make_user(db_session, "paginate_bounds")

    async with test_client_factory(user) as client:
        response = await client.get(f"{path}?{query}")

    assert response.status_code == 422, (
        f"{path}?{query} expected 422, got {response.status_code}: {response.text}"
    )
