"""Aliyun OSS storage backend."""
import logging
import mimetypes
import os
from typing import Optional

try:
    import oss2
except ImportError:  # pragma: no cover
    oss2 = None

from .base import StorageBackend, StorageResult
from utils.pipeline_log import pipeline_log

class OSSStorage(StorageBackend):
    """Aliyun OSS storage implementation."""

    def __init__(self):
        if oss2 is None:
            raise ImportError("oss2 is required for OSS storage. Please install dependency 'oss2'.")

        self.bucket = os.getenv('ALIYUN_OSS_BUCKET')
        if not self.bucket:
            raise ValueError("ALIYUN_OSS_BUCKET environment variable not set")

        endpoint = os.getenv('ALIYUN_OSS_ENDPOINT')
        if not endpoint:
            raise ValueError("ALIYUN_OSS_ENDPOINT environment variable not set")
        if not endpoint.startswith("http://") and not endpoint.startswith("https://"):
            endpoint = f"https://{endpoint}"

        access_key_id = os.getenv('ALIYUN_OSS_ACCESS_KEY_ID')
        access_key_secret = os.getenv('ALIYUN_OSS_ACCESS_KEY_SECRET')
        if not access_key_id or not access_key_secret:
            raise ValueError("ALIYUN_OSS_ACCESS_KEY_ID/ALIYUN_OSS_ACCESS_KEY_SECRET not set")

        auth = oss2.Auth(access_key_id, access_key_secret)
        connect_timeout = float(os.getenv("OSS_CONNECT_TIMEOUT_SECONDS") or "180")
        self.client = oss2.Bucket(
            auth=auth,
            endpoint=endpoint,
            bucket_name=self.bucket,
            connect_timeout=connect_timeout,
        )

    def upload(self, local_path: str, key: str) -> StorageResult:
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Local file not found: {local_path}")

        content_type, _ = mimetypes.guess_type(local_path)
        headers = {
            'Content-Type': content_type or 'application/octet-stream',
            'x-oss-server-side-encryption': 'AES256',
        }
        self.client.put_object_from_file(key, local_path, headers=headers)
        pipeline_log(
            f"Uploaded {local_path} to oss://{self.bucket}/{key}",
            stage="pipeline",
            component="storage",
        )

        return StorageResult(
            storage_backend='oss',
            oss_bucket=self.bucket,
            oss_key=key,
            file_path=local_path,
        )

    def download(self, key: str, local_path: str) -> None:
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        self.client.get_object_to_file(key, local_path)

    def generate_download_url(
        self,
        key: str,
        filename: str,
        content_disposition: str = 'inline',
        expiration: int = 3600
    ) -> str:
        response_content_disposition = f'{content_disposition}; filename="{filename}"'
        return self.client.sign_url(
            method='GET',
            key=key,
            expires=expiration,
            params={'response-content-disposition': response_content_disposition},
        )

    def delete(self, key: str) -> bool:
        try:
            self.client.delete_object(key)
            return True
        except Exception as e:
            pipeline_log(
                f"OSS delete failed: {e}",
                stage="pipeline",
                component="storage",
                level=logging.ERROR,
            )
            return False

    def exists(self, key: str) -> bool:
        try:
            return bool(self.client.object_exists(key))
        except Exception:
            return False

    def get_file_size(self, key: str) -> Optional[int]:
        try:
            response = self.client.head_object(key)
            return response.content_length
        except Exception:
            return None
