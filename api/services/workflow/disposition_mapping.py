"""Translate Dograh dispositions into an organization's own disposition codes.

``gathered_context.mapped_call_disposition`` is the field every downstream
consumer reads -- outbound webhooks, the ``dispositionCode`` run filter, the
daily/run reports, and the external-PBX lead write-back. Applying the
organization's mapping when that field is *written* is what lets all of them
speak the customer's vocabulary without each growing its own lookup, and what
keeps ``call_disposition`` available beside it as the untranslated record of
what Dograh actually observed.

The mapping lives in organization preferences rather than in code because the
target codes are a property of the customer's dialer or CRM, not of Dograh: a
VICIdial deployment's ``XFER``/``DNC`` catalog is one deployment's convention.
"""

from __future__ import annotations

from api.services.organization_preferences import get_organization_preferences

# The identity mapping. Named so callers can pass it explicitly to mean "this
# organization has no mapping" without constructing a dict per call.
NO_DISPOSITION_MAPPING: dict[str, str] = {}


async def get_disposition_mapping(
    organization_id: int | None,
    db=None,
) -> dict[str, str]:
    """The organization's disposition mapping, or ``{}`` when it has none.

    Returned case-folded on the key so a disposition recorded as ``Do_Not_Call``
    matches an entry configured as ``do_not_call``. Free-text end-call reasons
    come from the model, so their casing is not something the configuring user
    can predict.
    """
    if organization_id is None:
        return NO_DISPOSITION_MAPPING

    preferences = await get_organization_preferences(organization_id, db=db)
    if not preferences.disposition_mapping_enabled:
        return NO_DISPOSITION_MAPPING
    return {
        source.casefold(): target
        for source, target in preferences.disposition_mapping.items()
    }


def apply_disposition_mapping(
    mapping: dict[str, str],
    disposition: str | None,
) -> str | None:
    """Return the organization's code for ``disposition``, or it unchanged.

    Passing an unmapped disposition through -- rather than dropping it -- is
    deliberate: a mapping is a set of overrides, so anything not overridden
    keeps behaving exactly as it did before the mapping existed.
    """
    if not disposition:
        return disposition
    return mapping.get(disposition.strip().casefold(), disposition)


async def map_disposition(
    organization_id: int | None,
    disposition: str | None,
    db=None,
) -> str | None:
    """One-shot mapping for callers that translate a single disposition.

    Callers that map more than once for the same call -- the pipeline engine --
    should hold the mapping from :func:`get_disposition_mapping` instead, so a
    disposition can be stamped without an await on the teardown path.
    """
    mapping = await get_disposition_mapping(organization_id, db=db)
    return apply_disposition_mapping(mapping, disposition)
