"""AWS S3 storage backend with multipart upload support."""
import os
import mimetypes
import logging
from typing import Optional

try:
    import boto3
    from botocore.exceptions import ClientError
    from botocore.config import Config
    from boto3.s3.transfer import TransferConfig
except ImportError:  # pragma: no cover
    boto3 = None
    ClientError = Exception
    Config = None
    TransferConfig = None
from .base import StorageBackend, StorageResult
from utils.pipeline_log import pipeline_log

class S3Storage(StorageBackend):
    """S3 storage implementation with optimized uploads."""

    def __init__(self):
        if boto3 is None or Config is None or TransferConfig is None:
            raise ImportError("boto3 is required for S3 storage. Please install dependency 'boto3'.")

        self.bucket = os.getenv('AWS_S3_BUCKET')
        if not self.bucket:
            raise ValueError("AWS_S3_BUCKET environment variable not set")

        config = Config(
            retries={'max_attempts': 3, 'mode': 'adaptive'},
            connect_timeout=10,
            read_timeout=60,
            signature_version='s3v4'
        )

        self.client = boto3.client(
            's3',
            region_name=os.getenv('AWS_REGION', 'us-east-1'),
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            config=config
        )

        self.transfer_config = TransferConfig(
            multipart_threshold=100 * 1024 * 1024,
            max_concurrency=10,
            multipart_chunksize=50 * 1024 * 1024,
            use_threads=True
        )

    def upload(self, local_path: str, key: str) -> StorageResult:
        """Upload file to S3 with automatic multipart for large files."""
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Local file not found: {local_path}")

        try:
            content_type, _ = mimetypes.guess_type(local_path)
            extra_args = {
                'ContentType': content_type or 'application/octet-stream',
                'ServerSideEncryption': 'AES256'
            }

            self.client.upload_file(
                local_path,
                self.bucket,
                key,
                ExtraArgs=extra_args,
                Config=self.transfer_config
            )

            pipeline_log(
                f"Uploaded {local_path} to s3://{self.bucket}/{key}",
                stage="pipeline",
                component="storage",
            )

            return StorageResult(
                storage_backend='s3',
                s3_bucket=self.bucket,
                s3_key=key,
                file_path=local_path
            )

        except ClientError as e:
            pipeline_log(
                f"S3 upload failed: {e}",
                stage="pipeline",
                component="storage",
                level=logging.ERROR,
            )
            raise

    def download(self, key: str, local_path: str) -> None:
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        self.client.download_file(
            self.bucket, key, local_path, Config=self.transfer_config
        )

    def generate_download_url(
        self,
        key: str,
        filename: str,
        content_disposition: str = 'inline',
        expiration: int = 3600
    ) -> str:
        """Generate presigned URL for secure temporary access."""
        try:
            response_content_disposition = f'{content_disposition}; filename="{filename}"'
            url = self.client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': self.bucket,
                    'Key': key,
                    'ResponseContentDisposition': response_content_disposition
                },
                ExpiresIn=expiration
            )
            return url

        except ClientError as e:
            pipeline_log(
                f"Presigned URL generation failed: {e}",
                stage="pipeline",
                component="storage",
                level=logging.ERROR,
            )
            raise

    def delete(self, key: str) -> bool:
        """Delete object from S3."""
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:
            pipeline_log(
                f"S3 delete failed: {e}",
                stage="pipeline",
                component="storage",
                level=logging.ERROR,
            )
            return False

    def exists(self, key: str) -> bool:
        """Check if object exists in S3."""
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def get_file_size(self, key: str) -> Optional[int]:
        """Get file size from S3."""
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
            return response['ContentLength']
        except ClientError:
            return None
