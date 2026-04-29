from __future__ import annotations

import os
import tempfile

import boto3
from botocore.client import Config
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

_ENDPOINT = os.environ["MINIO_ENDPOINT"]
_ACCESS_KEY = os.environ["MINIO_ACCESS_KEY"]
_SECRET_KEY = os.environ["MINIO_SECRET_KEY"]
_BUCKET = os.environ.get("MINIO_BUCKET", "ragent")

def _make_client():
    client = boto3.client(
        "s3",
        endpoint_url=_ENDPOINT,
        aws_access_key_id=_ACCESS_KEY,
        aws_secret_access_key=_SECRET_KEY,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )
    existing = [b["Name"] for b in client.list_buckets().get("Buckets", [])]
    if _BUCKET not in existing:
        client.create_bucket(Bucket=_BUCKET)
    return client

_client = _make_client()

def upload_fileobj(file_obj, object_key: str) -> str:
    """上传文件对象到 MinIO，返回 object_key"""
    _client.upload_fileobj(file_obj, _BUCKET, object_key)
    return object_key

def download_to_tempfile(object_key: str, suffix: str = "") -> str:
    """从 MinIO 下载文件到本地临时文件，返回临时文件路径"""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.close()
    _client.download_file(_BUCKET, object_key, tmp.name)
    return tmp.name

def delete_object(object_key: str) -> None:
    """删除 MinIO 中的对象"""
    _client.delete_object(Bucket=_BUCKET, Key=object_key)