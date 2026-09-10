from api.routes import organization
from api.services.configuration.masking import (
    build_sensitive_tree,
    mask_key,
    restore_masked_fields,
)


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


def _credentials_with_non_agent_api(
    agent_password: str = "agent-secret",
    non_agent_password: str = "non-agent-secret",
) -> dict:
    """Full credentials including optional non_agent_api section."""
    return {
        "ari_endpoint": "https://asterisk.example.com",
        "app_name": "dograh",
        "app_password": "ari-secret",
        "external_pbx": {
            "type": "vicidial",
            "agent_api": {
                "url": "https://vici.example.com/agc/api.php",
                "username": "agent-user",
                "password": agent_password,
            },
            "non_agent_api": {
                "url": "https://vici.example.com/vicidial/non_agent_api.php",
                "username": "lead-api-user",
                "password": non_agent_password,
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
    fields_set = {
        "ari_endpoint", "app_name", "app_password",
        "external_pbx", "external_pbx.type",
        "external_pbx.agent_api", "external_pbx.agent_api.url",
        "external_pbx.agent_api.username", "external_pbx.agent_api.password",
    }

    organization.preserve_masked_fields("ari", request, existing, fields_set=fields_set)

    assert request["app_password"] == "ari-secret"
    assert request["external_pbx"]["agent_api"]["password"] == "agent-secret"


def test_nested_secrets_not_restored_when_external_pbx_explicitly_none():
    """When external_pbx is explicitly set to None (in fields_set), nested secrets should NOT be restored."""
    existing = _credentials_with_non_agent_api()
    request = {
        "ari_endpoint": "https://asterisk.example.com",
        "app_name": "dograh",
        "app_password": "ari-secret",
        "external_pbx": None,  # Explicitly cleared
    }

    # external_pbx is in fields_set AND its value is None → explicit clear
    fields_set = {"external_pbx"}
    organization.preserve_masked_fields("ari", request, existing, fields_set=fields_set)

    # external_pbx should remain None, not be recreated with secrets
    assert request["external_pbx"] is None


def test_nested_secrets_preserved_when_external_pbx_omitted():
    """When external_pbx is omitted entirely, nested secrets and non-sensitive fields should be preserved."""
    existing = _credentials_with_non_agent_api()
    request = {
        "ari_endpoint": "https://asterisk.example.com",
        "app_name": "dograh",
        "app_password": "ari-secret",
        # external_pbx is omitted entirely
    }

    fields_set = {"ari_endpoint", "app_name", "app_password"}
    organization.preserve_masked_fields("ari", request, existing, fields_set=fields_set)

    # external_pbx should be preserved since it was omitted (original behavior)
    assert request["external_pbx"]["type"] == "vicidial"
    assert request["external_pbx"]["agent_api"]["url"] == "https://vici.example.com/agc/api.php"
    assert request["external_pbx"]["agent_api"]["username"] == "agent-user"
    assert request["external_pbx"]["agent_api"]["password"] == "agent-secret"
    assert request["external_pbx"]["non_agent_api"]["url"] == "https://vici.example.com/vicidial/non_agent_api.php"
    assert request["external_pbx"]["non_agent_api"]["username"] == "lead-api-user"
    assert request["external_pbx"]["non_agent_api"]["password"] == "non-agent-secret"


def test_nested_secrets_not_restored_when_non_agent_api_explicitly_none():
    """When external_pbx.non_agent_api is explicitly set to None (in fields_set), its secrets should NOT be restored."""
    existing = _credentials_with_non_agent_api()
    request = {
        "ari_endpoint": "https://asterisk.example.com",
        "app_name": "dograh",
        "app_password": "ari-secret",
        "external_pbx": {
            "type": "vicidial",
            "agent_api": {
                "url": "https://vici.example.com/agc/api.php",
                "username": "agent-user",
                "password": mask_key("agent-secret"),
            },
            "non_agent_api": None,  # Explicitly cleared
        },
    }

    # non_agent_api is in fields_set AND its dict value is None → explicit clear
    fields_set = {"external_pbx", "external_pbx.type", "external_pbx.agent_api", "external_pbx.agent_api.url", "external_pbx.agent_api.username", "external_pbx.agent_api.password", "external_pbx.non_agent_api"}
    organization.preserve_masked_fields("ari", request, existing, fields_set=fields_set)

    # non_agent_api should remain None
    assert request["external_pbx"]["non_agent_api"] is None
    # agent_api secrets should still be preserved since that section is present
    assert request["external_pbx"]["agent_api"]["password"] == "agent-secret"


def test_non_agent_api_secrets_preserved_when_sibling_section_omitted_with_fields_set():
    """Production path: external_pbx submitted with agent_api only, non_agent_api omitted.

    This is the scenario that occurs on the real update endpoint when
    model_dump() materialises the omitted non_agent_api as None.  The stored
    non_agent_api credentials must be preserved — ``external_pbx`` being in
    fields_set only means the user submitted the section, NOT that they
    cleared every child.
    """
    existing = _credentials_with_non_agent_api()
    request = {
        "ari_endpoint": "https://asterisk.example.com",
        "app_name": "dograh",
        "app_password": "ari-secret",
        "external_pbx": {
            "type": "vicidial",
            "agent_api": {
                "url": "https://vici.example.com/agc/api.php",
                "username": "agent-user",
                "password": mask_key("agent-secret"),
            },
            "non_agent_api": None,  # materialised by model_dump(), NOT explicitly sent
        },
    }

    # non_agent_api is NOT in fields_set — it was omitted by the client
    fields_set = {
        "external_pbx", "external_pbx.type",
        "external_pbx.agent_api", "external_pbx.agent_api.url",
        "external_pbx.agent_api.username", "external_pbx.agent_api.password",
    }
    organization.preserve_masked_fields("ari", request, existing, fields_set=fields_set)

    # non_agent_api secrets and non-sensitive siblings must be restored (omitted ≠ cleared)
    assert request["external_pbx"]["non_agent_api"]["url"] == "https://vici.example.com/vicidial/non_agent_api.php"
    assert request["external_pbx"]["non_agent_api"]["username"] == "lead-api-user"
    assert request["external_pbx"]["non_agent_api"]["password"] == "non-agent-secret"
    # agent_api secrets should still be restored from mask
    assert request["external_pbx"]["agent_api"]["password"] == "agent-secret"


def test_nested_secrets_preserved_when_non_agent_api_omitted():
    """When external_pbx.non_agent_api is omitted from request, its secrets should be preserved (original behavior)."""
    existing = _credentials_with_non_agent_api()
    request = {
        "ari_endpoint": "https://asterisk.example.com",
        "app_name": "dograh",
        "app_password": "ari-secret",
        "external_pbx": {
            "type": "vicidial",
            "agent_api": {
                "url": "https://vici.example.com/agc/api.php",
                "username": "agent-user",
                "password": mask_key("agent-secret"),
            },
            # non_agent_api is omitted entirely
        },
    }

    fields_set = {
        "ari_endpoint", "app_name", "app_password",
        "external_pbx", "external_pbx.type",
        "external_pbx.agent_api", "external_pbx.agent_api.url",
        "external_pbx.agent_api.username", "external_pbx.agent_api.password",
    }
    organization.preserve_masked_fields("ari", request, existing, fields_set=fields_set)

    # non_agent_api secrets and non-sensitive siblings should be preserved since parent is present and not None
    assert request["external_pbx"]["non_agent_api"]["url"] == "https://vici.example.com/vicidial/non_agent_api.php"
    assert request["external_pbx"]["non_agent_api"]["username"] == "lead-api-user"
    assert request["external_pbx"]["non_agent_api"]["password"] == "non-agent-secret"
    # agent_api secrets should still be preserved since that section is present
    assert request["external_pbx"]["agent_api"]["password"] == "agent-secret"


def test_nested_secrets_preserved_when_parent_present_and_not_none():
    """When external_pbx is present and not None, nested secrets ARE preserved as expected."""
    existing = _credentials_with_non_agent_api()
    request = organization._credentials_for_display("ari", existing)
    fields_set = {
        "ari_endpoint", "app_name", "app_password",
        "external_pbx", "external_pbx.type",
        "external_pbx.agent_api", "external_pbx.agent_api.url",
        "external_pbx.agent_api.username", "external_pbx.agent_api.password",
        "external_pbx.non_agent_api", "external_pbx.non_agent_api.url",
        "external_pbx.non_agent_api.username", "external_pbx.non_agent_api.password",
    }

    organization.preserve_masked_fields("ari", request, existing, fields_set=fields_set)

    # All nested secrets should be restored
    assert request["app_password"] == "ari-secret"
    assert request["external_pbx"]["agent_api"]["password"] == "agent-secret"
    assert request["external_pbx"]["non_agent_api"]["password"] == "non-agent-secret"


def test_non_agent_api_secrets_preserved_when_parent_present():
    """When external_pbx.non_agent_api is present, its secrets are preserved even if masked."""
    existing = _credentials_with_non_agent_api()
    request = {
        "ari_endpoint": "https://asterisk.example.com",
        "app_name": "dograh",
        "app_password": "ari-secret",
        "external_pbx": {
            "type": "vicidial",
            "agent_api": {
                "url": "https://vici.example.com/agc/api.php",
                "username": "agent-user",
                "password": mask_key("agent-secret"),
            },
            "non_agent_api": {
                "url": "https://vici.example.com/vicidial/non_agent_api.php",
                "username": "lead-api-user",
                "password": mask_key("non-agent-secret"),
            },
        },
    }

    fields_set = {
        "ari_endpoint", "app_name", "app_password",
        "external_pbx", "external_pbx.type",
        "external_pbx.agent_api", "external_pbx.agent_api.url",
        "external_pbx.agent_api.username", "external_pbx.agent_api.password",
        "external_pbx.non_agent_api", "external_pbx.non_agent_api.url",
        "external_pbx.non_agent_api.username", "external_pbx.non_agent_api.password",
    }
    organization.preserve_masked_fields("ari", request, existing, fields_set=fields_set)

    # Both nested secrets should be restored
    assert request["external_pbx"]["agent_api"]["password"] == "agent-secret"
    assert request["external_pbx"]["non_agent_api"]["password"] == "non-agent-secret"


def test_nested_secrets_not_restored_when_parent_explicitly_none():
    """When parent is explicitly None, nested secrets should NOT be restored."""
    existing = _credentials_with_non_agent_api()
    request = {
        "ari_endpoint": "https://asterisk.example.com",
        "app_name": "dograh",
        "app_password": "ari-secret",
        "external_pbx": None,  # Explicitly cleared
    }

    fields_set = {"ari_endpoint", "app_name", "app_password", "external_pbx"}
    organization.preserve_masked_fields("ari", request, existing, fields_set=fields_set)

    # external_pbx should remain None
    assert request["external_pbx"] is None


def test_secret_that_is_also_a_section_gets_both_roles():
    """A sensitive path may also be an ancestor of another sensitive path.

    No provider declares that today, but ``sensitive=True`` on a section field
    is a one-line registry change. Both roles have to survive: the path is
    unmasked when the stored value is a scalar, and recursed into when it is a
    section — a tree that kept only one role would write the mask back to the
    database.
    """
    tree = build_sensitive_tree(["a", "a.b"])

    assert tree["a"].secret, "'a' is a secret in its own right"
    assert "b" in tree["a"].children, "'a' is also an ancestor of secret 'a.b'"

    # Stored scalar: 'a' acts as a secret, so the resubmitted mask is replaced.
    request = {"a": mask_key("top-secret")}
    restore_masked_fields(request, {"a": "top-secret"}, {"a"}, ["a", "a.b"])
    assert request["a"] == "top-secret"

    # Stored section: 'a' acts as a section, so the walk descends to 'a.b'.
    request = {"a": {"b": mask_key("nested-secret")}}
    restore_masked_fields(
        request, {"a": {"b": "nested-secret"}}, {"a", "a.b"}, ["a", "a.b"]
    )
    assert request["a"]["b"] == "nested-secret"
