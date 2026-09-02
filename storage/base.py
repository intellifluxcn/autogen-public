"""Abstract base class for storage backends."""
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from dataclasses import dataclass


@dataclass
class StorageResult:
    """Result of a storage operation."""
    storage_backend: str
    s3_bucket: Optional[str] = None
    s3_key: Optional[str] = None
    oss_bucket: Optional[str] = None
    oss_key: Optional[str] = None
    file_path: Optional[str] = None


class StorageBackend(ABC):
    """Abstract storage interface."""

    @abstractmethod
    def upload(self, local_path: str, key: str) -> StorageResult:
        """Upload file to storage and return metadata."""
        pass

    @abstractmethod
    def download(self, key: str, local_path: str) -> None:
        """Download the object at ``key`` to ``local_path`` (creating parent
        dirs as needed). Raises on failure."""
        pass

    @abstractmethod
    def generate_download_url(
        self,
        key: str,
        filename: str,
        content_disposition: str = 'inline',
        expiration: int = 3600
    ) -> str:
        """Generate download URL (presigned for S3, local path for local)."""
        pass

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Delete file from storage."""
        pass

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check if file exists."""
        pass

    @abstractmethod
    def get_file_size(self, key: str) -> Optional[int]:
        """Get file size in bytes."""
        pass
