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
    assert _extract_org_id_from_key("recordings/1855/user.wav/nested") is None


def test_known_org_scoped_keys_extract_org_id():
    assert _extract_org_id_from_key("campaigns/42/source.csv") == 42
    assert _extract_org_id_from_key("knowledge_base/42/document/file.pdf") == 42
    assert _extract_org_id_from_key("recordings/42/greeting-123/greeting.wav") == 42
    assert _extract_legacy_workflow_run_id("campaigns/42/source.csv") is None


def test_ambient_noise_keys_extract_org_id():
    # Ambient noise files are stored as ambient-noise/{org_id}/{workflow_id}/{file}
    assert _extract_org_id_from_key("ambient-noise/42/77/greeting.mp3") == 42
    assert _extract_org_id_from_key("ambient-noise/42/77/abc_sound_1.mp3") == 42


def test_ambient_noise_keys_with_invalid_org_segment_are_not_org_scoped():
    assert _extract_org_id_from_key("ambient-noise/not-an-int/77/greeting.mp3") is None


def test_unknown_numeric_prefix_is_not_treated_as_org_scoped():
    assert _extract_org_id_from_key("unknown/42/file.wav") is None
