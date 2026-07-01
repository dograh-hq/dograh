"""CSV phone auto-detect + header->variable column mapping."""

from api.services.campaign.source_sync import CampaignSourceSyncService as S


def test_detect_phone_by_header_name():
    hdr = ["Name", "Mobile", "City"]
    rows = [["Amit", "+919812345678", "Delhi"]]
    assert S.detect_phone_column(hdr, rows) == 1


def test_detect_phone_by_value_shape_when_header_cryptic():
    hdr = ["col1", "col2"]
    rows = [["Amit", "+919812345678"], ["Riya", "+919800000001"]]
    assert S.detect_phone_column(hdr, rows) == 1


def test_detect_returns_none_when_no_phone():
    hdr = ["Name", "City"]
    rows = [["Amit", "Delhi"], ["Riya", "Mumbai"]]
    assert S.detect_phone_column(hdr, rows) is None


def test_apply_mapping_renames_phone_and_variables():
    hdr = ["Name", "Mobile", "City"]
    rows = [["Amit", "+919812345678", "Delhi"]]
    eff = S.apply_column_mapping(hdr, rows, {"Name": "customer_name"})
    assert eff == ["customer_name", "phone_number", "city"]


def test_apply_mapping_backward_compatible_with_phone_number_header():
    hdr = ["phone_number", "name"]
    rows = [["+919812345678", "Amit"]]
    assert S.apply_column_mapping(hdr, rows) == ["phone_number", "name"]


def test_validate_auto_detects_phone_without_mapping():
    hdr = ["Name", "Mobile"]
    rows = [["Amit", "+919812345678"], ["Riya", "+919800000001"]]
    res = S.validate_source_data(hdr, rows)
    assert res.is_valid is True
    assert "phone_number" in (res.headers or [])


def test_validate_errors_when_no_phone_column():
    hdr = ["Name", "City"]
    rows = [["Amit", "Delhi"]]
    res = S.validate_source_data(hdr, rows)
    assert res.is_valid is False
    assert "phone" in res.error.message.lower()


def test_validate_rejects_phone_without_country_code():
    hdr = ["Mobile"]
    rows = [["9812345678"]]  # no leading +
    res = S.validate_source_data(hdr, rows)
    assert res.is_valid is False
    assert "+" in res.error.message
