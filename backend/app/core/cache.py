"""Redis-backed JSON caching with namespaced keys and cache-aside helpers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec, TypeVar

import structlog
from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings

P = ParamSpec("P")
T = TypeVar("T", bound=BaseModel)
logger = structlog.get_logger(__name__)


class RedisCache:
    """Namespaced asynchronous Redis cache storing validated JSON models."""

    def __init__(self, client: Redis, prefix: str = "travelmind") -> None:
        """Create a cache around an async Redis client."""
        self.client = client
        self.prefix = prefix

    @classmethod
    def from_settings(cls) -> RedisCache:
        """Create a Redis cache from the application settings."""
        return cls(Redis.from_url(get_settings().redis_url, decode_responses=True))

    def make_key(self, namespace: str, key: str) -> str:
        """Build a stable, namespaced Redis key."""
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return f"{self.prefix}:{namespace}:{digest}"

    async def get(self, namespace: str, key: str, schema: type[T]) -> T | None:
        """Load and validate a cached value, returning None when it is absent."""
        redis_key = self.make_key(namespace, key)
        try:
            payload = await self.client.get(redis_key)
            if payload is None:
                return None
            try:
                return schema.model_validate_json(payload)
            except ValidationError:
                logger.warning("cache_value_invalid", namespace=namespace)
                await self.delete(namespace, key)
                return None
        except RedisError as error:
            logger.warning(
                "cache_read_failed",
                namespace=namespace,
                error_type=type(error).__name__,
            )
            return None

    async def set(self, namespace: str, key: str, value: BaseModel, ttl_s: int) -> None:
        """Serialize a Pydantic model and store it with a TTL."""
        if ttl_s < 1:
            raise ValueError("ttl_s must be positive")
        try:
            await self.client.set(
                self.make_key(namespace, key),
                value.model_dump_json(),
                ex=ttl_s,
            )
        except RedisError as error:
            logger.warning(
                "cache_write_failed",
                namespace=namespace,
                error_type=type(error).__name__,
            )

    async def delete(self, namespace: str, key: str) -> None:
        """Delete a namespaced value."""
        try:
            await self.client.delete(self.make_key(namespace, key))
        except RedisError as error:
            logger.warning(
                "cache_delete_failed",
                namespace=namespace,
                error_type=type(error).__name__,
            )


def cached(
    namespace: str,
    ttl_s: int,
    key_builder: Callable[P, str],
    schema: type[T],
    cache: RedisCache,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Decorate an async Pydantic-returning function with cache-aside behavior."""

    def decorate(function: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        @wraps(function)
        async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
            key = key_builder(*args, **kwargs)
            cached_value = await cache.get(namespace, key, schema)
            if cached_value is not None:
                return cached_value
            lock_key = cache.make_key(namespace, f"lock:{key}")
            lock = cache.client.lock(
                lock_key, timeout=max(ttl_s, 10), blocking_timeout=5
            )
            try:
                acquired = await lock.acquire()
            except RedisError as error:
                logger.warning(
                    "cache_lock_failed",
                    namespace=namespace,
                    error_type=type(error).__name__,
                )
                acquired = False
            if acquired:
                try:
                    cached_value = await cache.get(namespace, key, schema)
                    if cached_value is not None:
                        return cached_value
                    result = await function(*args, **kwargs)
                    await cache.set(namespace, key, result, ttl_s)
                    return result
                finally:
                    try:
                        await lock.release()
                    except RedisError as error:
                        logger.warning(
                            "cache_lock_release_failed",
                            namespace=namespace,
                            error_type=type(error).__name__,
                        )
            result = await function(*args, **kwargs)
            await cache.set(namespace, key, result, ttl_s)
            return result

        return wrapped

    return decorate


async def close_cache(cache: RedisCache) -> None:
    """Close the underlying Redis connection pool."""
    await cache.client.aclose()


def canonical_json(value: object) -> str:
    """Return deterministic JSON suitable for cache key builders."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
