from datetime import datetime, timezone

from loguru import logger
from pydantic import ValidationError
from sqlalchemy.future import select

from api.db.base_client import BaseDBClient
from api.db.models import UserConfigurationModel, UserModel
from api.schemas.user_configuration import UserConfiguration


class UserClient(BaseDBClient):
    async def get_or_create_user_by_provider_id(self, provider_id: str) -> UserModel:
        async with self.async_session() as session:
            # First try to get existing user
            result = await session.execute(
                select(UserModel).where(UserModel.provider_id == provider_id)
            )
            user = result.scalars().first()

            if user is None:
                # Use PostgreSQL's INSERT ... ON CONFLICT DO NOTHING
                # This is atomic and handles race conditions at the database level
                from sqlalchemy.dialects.postgresql import insert

                stmt = insert(UserModel.__table__).values(
                    provider_id=provider_id,
                    created_at=datetime.now(timezone.utc),
                    selected_organization_id=None,  # Will be set later
                    is_superuser=False,  # Default value
                )
                # ON CONFLICT DO NOTHING - if another request already inserted, this becomes a no-op
                stmt = stmt.on_conflict_do_nothing(index_elements=["provider_id"])

                result = await session.execute(stmt)
                await session.commit()

                # Now fetch the user (either the one we just created or the one that existed)
                result = await session.execute(
                    select(UserModel).where(UserModel.provider_id == provider_id)
                )
                user = result.scalars().first()

                if user is None:
                    # This should never happen, but handle it just in case
                    error_msg = (
                        f"Failed to create or fetch user with provider_id {provider_id}"
                    )
                    raise ValueError(error_msg)
        return user

    async def get_user_by_id(self, user_id: int) -> UserModel | None:
        """Fetch a user by their internal ID."""
        async with self.async_session() as session:
            result = await session.execute(
                select(UserModel).where(UserModel.id == user_id)
            )
            return result.scalars().first()

    async def get_user_configurations(self, user_id: int) -> UserConfiguration:
        async with self.async_session() as session:
            result = await session.execute(
                select(UserConfigurationModel).where(
                    UserConfigurationModel.user_id == user_id
                )
            )
            configuration_obj = result.scalars().first()
            if not configuration_obj:
                return UserConfiguration()

            try:
                return UserConfiguration.model_validate(
                    {
                        **configuration_obj.configuration,
                        "last_validated_at": configuration_obj.last_validated_at,
                    }
                )
            except ValidationError as e:
                # If configuration contains an unsupported provider,
                # return a default configuration without failing
                logger.warning(
                    f"Failed to validate user configuration for user {user_id}: {e}. "
                    "Returning default configuration."
                )
                return UserConfiguration()

    async def update_user_configuration(
        self, user_id: int, configuration: UserConfiguration
    ) -> UserConfiguration:
        async with self.async_session() as session:
            result = await session.execute(
                select(UserConfigurationModel).where(
                    UserConfigurationModel.user_id == user_id
                )
            )
            configuration_obj = result.scalars().first()
            if not configuration_obj:
                configuration_obj = UserConfigurationModel(
                    user_id=user_id, configuration=configuration.model_dump()
                )
                session.add(configuration_obj)
            else:
                configuration_obj.configuration = configuration.model_dump()
            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e
            await session.refresh(configuration_obj)
        return UserConfiguration.model_validate(configuration_obj.configuration)

    async def update_user_configuration_last_validated_at(self, user_id: int) -> None:
        async with self.async_session() as session:
            result = await session.execute(
                select(UserConfigurationModel).where(
                    UserConfigurationModel.user_id == user_id
                )
            )
            configuration_obj = result.scalars().first()
            if not configuration_obj:
                raise ValueError(f"User configuration with ID {user_id} not found")
            configuration_obj.last_validated_at = datetime.now()
            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e
            await session.refresh(configuration_obj)

    async def update_user_selected_organization(
        self, user_id: int, organization_id: int
    ) -> None:
        """Update the user's selected organization ID."""
        async with self.async_session() as session:
            from sqlalchemy import update

            # Use a direct UPDATE statement to avoid race conditions
            # This is atomic at the database level
            stmt = (
                update(UserModel)
                .where(UserModel.id == user_id)
                .values(selected_organization_id=organization_id)
            )

            result = await session.execute(stmt)

            if result.rowcount == 0:
                raise ValueError(f"User with ID {user_id} not found")

            await session.commit()
