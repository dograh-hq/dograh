import os
from typing import Any

import aiohttp


class StackAuthUserSearchError(Exception):
    """Raised when Stack Auth user search fails unexpectedly."""


class StackAuthSessionError(Exception):
    """Raised when Stack Auth cannot create an impersonation session."""


class StackAuth:
    def __init__(self):
        self.project_id = os.environ.get("STACK_AUTH_PROJECT_ID")
        self.secret_server_key = os.environ.get("STACK_SECRET_SERVER_KEY")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _strip_bearer(self, access_token: str | None) -> str | None:
        """Remove the leading "Bearer " prefix from the token if present."""
        if not access_token:
            return None
        if access_token.startswith("Bearer "):
            return access_token.split(" ", 1)[1]
        return access_token

    async def get_user(self, access_token: str):
        if not access_token:
            return None

        access_token = self._strip_bearer(access_token)

        url = os.environ.get("STACK_AUTH_API_URL") + "/api/v1/users/me"
        headers = {
            "x-stack-access-type": "server",
            "x-stack-project-id": self.project_id,
            "x-stack-secret-server-key": self.secret_server_key,
            "x-stack-access-token": access_token,
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                response = await response.json()
                if "id" in response:
                    return response
                else:
                    return None

    async def impersonate(self, stack_user_id: str):
        url = os.environ.get("STACK_AUTH_API_URL") + "/api/v1/auth/sessions"
        headers = {
            "x-stack-access-type": "server",
            "x-stack-project-id": self.project_id,
            "x-stack-secret-server-key": self.secret_server_key,
        }

        data = {
            "user_id": stack_user_id,
            "expires_in_millis": 3600000,
            "is_impersonation": True,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=data) as response:
                    if response.status >= 400:
                        raise StackAuthSessionError(
                            "Stack Auth session creation failed"
                        )

                    return await response.json()
        except (aiohttp.ClientError, ValueError) as exc:
            raise StackAuthSessionError("Stack Auth session creation failed") from exc

    async def find_users_by_email(self, email: str) -> list[dict[str, Any]]:
        """Return Stack Auth users whose primary email exactly matches."""
        normalized_email = email.strip().lower()
        url = os.environ.get("STACK_AUTH_API_URL") + "/api/v1/users"
        headers = {
            "x-stack-access-type": "server",
            "x-stack-project-id": self.project_id,
            "x-stack-secret-server-key": self.secret_server_key,
        }
        params = {
            "query": normalized_email,
            "limit": "10",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, params=params) as response:
                    if response.status >= 400:
                        raise StackAuthUserSearchError("Stack Auth user search failed")

                    payload = await response.json()
        except (aiohttp.ClientError, ValueError) as exc:
            raise StackAuthUserSearchError("Stack Auth user search failed") from exc

        users = payload.get("items", []) if isinstance(payload, dict) else []
        if not isinstance(users, list):
            return []

        return [
            user
            for user in users
            if isinstance(user, dict)
            and self._stack_user_has_email(user, normalized_email)
        ]

    def _stack_user_has_email(self, user: dict[str, Any], email: str) -> bool:
        primary_email = user.get("primary_email")
        return isinstance(primary_email, str) and primary_email.lower() == email

    # ------------------------------------------------------------------
    # Team & user management helpers
    # ------------------------------------------------------------------

    # async def create_team(
    #     self,
    #     access_token: str,
    #     display_name: str,
    #     profile_image_url: str | None = None,
    #     client_metadata: dict | None = None,
    # ) -> dict:
    #     """Create a new team for the authenticated user and return the API response."""
    #     token = self._strip_bearer(access_token)
    #     if token is None:
    #         raise ValueError("Access token required to create team")

    #     url = os.environ.get("STACK_AUTH_API_URL") + "/api/v1/teams"
    #     headers = {
    #         "x-stack-access-type": "server",
    #         "x-stack-project-id": self.project_id,
    #         "x-stack-secret-server-key": self.secret_server_key,
    #         "x-stack-access-token": token,
    #         "Content-Type": "application/json",
    #     }

    #     payload: dict = {
    #         "display_name": display_name,
    #         "creator_user_id": "me",
    #     }
    #     if profile_image_url is not None:
    #         payload["profile_image_url"] = profile_image_url
    #     if client_metadata is not None:
    #         payload["client_metadata"] = client_metadata

    #     async with aiohttp.ClientSession() as session:
    #         async with session.post(url, headers=headers, json=payload) as response:
    #             return await response.json()

    # async def update_user(self, access_token: str, data: dict) -> dict:
    #     """Patch the current user with supplied data and return the API response."""
    #     token = self._strip_bearer(access_token)
    #     if token is None:
    #         raise ValueError("Access token required to update user")

    #     url = os.environ.get("STACK_AUTH_API_URL") + "/api/v1/users/me"
    #     headers = {
    #         "x-stack-access-type": "server",
    #         "x-stack-project-id": self.project_id,
    #         "x-stack-secret-server-key": self.secret_server_key,
    #         "x-stack-access-token": token,
    #         "Content-Type": "application/json",
    #     }

    #     async with aiohttp.ClientSession() as session:
    #         async with session.patch(url, headers=headers, json=data) as response:
    #             return await response.json()


stackauth = StackAuth()
