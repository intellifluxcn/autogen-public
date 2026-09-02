"""Local filesystem storage backend for development."""
import os
import shutil
from typing import Optional
from pathlib import Path
from .base import StorageBackend, StorageResult


class LocalStorage(StorageBackend):
    """Local filesystem implementation."""

    def __init__(self):
        self.base_dir = Path(os.getcwd()) / 'outputs'

    def upload(self, local_path: str, key: str) -> StorageResult:
        """
        For local storage, files are already in outputs/ directory.
        Just verify the file exists and return metadata.
        """
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Local file not found: {local_path}")

        return StorageResult(
            storage_backend='local',
            s3_bucket=None,
            s3_key=None,
            file_path=local_path
        )

    def download(self, key: str, local_path: str) -> None:
        """Copy the stored file (outputs/<key>) to ``local_path``."""
        src = self.base_dir / key
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        shutil.copyfile(str(src), local_path)

    def generate_download_url(
        self,
        key: str,
        filename: str,
        content_disposition: str = 'inline',
        expiration: int = 3600
    ) -> str:
        """
        Return internal marker for local file serving.
        Backend will detect this and serve via FileResponse.
        """
        # Return the key so the backend can construct the full path
        return f"__LOCAL__:{key}"

    def delete(self, key: str) -> bool:
        """Delete local file."""
        file_path = self.base_dir / key
        try:
            if file_path.exists():
                file_path.unlink()
            return True
        except OSError:
            return False

    def exists(self, key: str) -> bool:
        """Check if local file exists."""
        return (self.base_dir / key).exists()

    def get_file_size(self, key: str) -> Optional[int]:
        """Get local file size."""
        file_path = self.base_dir / key
        if file_path.exists():
            return file_path.stat().st_size
        return None
