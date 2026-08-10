"""Auto-deactivation and log-throttling policy for ARI connections.

These cover the accounting only — no sockets and no database. The point of the
policy is that a permanently broken customer PBX stops being retried (and stops
emitting one ERROR per attempt) without an operator intervening.
"""

from types import SimpleNamespace

import pytest

from api.errors.failure import DograhFailure, ErrorSource, ErrorType
from api.services.telephony import ari_manager


class _FakeDBClient:
    def __init__(self):
        self.deactivated = []
        self.reactivated = []
        self.rows = []

    async def list_active_telephony_configurations_by_provider(self, provider):
        return self.rows

    async def set_telephony_configuration_inactive(
        self, config_id, organization_id, reason
    ):
        self.deactivated.append((config_id, organization_id, reason))
        return True

    async def set_telephony_configuration_active(self, config_id, organization_id):
        self.reactivated.append((config_id, organization_id))
        return True


@pytest.fixture
def clock(monkeypatch):
    """Replace the module's ``time`` reference so only ari_manager is affected."""
    now = [1000.0]
    monkeypatch.setattr(ari_manager, "time", SimpleNamespace(monotonic=lambda: now[0]))
    return now


@pytest.fixture
def db(monkeypatch):
    fake = _FakeDBClient()
    monkeypatch.setattr(ari_manager, "db_client", fake)
    return fake


@pytest.fixture
def logged(monkeypatch):
    captured = []
    monkeypatch.setattr(
        ari_manager,
        "log_failure",
        lambda failure, **context: captured.append((failure, context)),
    )
    return captured


def _connection() -> ari_manager.ARIConnection:
    return ari_manager.ARIConnection(
        organization_id=42,
        telephony_configuration_id=7,
        ari_endpoint="http://pbx.example.com:8088",
        app_name="dograh",
        app_password="secret",
        ws_client_name="dograh_ws",
    )


def _permanent_failure() -> DograhFailure:
    return DograhFailure(
        source=ErrorSource.TELEPHONY,
        type=ErrorType.CONFIG_ERROR,
        code="ari-401",
        internal_message="server rejected WebSocket connection: HTTP 401",
        external_message="Check your telephony configuration and credentials.",
        provider="ari",
        error_owner="user",
        retryable=False,
    )


def _transient_failure() -> DograhFailure:
    return DograhFailure(
        source=ErrorSource.TELEPHONY,
        type=ErrorType.PROVIDER_ERROR,
        code="ari-connection",
        internal_message="ConnectionClosed: 1006",
        external_message="The external telephony service is temporarily unavailable.",
        provider="ari",
        error_owner="user",
        retryable=True,
    )


@pytest.mark.asyncio
async def test_permanent_failure_deactivates_at_threshold(clock, db, logged):
    conn = _connection()

    for _ in range(ari_manager._PERMANENT_FAILURE_THRESHOLD - 1):
        await conn._record_failure(_permanent_failure())
    assert db.deactivated == []
    assert conn._running is False  # never started; deactivation must not flip it

    await conn._record_failure(_permanent_failure())

    assert len(db.deactivated) == 1
    config_id, organization_id, reason = db.deactivated[0]
    assert (config_id, organization_id) == (7, 42)
    assert reason.startswith("ari-401:")


@pytest.mark.asyncio
async def test_transient_failure_waits_for_the_full_window(clock, db, logged):
    conn = _connection()

    # Far more attempts than the permanent threshold, but still inside the window.
    for _ in range(20):
        await conn._record_failure(_transient_failure())
        clock[0] += 60
    assert db.deactivated == []

    clock[0] += ari_manager._TRANSIENT_FAILURE_WINDOW
    await conn._record_failure(_transient_failure())

    assert len(db.deactivated) == 1
    assert db.deactivated[0][2].startswith("ari-connection:")


@pytest.mark.asyncio
async def test_repeated_identical_failures_are_logged_once_per_interval(
    clock, db, logged
):
    conn = _connection()

    for _ in range(4):
        await conn._record_failure(_permanent_failure())
    assert len(logged) == 1

    # A different failure code is always worth a line.
    await conn._record_failure(_transient_failure())
    assert len(logged) == 2

    # Same code again, still inside the interval.
    await conn._record_failure(_transient_failure())
    assert len(logged) == 2

    clock[0] += ari_manager._FAILURE_LOG_INTERVAL
    await conn._record_failure(_transient_failure())
    assert len(logged) == 3


@pytest.mark.asyncio
async def test_stable_connection_clears_the_streak_without_touching_the_flag(
    clock, db, logged
):
    conn = _connection()
    conn._consecutive_failures = 3
    conn._failing_since = clock[0]
    conn._reconnect_delay = 128

    await conn._note_stable_connection()

    assert conn._consecutive_failures == 0
    assert conn._failing_since is None
    assert conn._reconnect_delay == 1
    # Deactivation is one-way — a worker must never re-enable a row itself.
    assert db.reactivated == []


