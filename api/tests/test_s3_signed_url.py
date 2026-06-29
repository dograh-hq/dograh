from api.routes.s3_signed_url import (
    _extract_legacy_workflow_run_id,
    _extract_org_id_from_key,
)


def test_split_recording_keys_are_workflow_run_artifacts_not_org_keys():
    assert _extract_legacy_workflow_run_id("recordings/1855/user.wav") == 1855
    assert _extract_legacy_workflow_run_id("recordings/1855/bot.wav") == 1855

    assert _extract_org_id_from_key("recordings/1855/user.wav") is None
    assert _extract_org_id_from_key("recordings/1855/bot.wav") is None


def test_legacy_recording_keys_do_not_fall_through_to_org_scoped_auth():
    assert _extract_legacy_workflow_run_id("recordings/1855.wav") == 1855
    assert _extract_legacy_workflow_run_id("recordings/1855/other.wav") is None

    assert _extract_org_id_from_key("recordings/1855.wav") is None
    assert _extract_org_id_from_key("recordings/1855/other.wav") is None


def test_known_org_scoped_keys_extract_org_id():
    assert _extract_org_id_from_key("campaigns/42/source.csv") == 42
    assert _extract_org_id_from_key("knowledge_base/42/document/file.pdf") == 42
    assert _extract_legacy_workflow_run_id("campaigns/42/source.csv") is None


def test_unknown_numeric_prefix_is_not_treated_as_org_scoped():
    assert _extract_org_id_from_key("unknown/42/file.wav") is None
