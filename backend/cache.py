"""
Redis Application Caching Service.
Provides basic get, set, delete, and pattern-invalidation caching functions.
Supports Redis when running, and provides in-memory fallback for testing.
"""
import fnmatch
import json
import logging
import time
import redis
from flask import current_app

logger = logging.getLogger(__name__)

# In-memory cache store used during testing or offline fallback
_memory_cache = {}  # key -> (value_json, expiry_timestamp)


def _get_memory_cache(key: str):
    item = _memory_cache.get(key)
    if not item:
        return None
    val_json, expiry = item
    if expiry is not None and time.time() > expiry:
        _memory_cache.pop(key, None)
        return None
    return json.loads(val_json)


def _set_memory_cache(key: str, value, timeout: int = 60):
    expiry = time.time() + timeout if timeout else None
    _memory_cache[key] = (json.dumps(value), expiry)
    return True


def _delete_memory_cache(key: str):
    _memory_cache.pop(key, None)
    return True


def _delete_pattern_memory_cache(pattern: str):
    to_delete = [k for k in _memory_cache.keys() if fnmatch.fnmatch(k, pattern)]
    for k in to_delete:
        _memory_cache.pop(k, None)
    return True


def get_redis_client():
    """
    Returns a configured Redis client instance.
    Uses current_app config or falls back to standard localhost.
    """
    try:
        if current_app:
            if current_app.config.get("TESTING") and not current_app.config.get("USE_REAL_REDIS"):
                return None
            redis_url = current_app.config.get("CACHE_REDIS_URL", "redis://localhost:6379/0")
            if "redis_client" not in current_app.extensions:
                current_app.extensions["redis_client"] = redis.from_url(
                    redis_url,
                    decode_responses=True,
                    socket_connect_timeout=0.5,
                    socket_timeout=0.5,
                )
            return current_app.extensions["redis_client"]
    except Exception as e:
        logger.warning(f"[Cache] Unable to connect to Redis: {e}")
        return None
    return None


def cache_get(key: str):
    """
    Retrieves a cached value by key and deserializes from JSON.
    Returns None on cache miss or connection error.
    """
    try:
        client = get_redis_client()
        if client:
            val = client.get(key)
            if val is not None:
                return json.loads(val)
            return None
        return _get_memory_cache(key)
    except Exception as e:
        logger.warning(f"[Cache] cache_get failed for key '{key}': {e}")
        return _get_memory_cache(key)


def cache_set(key: str, value, timeout: int = 60) -> bool:
    """
    Serializes value to JSON and stores in Redis (or memory cache) with an explicit TTL.
    """
    try:
        client = get_redis_client()
        if client:
            client.set(key, json.dumps(value), ex=timeout)
            return True
        return _set_memory_cache(key, value, timeout=timeout)
    except Exception as e:
        logger.warning(f"[Cache] cache_set failed for key '{key}': {e}")
        return _set_memory_cache(key, value, timeout=timeout)


def cache_delete(key: str) -> bool:
    """
    Deletes a single key from Redis and memory cache.
    """
    try:
        client = get_redis_client()
        if client:
            client.delete(key)
        _delete_memory_cache(key)
        return True
    except Exception as e:
        logger.warning(f"[Cache] cache_delete failed for key '{key}': {e}")
        _delete_memory_cache(key)
        return True


def cache_delete_pattern(pattern: str) -> bool:
    """
    Deletes all keys matching a specific pattern (e.g. 'public_exams:*').
    """
    try:
        client = get_redis_client()
        if client:
            keys = client.keys(pattern)
            if keys:
                client.delete(*keys)
        _delete_pattern_memory_cache(pattern)
        return True
    except Exception as e:
        logger.warning(f"[Cache] cache_delete_pattern failed for pattern '{pattern}': {e}")
        _delete_pattern_memory_cache(pattern)
        return True


def invalidate_exam_cache():
    """
    Invalidates cached public examination listings and course details
    whenever Admin adds, modifies, or deletes an examination or course.
    """
    cache_delete_pattern("public_exams:*")
    cache_delete_pattern("courses_*")
