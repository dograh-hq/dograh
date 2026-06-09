from pydantic import BaseModel


class OrganizationPreferences(BaseModel):
    test_phone_number: str | None = None
    timezone: str | None = None
