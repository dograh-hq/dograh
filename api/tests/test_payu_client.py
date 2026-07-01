"""PayU hash correctness — the security boundary. Exact-format assertions so a
wrong pipe count / field order can't slip through, plus a verify round-trip."""

from api.services.billing import payu_client


def _creds(monkeypatch, key="XxWLV8", salt="SALT123"):
    monkeypatch.setattr(payu_client, "PAYU_MERCHANT_KEY", key)
    monkeypatch.setattr(payu_client, "PAYU_MERCHANT_SALT", salt)


def test_is_configured(monkeypatch):
    _creds(monkeypatch)
    assert payu_client.is_configured() is True
    monkeypatch.setattr(payu_client, "PAYU_MERCHANT_SALT", "")
    assert payu_client.is_configured() is False


def test_payment_url_test_vs_live(monkeypatch):
    monkeypatch.setattr(payu_client, "PAYU_MODE", "test")
    assert payu_client.payment_url() == "https://test.payu.in/_payment"
    monkeypatch.setattr(payu_client, "PAYU_MODE", "live")
    assert payu_client.payment_url() == "https://secure.payu.in/_payment"


def test_request_hash_string_exact_format_empty_udfs(monkeypatch):
    _creds(monkeypatch)
    s = payu_client.request_hash_string(
        txnid="t1", amount="2399.00", productinfo="Starter",
        firstname="Amit", email="a@x.test",
    )
    parts = s.split("|")
    # key|txnid|amount|productinfo|firstname|email + 10 empty (5 udf + 5) + SALT
    assert len(parts) == 17
    assert parts[:6] == ["XxWLV8", "t1", "2399.00", "Starter", "Amit", "a@x.test"]
    assert parts[6:16] == [""] * 10
    assert parts[16] == "SALT123"


def test_request_hash_string_places_udfs(monkeypatch):
    _creds(monkeypatch)
    s = payu_client.request_hash_string(
        txnid="t1", amount="10.00", productinfo="p", firstname="f", email="e",
        udf1="org4", udf2="scale",
    )
    parts = s.split("|")
    assert parts[6] == "org4"
    assert parts[7] == "scale"
    assert parts[8:16] == [""] * 8
    assert parts[16] == "SALT123"


def test_response_hash_string_exact_reverse_format(monkeypatch):
    _creds(monkeypatch)
    params = {
        "status": "success", "txnid": "t1", "amount": "2399.00",
        "productinfo": "Starter", "firstname": "Amit", "email": "a@x.test",
    }
    parts = payu_client.response_hash_string(params).split("|")
    # SALT|status + 5 empty + udf5..udf1 + email|firstname|productinfo|amount|txnid|key
    assert len(parts) == 18
    assert parts[0] == "SALT123"
    assert parts[1] == "success"
    assert parts[2:7] == [""] * 5
    assert parts[7:12] == [""] * 5  # udf5..udf1 empty
    assert parts[12:17] == ["a@x.test", "Amit", "Starter", "2399.00", "t1"]
    assert parts[17] == "XxWLV8"


def test_response_hash_string_prepends_additional_charges(monkeypatch):
    _creds(monkeypatch)
    s = payu_client.response_hash_string(
        {"status": "success", "txnid": "t1", "amount": "1", "additionalCharges": "10.00"}
    )
    assert s.startswith("10.00|SALT123|success|")


def test_verify_response_hash_roundtrip_and_tamper(monkeypatch):
    _creds(monkeypatch)
    params = {
        "status": "success", "txnid": "t1", "amount": "2399.00",
        "productinfo": "Starter", "firstname": "Amit", "email": "a@x.test",
    }
    params["hash"] = payu_client._sha512(payu_client.response_hash_string(params))
    assert payu_client.verify_response_hash(params) is True

    # tampered amount -> hash no longer matches
    bad = dict(params, amount="1.00")
    assert payu_client.verify_response_hash(bad) is False

    # garbage / missing hash -> rejected
    assert payu_client.verify_response_hash(dict(params, hash="deadbeef")) is False
    assert payu_client.verify_response_hash({k: v for k, v in params.items() if k != "hash"}) is False


def test_build_payment_params_includes_hash_and_core_fields(monkeypatch):
    _creds(monkeypatch)
    p = payu_client.build_payment_params(
        txnid="t1", amount="2399.00", productinfo="Starter", firstname="Amit",
        email="a@x.test", phone="9999999999",
        surl="https://api.auto4you.in/api/v1/billing/payu/callback",
        furl="https://api.auto4you.in/api/v1/billing/payu/callback",
    )
    assert p["key"] == "XxWLV8"
    assert p["txnid"] == "t1"
    assert p["amount"] == "2399.00"
    assert p["phone"] == "9999999999"
    assert p["surl"].endswith("/billing/payu/callback")
    # hash matches an independent recompute of the documented request string
    assert p["hash"] == payu_client._sha512(
        payu_client.request_hash_string(
            txnid="t1", amount="2399.00", productinfo="Starter",
            firstname="Amit", email="a@x.test",
        )
    )
    assert "salt" not in {k.lower() for k in p}  # SALT never leaves the server