@pytest.mark.asyncio
async def test_stable_connection_resets_the_permanent_streak(clock, db, logged):
    conn = _connection()

    for _ in range(ari_manager._PERMANENT_FAILURE_THRESHOLD - 1):
        await conn._record_failure(_permanent_failure())
    await conn._note_stable_connection()

    # The streak restarts, so one more failure must not trip the threshold.
    await conn._record_failure(_permanent_failure())
    assert db.deactivated == []


@pytest.mark.asyncio
async def test_failed_deactivation_write_keeps_the_connection_running(
    clock, logged, monkeypatch
):
    class _BrokenDBClient(_FakeDBClient):
        async def set_telephony_configuration_inactive(self, *_args, **_kwargs):
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(ari_manager, "db_client", _BrokenDBClient())
    conn = _connection()
    conn._running = True

    for _ in range(ari_manager._PERMANENT_FAILURE_THRESHOLD):
        await conn._record_failure(_permanent_failure())

    # Parking failed, so the loop must keep retrying rather than exit silently.
    assert conn._running is True


@pytest.mark.asyncio
async def test_immediate_clean_close_is_accounted_as_a_failure(
    clock, db, logged, monkeypatch
):
    """A normal close raises nothing, so the loop must notice it explicitly."""
    conn = _connection()
    conn._running = True

    async def _connect_and_listen():
        # Accept, then close cleanly well inside the stability threshold.
        clock[0] += 1
        conn._last_connection_stable = False
        if conn._consecutive_failures >= ari_manager._PERMANENT_FAILURE_THRESHOLD:
            conn._running = False

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(conn, "_connect_and_listen", _connect_and_listen)
    monkeypatch.setattr(ari_manager.asyncio, "sleep", _no_sleep)

    await conn._connection_loop()

    assert conn._consecutive_failures > 0
    assert logged[0][0].code == "ari-closed-immediately"
    # Backoff must have grown rather than spinning at the initial delay.
    assert conn._reconnect_delay > 1


def _row(config_id: int, ws_client_name: str = "dograh_ws") -> SimpleNamespace:
    return SimpleNamespace(
        id=config_id,
        organization_id=100 + config_id,
        credentials={
            "ari_endpoint": "http://pbx.example.com:8088",
            "app_name": "dograh",
            "app_password": "secret",
            "ws_client_name": ws_client_name,
        },
    )


@pytest.mark.asyncio
async def test_missing_ws_client_name_parks_the_config(clock, db, logged):
    """The socket would connect, but externalMedia audio can never be set up."""
    db.rows = [_row(1, ws_client_name=""), _row(2)]
    manager = ari_manager.ARIManager()

    configs = await manager._load_ari_configs()

    # The broken row is dropped from the active set; the healthy one survives.
    assert [c["telephony_configuration_id"] for c in configs] == [2]
    assert len(db.deactivated) == 1
    config_id, organization_id, reason = db.deactivated[0]
    assert (config_id, organization_id) == (1, 101)
    assert reason.startswith("ari-missing-ws-client-name:")
    assert logged[0][0].code == "ari-missing-ws-client-name"


@pytest.mark.asyncio
async def test_missing_ws_client_name_parks_on_the_first_look(clock, db, logged):
    """No streak to build: re-reading the row can only reach the same verdict."""
    db.rows = [_row(1, ws_client_name="")]
    manager = ari_manager.ARIManager()

    await manager._load_ari_configs()

    assert len(db.deactivated) == 1


@pytest.mark.asyncio
async def test_failed_park_write_leaves_the_row_for_the_next_refresh(
    clock, logged, monkeypatch
):
    class _BrokenDBClient(_FakeDBClient):
        async def set_telephony_configuration_inactive(self, *_args, **_kwargs):
            raise RuntimeError("database unavailable")

    broken = _BrokenDBClient()
    broken.rows = [_row(1, ws_client_name="")]
    monkeypatch.setattr(ari_manager, "db_client", broken)
    manager = ari_manager.ARIManager()

    # Still excluded, so no half-working connection is started meanwhile, and
    # nothing was recorded — the row returns next refresh and the write retries.
    assert await manager._load_ari_configs() == []
    assert broken.deactivated == []


def test_validation_failures_are_throttled_per_config(clock):
    manager = ari_manager.ARIManager()

    assert manager._should_log_validation_failure(1, "ari-missing-ws-client-name")
    assert not manager._should_log_validation_failure(1, "ari-missing-ws-client-name")

    # A different config is tracked independently.
    assert manager._should_log_validation_failure(2, "ari-missing-ws-client-name")

    # A different code for the same config is worth a line.
    assert manager._should_log_validation_failure(1, "ari-incomplete-config")

    clock[0] += ari_manager._FAILURE_LOG_INTERVAL
    assert manager._should_log_validation_failure(1, "ari-incomplete-config")
