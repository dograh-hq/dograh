from typing import Any, BinaryIO, Dict, NoReturn, Optional

from .base import BaseFileSystem


class NullFileSystem(BaseFileSystem):
    """No-op filesystem used when storage is not configured (e.g. tests).

    Every operation raises so that any test that exercises storage fails
    loudly instead of silently succeeding against a stub.
    """

    def _fail(self, op: str) -> NoReturn:
        raise RuntimeError(
            f"NullFileSystem.{op} called — storage is not configured. "
            "Set ENVIRONMENT to a non-test value or inject a real filesystem fixture."
        )

    async def acreate_file(self, file_path: str, content: BinaryIO) -> bool:
        self._fail("acreate_file")

    async def aupload_file(self, local_path: str, destination_path: str) -> bool:
        self._fail("aupload_file")

    async def aget_signed_url(
        self,
        file_path: str,
        expiration: int = 3600,
        force_inline: bool = False,
        use_internal_endpoint: bool = False,
    ) -> Optional[str]:
        self._fail("aget_signed_url")

    async def aget_file_metadata(self, file_path: str) -> Optional[Dict[str, Any]]:
        self._fail("aget_file_metadata")

    async def aget_presigned_put_url(
        self,
        file_path: str,
        expiration: int = 900,
        content_type: str = "text/csv",
        max_size: int = 10_485_760,
    ) -> Optional[str]:
        self._fail("aget_presigned_put_url")

    async def adownload_file(self, source_path: str, local_path: str) -> bool:
        self._fail("adownload_file")

    async def acopy_file(self, source_path: str, destination_path: str) -> bool:
        self._fail("acopy_file")
