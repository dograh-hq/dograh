"""Customer-safe model failures carried through the builder event stream."""

from fastapi import HTTPException

from api.errors.failure import DograhFailure
from api.errors.mps import MPS_UNAVAILABLE_PUBLIC_MESSAGE


class BuilderModelError(HTTPException):
    def __init__(
        self, failure: DograhFailure, *, upstream_status_code: int | None = None
    ) -> None:
        status_code = 503
        message = MPS_UNAVAILABLE_PUBLIC_MESSAGE
        if failure.code == "dograh-timeout" or upstream_status_code in {408, 504}:
            status_code = 504
        elif upstream_status_code in {401, 403}:
            status_code = upstream_status_code
            message = (
                "Dograh could not authorize agent builder. Check your Dograh "
                "Service Key in /model-configurations and try again."
            )
        elif upstream_status_code == 402:
            status_code = 402
            message = (
                "Not enough Dograh credits to build this agent. Add credits and retry."
            )
        elif upstream_status_code == 429:
            status_code = 429
            message = "Dograh's model service (MPS) is rate limiting requests. Try again later."
        elif upstream_status_code is not None and 400 <= upstream_status_code < 500:
            status_code = upstream_status_code
            message = (
                "Dograh's model service (MPS) rejected the builder request "
                f"(HTTP {upstream_status_code})."
            )
        super().__init__(status_code=status_code, detail=message)
        self.error_code = failure.code
        self.retryable = failure.retryable
        self.upstream_status_code = upstream_status_code

    def event(self) -> dict:
        return {
            "type": "error",
            "message": self.detail,
            "status_code": self.status_code,
            "error_code": self.error_code,
            "retryable": self.retryable,
            "upstream_status_code": self.upstream_status_code,
        }
