import os
from typing import Any

import aiohttp


from loguru import logger


class StackAuthUserSearchError(Exception):
    """Raised when Stack Auth user search fails unexpectedly."""


class StackAuthSessionError(Exception):
    """Raised when Stack Auth cannot create an impersonation session."""


class StackAuth:
    def __init__(self):
        pass

    @property
    def project_id(self) -> str | None:
        try:
            from api.constants import STACK_AUTH_PROJECT_ID
            if STACK_AUTH_PROJECT_ID:
                return STACK_AUTH_PROJECT_ID
        except Exception:
            pass
        return (
            os.environ.get("STACK_AUTH_PROJECT_ID")
            or os.environ.get("NEXT_PUBLIC_HEXCLAVE_PROJECT_ID")
            or os.environ.get("HEXCLAVE_PROJECT_ID")
        )

    @property
    def secret_server_key(self) -> str | None:
        try:
            from api.constants import STACK_SECRET_SERVER_KEY
            if STACK_SECRET_SERVER_KEY:
                return STACK_SECRET_SERVER_KEY
        except Exception:
            pass
        return (
            os.environ.get("STACK_SECRET_SERVER_KEY")
            or os.environ.get("HEXCLAVE_SECRET_SERVER_KEY")
        )

    @property
    def api_url(self) -> str:
        val = None
        try:
            from api.constants import STACK_AUTH_API_URL
            val = STACK_AUTH_API_URL
        except Exception:
            pass
        if not val:
            val = (
                os.environ.get("STACK_AUTH_API_URL")
                or os.environ.get("NEXT_PUBLIC_HEXCLAVE_API_URL")
                or os.environ.get("HEXCLAVE_API_URL")
                or "https://api.stack-auth.com"
            )
        return (val or "https://api.stack-auth.com").rstrip("/")

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
            logger.debug("Stack Auth get_user called without access_token")
            return None

        access_token = self._strip_bearer(access_token)
        if not access_token:
            logger.debug("Stack Auth get_user access_token was empty after strip")
            return None

        url = f"{self.api_url}/api/v1/users/me"
        headers = {
            "x-stack-access-type": "server",
            "x-stack-project-id": self.project_id or "",
            "x-stack-secret-server-key": self.secret_server_key or "",
            "x-stack-access-token": access_token,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    res_json = await response.json()
                    if response.status != 200:
                        logger.warning(
                            f"Stack Auth /api/v1/users/me returned {response.status}: {res_json}. "
                            f"(url={url}, project_id={self.project_id})"
                        )
                        return None
                    if "id" in res_json:
                        return res_json
                    else:
                        logger.warning(f"Stack Auth response missing 'id': {res_json}")
                        return None
        except Exception as exc:
            logger.error(f"Error calling Stack Auth /api/v1/users/me: {exc}")
            return None

    async def impersonate(self, stack_user_id: str):
        url = f"{self.api_url}/api/v1/auth/sessions"
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
        url = f"{self.api_url}/api/v1/users"
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
