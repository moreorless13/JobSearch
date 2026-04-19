from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:  # pragma: no cover - import failure is exercised by tests through dependency injection
    import redis as redis_module
except ImportError:  # pragma: no cover
    redis_module = None

DEFAULT_LOCAL_REDIS_URL = "redis://localhost:6379/0"
DEFAULT_DOCKER_REDIS_URL = "redis://host.docker.internal:6379/0"
DEFAULT_COMPOSE_REDIS_URL = "redis://redis:6379/0"
LOCAL_REDIS_CONTAINER_NAME = "jobsearch-redis"
REDIS_IMAGE = "redis:7-alpine"


@dataclass(frozen=True)
class RedisPreflightResult:
    redis_url: str


class RedisPreflightError(RuntimeError):
    def __init__(self, message: str, *, start_command: str) -> None:
        super().__init__(message)
        self.start_command = start_command


def redis_start_command(redis_url: str | None, *, compose_file_exists: bool = True) -> str:
    if not redis_url:
        return _docker_run_command(6379)

    parsed = urlparse(redis_url)
    host = parsed.hostname or ""
    port = parsed.port or 6379
    if host == "redis" and compose_file_exists:
        return "docker compose up -d redis"
    return _docker_run_command(port)


def run_redis_preflight(
    *,
    redis_url: str | None = None,
    redis_dependency: Any | None = None,
    root_dir: Path | None = None,
) -> RedisPreflightResult:
    resolved_url = os.getenv("REDIS_URL") if redis_url is None else redis_url
    repo_root = root_dir or Path(__file__).resolve().parent.parent
    compose_file_exists = (repo_root / "compose.yaml").exists()
    start_command = redis_start_command(resolved_url, compose_file_exists=compose_file_exists)

    if not resolved_url:
        raise RedisPreflightError(
            "Redis preflight failed: REDIS_URL is not configured.",
            start_command=start_command,
        )

    dependency = redis_dependency if redis_dependency is not None else redis_module
    if dependency is None:
        raise RedisPreflightError(
            "Redis preflight failed: the redis package is not installed.",
            start_command=start_command,
        )

    try:
        client = dependency.Redis.from_url(resolved_url, decode_responses=True)
        client.ping()
    except Exception as exc:  # pragma: no cover - exact client errors vary by redis-py version
        raise RedisPreflightError(
            f"Redis preflight failed: could not connect to {resolved_url}: {exc}",
            start_command=start_command,
        ) from exc

    return RedisPreflightResult(redis_url=resolved_url)


def _docker_run_command(port: int) -> str:
    return f"docker run --name {LOCAL_REDIS_CONTAINER_NAME} -p {port}:6379 -d {REDIS_IMAGE}"
