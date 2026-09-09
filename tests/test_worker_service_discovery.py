"""The RQ worker must start service discovery, not just the API.

Every URL lookup goes through service_config._get_url, which consults
config-service only when init() has run:

    if _has_config_client and _initialized:

init() lived solely in the FastAPI startup event, which the worker process never
executes. So the worker skipped discovery entirely and fell through to env vars
that a discovery-based install does not set.

The worker is where the LLM work happens -- structuring OCR text into a recipe,
generating meal plans -- so on prod this surfaced as

    LLM proxy URL is not configured. Register jarvis-llm-proxy-api in
    config-service (JARVIS_CONFIG_URL) or set LLM_BASE_URL.

from a container that had JARVIS_CONFIG_URL set, against a config-service that
had jarvis-llm-proxy-api registered and healthy. Nothing was misconfigured; the
process simply never asked.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

WORKER_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run_rq_worker.py"


@pytest.fixture
def worker_module(monkeypatch):
    """Import the worker entrypoint without letting it touch Redis."""
    spec = importlib.util.spec_from_file_location("_run_rq_worker_under_test", WORKER_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


def test_worker_script_exists():
    assert WORKER_SCRIPT.is_file()


def test_main_initializes_service_discovery(worker_module, monkeypatch):
    """Without this the worker cannot resolve llm-proxy, auth, or anything else."""
    calls = []

    monkeypatch.setattr(
        worker_module.service_config, "init", lambda: calls.append("init") or True
    )

    # Stop before any real work: the point is that init() happened first.
    class _Stop(Exception):
        pass

    def _no_redis():
        raise _Stop()

    monkeypatch.setattr(worker_module, "get_redis_connection", _no_redis)
    monkeypatch.setattr(worker_module.time, "sleep", lambda _s: None)

    with pytest.raises(_Stop):
        worker_module.main()

    assert calls == ["init"], "the worker must start service discovery before working"


def test_discovery_is_initialized_before_the_worker_connects(worker_module, monkeypatch):
    """Ordering matters: a lookup made before init() silently misses discovery."""
    order = []

    monkeypatch.setattr(
        worker_module.service_config, "init", lambda: order.append("init") or True
    )

    class _Stop(Exception):
        pass

    def _connect():
        order.append("redis")
        raise _Stop()

    monkeypatch.setattr(worker_module, "get_redis_connection", _connect)
    monkeypatch.setattr(worker_module.time, "sleep", lambda _s: None)

    with pytest.raises(_Stop):
        worker_module.main()

    # main() retries the connection, so "redis" repeats. Discovery must be
    # started exactly once, and before the first attempt -- a lookup made before
    # init() silently misses config-service rather than failing loudly.
    assert order[0] == "init"
    assert order.count("init") == 1
    assert order[1] == "redis"


def test_worker_survives_discovery_being_unavailable(worker_module, monkeypatch):
    """init() returning False means "no config-service" -- fall back, do not crash."""
    monkeypatch.setattr(worker_module.service_config, "init", lambda: False)

    class _Stop(Exception):
        pass

    monkeypatch.setattr(
        worker_module, "get_redis_connection", lambda: (_ for _ in ()).throw(_Stop())
    )
    monkeypatch.setattr(worker_module.time, "sleep", lambda _s: None)

    # Reaching the Redis step at all proves it did not abort on a False init().
    with pytest.raises(_Stop):
        worker_module.main()
