"""Storage backend factory with validation."""
import os
from typing import Optional

from .base import StorageBackend
from .s3_storage import S3Storage
from .oss_storage import OSSStorage
from .local_storage import LocalStorage
from utils.pipeline_log import pipeline_log

_storage_instance: Optional[StorageBackend] = None


def get_storage() -> StorageBackend:
    """Get storage backend singleton based on environment."""
    global _storage_instance

    if _storage_instance is not None:
        return _storage_instance

    backend = os.getenv('STORAGE_BACKEND', 'local').lower()

    if backend == 's3':
        required_vars = ['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_S3_BUCKET']
        missing = [v for v in required_vars if not os.getenv(v)]
        if missing:
            raise ValueError(f"Missing required environment variables for S3: {missing}")

        _storage_instance = S3Storage()
        pipeline_log(
            f"Using S3 storage backend: {os.getenv('AWS_S3_BUCKET')}",
            stage="pipeline",
            component="storage",
        )

    elif backend == 'oss':
        required_vars = [
            'ALIYUN_OSS_ACCESS_KEY_ID',
            'ALIYUN_OSS_ACCESS_KEY_SECRET',
            'ALIYUN_OSS_BUCKET',
            'ALIYUN_OSS_ENDPOINT',
        ]
        missing = [v for v in required_vars if not os.getenv(v)]
        if missing:
            raise ValueError(f"Missing required environment variables for OSS: {missing}")

        _storage_instance = OSSStorage()
        pipeline_log(
            f"Using OSS storage backend: {os.getenv('ALIYUN_OSS_BUCKET')}",
            stage="pipeline",
            component="storage",
        )

    elif backend == 'local':
        _storage_instance = LocalStorage()
        pipeline_log(
            "Using local storage backend",
            stage="pipeline",
            component="storage",
        )

    else:
        raise ValueError(f"Unknown storage backend: {backend}")

    return _storage_instance


def reset_storage():
    """Reset storage instance (for testing)."""
    global _storage_instance
    _storage_instance = None
