from api.routes import organization


def _credentials(password: str = "agent-secret") -> dict:
    return {
        "ari_endpoint": "https://asterisk.example.com",
        "app_name": "dograh",
        "app_password": "ari-secret",
        "external_pbx": {
            "type": "vicidial",
            "agent_api": {
                "url": "https://vici.example.com/agc/api.php",
                "username": "agent-user",
                "password": password,
            },
        },
    }


def test_nested_external_pbx_secrets_are_masked_without_mutating_source():
    credentials = _credentials()

    masked = organization._credentials_for_display("ari", credentials)

    assert masked["app_password"] != "ari-secret"
    assert masked["external_pbx"]["agent_api"]["password"] != "agent-secret"
    assert credentials["external_pbx"]["agent_api"]["password"] == "agent-secret"


def test_nested_masked_external_pbx_secrets_are_restored_on_update():
    existing = _credentials()
    request = organization._credentials_for_display("ari", existing)

    organization.preserve_masked_fields("ari", request, existing)

    assert request["app_password"] == "ari-secret"
    assert request["external_pbx"]["agent_api"]["password"] == "agent-secret"
