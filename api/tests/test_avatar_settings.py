"""Tests for SpatialReal avatar configuration resolution and session plumbing."""

import pytest

from api.schemas.workflow_configurations import (
    AvatarConfigurationDefaults,
    WorkflowConfigurationDefaults,
)
from api.services.avatar import session_manager
from api.services.avatar.session_manager import (
    FLAG_LAST,
    MSG_TYPE_AUDIO,
    MSG_TYPE_FRAMES,
    AvatarRunSession,
    resolve_avatar_settings,
)


@pytest.fixture
def configured_env(monkeypatch):
    monkeypatch.setattr(session_manager, "SPATIALREAL_APP_ID", "app_test")
    monkeypatch.setattr(session_manager, "SPATIALREAL_API_KEY", "sk-test")
    monkeypatch.setattr(session_manager, "SPATIALREAL_AVATAR_ID", "avatar-env")
    monkeypatch.setattr(session_manager, "SPATIALREAL_MODE", "sdk")


class TestResolveAvatarSettings:
    def test_no_block_follows_env_default(self, configured_env):
        settings = resolve_avatar_settings(None)
        assert settings == {
            "enabled": True,
            "mode": "sdk",
            "avatar_id": "avatar-env",
        }

    def test_no_block_env_off(self, configured_env, monkeypatch):
        monkeypatch.setattr(session_manager, "SPATIALREAL_MODE", "off")
        settings = resolve_avatar_settings(None)
        assert settings["enabled"] is False
        assert settings["mode"] == "off"

    def test_block_disabled_overrides_env(self, configured_env):
        settings = resolve_avatar_settings(
            {"avatar_configuration": {"enabled": False}}
        )
        assert settings["enabled"] is False

    def test_block_enabled_with_overrides(self, configured_env):
        settings = resolve_avatar_settings(
            {
                "avatar_configuration": {
                    "enabled": True,
                    "avatar_id": "avatar-workflow",
                    "mode": "host",
                }
            }
        )
        assert settings == {
            "enabled": True,
            "mode": "host",
            "avatar_id": "avatar-workflow",
        }

    def test_block_enabled_falls_back_to_env_values(self, configured_env):
        settings = resolve_avatar_settings({"avatar_configuration": {"enabled": True}})
        assert settings["avatar_id"] == "avatar-env"
        assert settings["mode"] == "sdk"

    def test_missing_credentials_disable(self, configured_env, monkeypatch):
        monkeypatch.setattr(session_manager, "SPATIALREAL_API_KEY", "")
        settings = resolve_avatar_settings({"avatar_configuration": {"enabled": True}})
        assert settings["enabled"] is False

    def test_missing_avatar_id_disables(self, configured_env, monkeypatch):
        monkeypatch.setattr(session_manager, "SPATIALREAL_AVATAR_ID", "")
        settings = resolve_avatar_settings({"avatar_configuration": {"enabled": True}})
        assert settings["enabled"] is False


class TestAvatarConfigurationSchema:
    def test_defaults(self):
        config = WorkflowConfigurationDefaults()
        assert config.avatar_configuration.enabled is False
        assert config.avatar_configuration.avatar_id is None
        assert config.avatar_configuration.mode is None

    def test_valid_block(self):
        config = WorkflowConfigurationDefaults(
            avatar_configuration={"enabled": True, "avatar_id": "abc", "mode": "host"}
        )
        assert config.avatar_configuration.enabled is True
        assert config.avatar_configuration.mode == "host"

    def test_invalid_mode_rejected(self):
        with pytest.raises(Exception):
            AvatarConfigurationDefaults(mode="livekit")


class TestAvatarRunSessionQueue:
    def test_binary_framing(self):
        session = AvatarRunSession(1, avatar_id="a")
        session._on_encoded_audio("req-1", b"audio")
        session._on_frames(b"frames", True)

        first = session.relay_queue.get_nowait()
        assert first[0] == MSG_TYPE_AUDIO
        assert first[1] == 0
        assert first[2:] == b"audio"

        second = session.relay_queue.get_nowait()
        assert second[0] == MSG_TYPE_FRAMES
        assert second[1] & FLAG_LAST
        assert second[2:] == b"frames"

    def test_queue_drops_oldest_when_full(self):
        session = AvatarRunSession(1, avatar_id="a")
        for i in range(session_manager.RELAY_QUEUE_MAX + 5):
            session._on_frames(bytes([i % 256]), False)
        assert session.relay_queue.qsize() == session_manager.RELAY_QUEUE_MAX
        # Oldest entries were discarded; the newest survives at the tail.
        items = []
        while not session.relay_queue.empty():
            items.append(session.relay_queue.get_nowait())
        assert items[-1][2:] == bytes([(session_manager.RELAY_QUEUE_MAX + 4) % 256])

    def test_failure_enqueues_error_control_message(self):
        session = AvatarRunSession(1, avatar_id="a")
        session._on_error(RuntimeError("boom"))
        assert session.failed is True
        control = session.relay_queue.get_nowait()
        assert isinstance(control, dict)
        assert control["type"] == "avatar-error"

    @pytest.mark.asyncio
    async def test_send_audio_noop_after_failure(self):
        session = AvatarRunSession(1, avatar_id="a")
        session.failed = True
        # Must not raise even though no underlying SDK session exists.
        await session.send_audio(b"pcm")


class TestSessionRegistryCap:
    @pytest.mark.asyncio
    async def test_cap_refuses_new_sessions(self, configured_env, monkeypatch):
        monkeypatch.setattr(session_manager, "SPATIALREAL_MAX_SESSIONS", 0)
        monkeypatch.setattr(session_manager, "_sessions", {})
        # Simulate an active session consuming the (zero) budget is not even
        # needed: cap of 0 refuses immediately.
        result = await session_manager.get_or_create_avatar_session(42)
        assert result is None
