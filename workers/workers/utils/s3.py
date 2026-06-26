"""S3 / MinIO helper utilities — synchronous (used from Celery tasks)."""

from __future__ import annotations

import os

import boto3
from botocore.client import Config

S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "http://localhost:9000")
S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "minioadmin")
S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "minioadmin")
S3_BUCKET = os.environ.get("S3_BUCKET", "rabbithole-blobs")

_client = None


def _get_s3():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=S3_ENDPOINT,
            aws_access_key_id=S3_ACCESS_KEY,
            aws_secret_access_key=S3_SECRET_KEY,
            config=Config(signature_version="s3v4"),
        )
        # Ensure bucket exists
        try:
            _client.head_bucket(Bucket=S3_BUCKET)
        except Exception:
            try:
                _client.create_bucket(Bucket=S3_BUCKET)
            except Exception:
                pass
    return _client


def upload_blob(key: str, data: bytes) -> str:
    """Upload bytes to S3/MinIO synchronously and return the key."""
    _get_s3().put_object(Bucket=S3_BUCKET, Key=key, Body=data)
    return key
