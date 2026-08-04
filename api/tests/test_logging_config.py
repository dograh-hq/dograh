from types import SimpleNamespace

from pipecat.utils.run_context import run_id_var

from api.logging_config import enrich_log_record


def _record(level: int, extra: dict | None = None) -> dict:
    return {
        "level": SimpleNamespace(no=level),
        "extra": dict(extra or {}),
    }


def test_error_without_classification_gets_operator_fallback():
    token = run_id_var.set("2250")
    try:
        record = _record(40)
        enrich_log_record(record)
    finally:
        run_id_var.reset(token)

    assert record["extra"] == {
        "run_id": "2250",
        "error_owner": "operator",
        "error_type": "system_error",
        "error_source": "platform",
        "error_code": "unclassified-error",
        "classified": True,
        "classification_mode": "fallback",
    }


def test_explicit_error_classification_is_preserved():
    record = _record(
        40,
        {
            "error_owner": "user",
            "error_type": "config_error",
            "error_source": "stt",
            "error_code": "deepgram-401",
            "classified": False,
        },
    )

    enrich_log_record(record)

    assert record["extra"]["error_owner"] == "user"
    assert record["extra"]["error_type"] == "config_error"
    assert record["extra"]["error_source"] == "stt"
    assert record["extra"]["error_code"] == "deepgram-401"
    assert record["extra"]["classified"] is True
    assert record["extra"]["classification_mode"] == "explicit"


def test_partial_error_classification_falls_back_as_a_unit():
    record = _record(40, {"error_owner": "user", "error_type": "config_error"})

    enrich_log_record(record)

    assert record["extra"]["error_owner"] == "operator"
    assert record["extra"]["error_type"] == "system_error"
    assert record["extra"]["error_source"] == "platform"
    assert record["extra"]["error_code"] == "unclassified-error"
    assert record["extra"]["classification_mode"] == "fallback"


def test_warning_only_gets_run_context():
    record = _record(30)

    enrich_log_record(record)

    assert record["extra"] == {"run_id": None}
