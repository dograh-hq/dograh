"""bootstrap_default_model_configuration against the application's own sessions.

The shared ``db_session`` fixture sets ``expire_on_commit=False``; the
application's sessions (``BaseDBClient``) keep the default, which expires every
loaded attribute on commit. Reading one afterwards raised MissingGreenlet, so
every new organization's first bootstrap failed after persisting its model
configuration and only finished on a later request.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.db import db_client
from api.db.model_connection_client import DEFAULT_KEY
from api.db.models import OrganizationModel
from api.services.configuration.model_connections import (
    dograh_model_configuration_spec,
)


@pytest.fixture
async def app_sessions(setup_test_database):
    engine = create_async_engine(setup_test_database)
    # Constructed exactly as in BaseDBClient.
    factory = async_sessionmaker(bind=engine)
    old_engine, old_session = db_client.engine, db_client.async_session
    db_client.engine, db_client.async_session = engine, factory
    yield factory
    db_client.engine, db_client.async_session = old_engine, old_session
    await engine.dispose()


async def test_bootstrap_returns_the_default_it_wrote(app_sessions):
    async with app_sessions() as session:
        organization = OrganizationModel(provider_id=f"bootstrap-{uuid.uuid4().hex}")
        session.add(organization)
        await session.flush()
        organization_id = organization.id
        await session.commit()

    configuration_uuid = await db_client.bootstrap_default_model_configuration(
        organization_id,
        connection_name="Dograh",
        provider="dograh",
        credentials={"api_key": "mps_sk_test"},
        configuration_name="Organization default",
        configuration_for=dograh_model_configuration_spec,
    )

    state = await db_client.get_configuration_values(organization_id, [DEFAULT_KEY])
    assert str(state[DEFAULT_KEY]) == str(configuration_uuid)
