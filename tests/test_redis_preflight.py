from __future__ import annotations

import pytest

from job_agent.redis_preflight import RedisPreflightError, redis_start_command, run_redis_preflight


class FakeRedisClient:
    def ping(self):
        return True


class FakeRedisFactory:
    seen_urls: list[str] = []

    @classmethod
    def from_url(cls, redis_url, *, decode_responses):
        cls.seen_urls.append(redis_url)
        assert decode_responses is True
        return FakeRedisClient()


class FakeRedisDependency:
    Redis = FakeRedisFactory


class BrokenRedisClient:
    def ping(self):
        raise ConnectionError("connection refused")


class BrokenRedisFactory:
    @staticmethod
    def from_url(_redis_url, *, decode_responses):
        assert decode_responses is True
        return BrokenRedisClient()


class BrokenRedisDependency:
    Redis = BrokenRedisFactory


def test_redis_start_command_uses_compose_for_compose_host() -> None:
    assert redis_start_command("redis://redis:6379/0") == "docker compose up -d redis"


def test_redis_start_command_uses_docker_run_for_localhost() -> None:
    assert (
        redis_start_command("redis://localhost:6380/0")
        == "docker run --name jobsearch-redis -p 6380:6379 -d redis:7-alpine"
    )


def test_run_redis_preflight_pings_configured_url() -> None:
    FakeRedisFactory.seen_urls = []

    result = run_redis_preflight(
        redis_url="redis://localhost:6379/0",
        redis_dependency=FakeRedisDependency,
    )

    assert result.redis_url == "redis://localhost:6379/0"
    assert FakeRedisFactory.seen_urls == ["redis://localhost:6379/0"]


def test_run_redis_preflight_reports_start_command_when_missing() -> None:
    with pytest.raises(RedisPreflightError) as exc_info:
        run_redis_preflight(redis_url="", redis_dependency=FakeRedisDependency)

    assert "REDIS_URL is not configured" in str(exc_info.value)
    assert exc_info.value.start_command == "docker run --name jobsearch-redis -p 6379:6379 -d redis:7-alpine"


def test_run_redis_preflight_reports_start_command_when_unreachable() -> None:
    with pytest.raises(RedisPreflightError) as exc_info:
        run_redis_preflight(
            redis_url="redis://host.docker.internal:6379/0",
            redis_dependency=BrokenRedisDependency,
        )

    assert "could not connect" in str(exc_info.value)
    assert exc_info.value.start_command == "docker run --name jobsearch-redis -p 6379:6379 -d redis:7-alpine"
