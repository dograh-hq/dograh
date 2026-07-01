import os

import aiohttp


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

        base = os.environ.get("STACK_AUTH_API_URL") or "https://api.stack-auth.com"
        url = base + "/api/v1/users/me"
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
        # Impersonation is a server-side Stack call; it needs the project id +
        # secret server key (unlike normal login, which the frontend SDK handles).
        if not (self.project_id and self.secret_server_key):
            raise RuntimeError(
                "Stack server auth is not configured — set STACK_AUTH_PROJECT_ID "
                "and STACK_SECRET_SERVER_KEY on the API to enable impersonation."
            )
        base = os.environ.get("STACK_AUTH_API_URL") or "https://api.stack-auth.com"
        url = base + "/api/v1/auth/sessions"
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

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as response:
                body = await response.json()

        if not isinstance(body, dict) or not body.get("refresh_token"):
            keys = list(body.keys()) if isinstance(body, dict) else type(body).__name__
            raise RuntimeError(
                f"Stack impersonate returned no refresh_token (response keys: {keys})"
            )
        return body

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
