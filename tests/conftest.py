"""Pytest configuration and shared fixtures."""
import os
import sys
from pathlib import Path
import pytest

# Add src/ to Python path so tests can import project modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

os.environ.setdefault("BOTTOKEN", "test-bot-token")

# Redis for the integration tests: a throwaway Docker container when Docker is
# available (testcontainers), otherwise whatever REDIS_HOST/REDIS_PORT point at
# (default localhost:6379, e.g. `redis-server` started locally).
_redis_container = None


def _local_redis_reachable() -> bool:
    import redis

    try:
        redis.Redis(
            host=os.environ.get("REDIS_HOST", "localhost"),
            port=int(os.environ.get("REDIS_PORT", "6379")),
            socket_connect_timeout=1,
        ).ping()
        return True
    except Exception:
        return False


def pytest_configure(config):
    """Set up Redis before test collection."""
    global _redis_container
    try:
        from testcontainers.redis import RedisContainer

        _redis_container = RedisContainer("redis:7-alpine")
        _redis_container.start()
        os.environ["REDIS_HOST"] = _redis_container.get_container_host_ip()
        os.environ["REDIS_PORT"] = str(_redis_container.get_exposed_port(6379))
        os.environ["REDIS_DB"] = "0"
        return
    except Exception as e:  # Docker missing, testcontainers missing, ...
        _redis_container = None
        if not _local_redis_reachable():
            raise RuntimeError(
                "No Redis available for tests: could not start a Docker container "
                f"({type(e).__name__}: {e}) and nothing answers at "
                f"{os.environ.get('REDIS_HOST', 'localhost')}:{os.environ.get('REDIS_PORT', '6379')}. "
                "Start Docker, or run `redis-server` locally (REDIS_HOST/REDIS_PORT to override)."
            ) from e
        os.environ.setdefault("REDIS_DB", "0")


def pytest_unconfigure(config):
    """Clean up Redis container after all tests."""
    global _redis_container
    if _redis_container:
        _redis_container.stop()


@pytest.fixture(scope="session")
def redis_container():
    """Get the running Redis container (None when a local server is used)."""
    return _redis_container
