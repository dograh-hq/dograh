"""Deferred Google authentication shared by text and realtime services.

Constructing a service never discovers credentials or refreshes a token.
Explicit JSON credentials share a bounded process cache by their complete
configuration; file and ADC credentials remain local to the service so changes
to their external identity are not hidden by a process cache.
"""

import asyncio
import json
import threading
from functools import lru_cache

import google.auth
from google.auth.credentials import Credentials
from google.auth.transport.requests import Request
from google.oauth2 import service_account

_SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)


class DeferredGoogleCredentials(Credentials):
    """Load and refresh credentials off-loop, serializing shared token refreshes."""

    def __init__(self, credentials: str | None, credentials_path: str | None):
        """Retain the credential source without loading it on the event loop."""
        super().__init__()
        self._json = credentials
        self._path = credentials_path
        self._delegate: Credentials | None = None
        self._lock = threading.Lock()

    def _refresh_locked(self, request: Request) -> None:
        if self._delegate is None:
            if self._json:
                self._delegate = service_account.Credentials.from_service_account_info(
                    json.loads(self._json), scopes=_SCOPES
                )
            elif self._path:
                self._delegate = service_account.Credentials.from_service_account_file(
                    self._path, scopes=_SCOPES
                )
            else:
                self._delegate, _ = google.auth.default(scopes=_SCOPES)
        self._delegate.refresh(request)
        self.token = self._delegate.token
        self.expiry = self._delegate.expiry
        self._quota_project_id = self._delegate.quota_project_id
        self._universe_domain = self._delegate.universe_domain

    def refresh(self, request: Request) -> None:
        """Honor SDK refreshes, coalescing callers that observed the same token."""
        observed = (self.token, self.expiry)
        with self._lock:
            if self.valid and observed != (self.token, self.expiry):
                return
            self._refresh_locked(request)

    def prepare(self) -> None:
        """Refresh only when needed; called from a worker thread."""
        with self._lock:
            if not self.valid:
                self._refresh_locked(Request())


@lru_cache(maxsize=128)
def _json_credentials(credentials: str) -> DeferredGoogleCredentials:
    return DeferredGoogleCredentials(credentials, None)


def get_google_credentials(
    credentials: str | None, credentials_path: str | None
) -> Credentials:
    """Return credentials without performing disk or network I/O."""
    if credentials:
        return _json_credentials(credentials)
    return DeferredGoogleCredentials(None, credentials_path)


async def prepare_google_credentials(credentials: Credentials | None) -> None:
    """Prepare authentication before a request, including synchronous Live SDK auth."""
    if credentials is None or credentials.valid:
        return
    if isinstance(credentials, DeferredGoogleCredentials):
        await asyncio.to_thread(credentials.prepare)
    else:
        await asyncio.to_thread(credentials.refresh, Request())
