"""MinIO 客户端：从项目统一配置 cfg['minio'] 读取，替代 shopkeeper 的 knowledge.utils.minio_util。

差异：
- shopkeeper 用裸 os.getenv，本项目走 omegaconf（支持 .env 覆盖 + local.yaml）
- 沿用它的"bucket 不存在就创建 + 失败返回 None"降级策略
"""
import logging

from minio import Minio
from minio.error import S3Error

from core.config import cfg

_client = None


def get_minio_client():
    global _client
    if _client is not None:
        return _client
    mcfg = cfg["minio"]
    try:
        client = Minio(
            mcfg["endpoint"],
            access_key=mcfg["access_key"],
            secret_key=mcfg["secret_key"],
            secure=str(mcfg.get("external_endpoint", "")).startswith("https://"),
        )
        bucket = mcfg["bucket"]
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
            logging.info(f"[minio] 创建桶 {bucket}")
        _client = client
        return client
    except S3Error as e:
        logging.error(f"[minio] 客户端创建失败：{e}")
        return None


def get_external_base_url() -> str:
    """写进 md 的外链前缀（external_endpoint / bucket）。"""
    return f"{str(cfg['minio']['external_endpoint']).rstrip('/')}/{cfg['minio']['bucket']}"


def reset_minio_client():
    global _client
    _client = None
