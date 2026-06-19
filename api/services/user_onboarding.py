from loguru import logger
from pydantic import ValidationError

from api.db import db_client
from api.enums import UserConfigurationKey
from api.schemas.onboarding_state import OnboardingState, OnboardingStateUpdate


async def get_onboarding_state(user_id: int) -> OnboardingState:
    value = await db_client.get_user_configuration_value(
        user_id, UserConfigurationKey.ONBOARDING.value
    )
    return _parse_state(value, user_id)


async def update_onboarding_state(
    user_id: int, update: OnboardingStateUpdate
) -> OnboardingState:
    state = update.apply_to(await get_onboarding_state(user_id))
    await db_client.upsert_user_configuration_value(
        user_id,
        UserConfigurationKey.ONBOARDING.value,
        state.model_dump(mode="json", exclude_none=True),
    )
    return state


def _parse_state(value, user_id: int) -> OnboardingState:
    if not value or not isinstance(value, dict):
        return OnboardingState()
    try:
        return OnboardingState.model_validate(value)
    except ValidationError as exc:
        logger.warning(
            f"Invalid onboarding state for user {user_id}: {exc}. Returning defaults."
        )
        return OnboardingState()
