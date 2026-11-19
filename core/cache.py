"""Redis 缓存层：工具结果短时缓存（检索去重、省钱省延迟）。

设计原则：
- 同步客户端（工具是同步函数，直接调用最简单）；
- Redis 不可用时全部静默降级为"不缓存"，绝不阻塞主流程。
"""
import hashlib
import json

from loguru import logger

from core.config import cfg

_client = None
_init_failed = False


def _get_client():
    global _client, _init_failed
    if _init_failed:
        return None
    if _client is None:
        try:
            import redis

            _client = redis.Redis.from_url(
                cfg["redis"]["url"],
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=1,
            )
            _client.ping()
            logger.info(f"[cache] Redis 已连接：{cfg['redis']['url']}")
        except Exception as e:
            _init_failed = True
            _client = None
            logger.warning(f"[cache] Redis 不可用，缓存功能关闭：{e}")
    return _client


def make_key(prefix: str, *parts: str) -> str:
    raw = "|".join(parts)
    return f"{prefix}:{hashlib.md5(raw.encode('utf-8')).hexdigest()}"


def get_json(key: str):
    cli = _get_client()
    if cli is None:
        return None
    try:
        val = cli.get(key)
        return json.loads(val) if val else None
    except Exception:
        return None


def set_json(key: str, value, ttl_seconds: int):
    cli = _get_client()
    if cli is None:
        return
    try:
        cli.setex(key, ttl_seconds, json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        pass
