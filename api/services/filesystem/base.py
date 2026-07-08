from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Protocol


class AsyncReadable(Protocol):
    """Anything exposing ``await .read() -> bytes`` (aiofiles handles, in-memory wrappers)."""

    async def read(self) -> bytes: ...


class _AsyncBytesReader:
    """Async file-like wrapper over in-memory bytes for acreate_file()."""

    def __init__(self, data: bytes):
        self._data = data

    async def read(self) -> bytes:
        return self._data


class BaseFileSystem(ABC):
    """Abstract base class for filesystem operations."""

    @abstractmethod
    async def acreate_file(self, file_path: str, content: AsyncReadable) -> bool:
        """Create a new file with the given content.

        Args:
            file_path: Path where the file should be created
            content: File content readable via ``await content.read()``

        Returns:
            bool: True if file was created successfully, False otherwise
        """
        pass

    async def acreate_file_from_bytes(self, file_path: str, data: bytes) -> bool:
        """Create a file directly from in-memory bytes (no local file needed).

        Args:
            file_path: Path where the file should be created
            data: File content as bytes

        Returns:
            bool: True if file was created successfully, False otherwise
        """
        return await self.acreate_file(file_path, _AsyncBytesReader(data))

    @abstractmethod
    async def aupload_file(self, local_path: str, destination_path: str) -> bool:
        """Upload a file from local path to destination.

        Args:
            local_path: Path to the local file
            destination_path: Path where the file should be uploaded

        Returns:
            bool: True if file was uploaded successfully, False otherwise
        """
        pass

    @abstractmethod
    async def aget_signed_url(
        self,
        file_path: str,
        expiration: int = 3600,
        force_inline: bool = False,
        use_internal_endpoint: bool = False,
    ) -> Optional[str]:
        """Generate a signed URL for temporary access to a file.

        Args:
            file_path: Path to the file
            expiration: URL expiration time in seconds (default: 1 hour)
            force_inline: Force inline display (browser preview vs download)
            use_internal_endpoint: Use internal endpoint (for container-to-container access)

        Returns:
            Optional[str]: Signed URL if successful, None otherwise
        """
        pass

    @abstractmethod
    async def aget_file_metadata(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a file.

        Args:
            file_path: Path to the file

        Returns:
            Optional[Dict[str, Any]]: File metadata if successful, None otherwise
            Contains: size, created_at, modified_at, etag, etc.
        """
        pass

    @abstractmethod
    async def aget_presigned_put_url(
        self,
        file_path: str,
        expiration: int = 900,
        content_type: str = "text/csv",
        max_size: int = 10_485_760,
    ) -> Optional[str]:
        """Generate a presigned PUT URL for direct file upload.

        Args:
            file_path: Path where the file should be uploaded
            expiration: URL expiration time in seconds (default: 15 minutes)
            content_type: MIME type of the file (default: text/csv)
            max_size: Maximum file size in bytes (default: 10MB)

        Returns:
            Optional[str]: Presigned PUT URL if successful, None otherwise
        """
        pass

    @abstractmethod
    async def adownload_file(self, source_path: str, local_path: str) -> bool:
        """Download a file from storage to local path.

        Args:
            source_path: Path to the file in storage
            local_path: Local path where file should be downloaded

        Returns:
            bool: True if file was downloaded successfully, False otherwise
        """
        pass

    @abstractmethod
    async def acopy_file(self, source_path: str, destination_path: str) -> bool:
        """Copy a file within storage (server-side copy).

        Args:
            source_path: Path to the source file
            destination_path: Path for the copied file

        Returns:
            bool: True if file was copied successfully, False otherwise
        """
        pass
