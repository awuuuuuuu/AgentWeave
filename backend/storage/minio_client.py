from __future__ import annotations

import tempfile

import boto3
from botocore.client import Config

from config import settings

_ENDPOINT = settings.minio_endpoint
_ACCESS_KEY = settings.minio_access_key
_SECRET_KEY = settings.minio_secret_key
_BUCKET = settings.minio_bucket

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
    """删除 MinIO 中的单个对象"""
    _client.delete_object(Bucket=_BUCKET, Key=object_key)

def delete_objects(object_keys: list[str]) -> list[str]:
    """批量删除 MinIO 对象（单次请求最多 1000 个）
    返回删除失败的 key 列表（非 NoSuchKey 类错误）
    """
    if not object_keys:
        return []
    failed: list[str] = []
    # S3 delete_objects 每次最多 1000 个
    for i in range(0, len(object_keys), 1000):
        batch = object_keys[i:i + 1000]
        resp = _client.delete_objects(
            Bucket=_BUCKET,
            Delete={"Objects": [{"Key": k} for k in batch], "Quiet": False},
        )
        for err in resp.get("Errors", []):
            if err.get("Code") not in ("NoSuchKey", "404"):
                failed.append(err["Key"])
    return failed