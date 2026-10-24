"""Shared real-browser test helpers: the local Chromium gate, a loopback server, fakes.

The guided pages, the run report and the standalone inspector tests all drive a real
server in a real browser. This module holds what they share; it defines no tests.
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from playwright.sync_api import Page

from adlife.core.domain.city_cognition_v2 import CityCognitionResultV2, CityProviderAttemptV2

_BROWSER_PATHS = (
    Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
    Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    Path("/usr/bin/google-chrome"),
    Path("/usr/bin/chromium"),
    Path("/usr/bin/chromium-browser"),
)
_LONG_BROWSER_TEST = pytest.mark.skipif(
    os.environ.get("ADLIFE_SKIP_LONG_TESTS") == "1" or sys.gettrace() is not None,
    reason="long browser workloads require an uninstrumented timing gate",
)


def _installed_browser() -> Path:
    browser = next((path for path in _BROWSER_PATHS if path.is_file()), None)
    if browser is None:
        message = "no supported local Chromium browser executable is installed"
        if os.environ.get("ADLIFE_REQUIRE_BROWSER") == "1":
            pytest.fail(message)
        pytest.skip(message)
    return browser


@contextmanager
def _serve(application: object) -> Iterator[str]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(
            application,
            host="127.0.0.1",
            port=port,
            log_level="critical",
            access_log=False,
            # A module-scoped browser can still hold a connection open when the server
            # stops; a loaded Windows runner then waited out the join below. Drop it instead.
            timeout_graceful_shutdown=3,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("workbench browser test server did not start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            raise RuntimeError("workbench browser test server did not stop")


def _artifact_hashes(directory: Path) -> dict[str, str]:
    """Snapshot every persisted byte without depending on filesystem metadata."""

    return {
        path.relative_to(directory).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _wait_for_route_handler(
    page: Page, delivered: Callable[[], object], *, what: str, timeout_s: float = 10.0
) -> None:
    """Pump Playwright events until a Python route handler has recorded what it saw.

    Playwright schedules a route handler as a task, after the page has issued the request,
    so the page may already show its next state while the handler has not run. Never index
    a handler's records right after the UI reacts; let the dispatcher deliver them first.
    """
    deadline = time.monotonic() + timeout_s
    while not delivered():
        if time.monotonic() >= deadline:
            raise AssertionError(f"the route handler never recorded {what}")
        page.wait_for_timeout(20)


class _SignalProvider:
    """A loopback-free fake provider returning one closed category vector."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.calls = 0
        self.closed = False

    async def call(self, request: Any) -> CityProviderAttemptV2:
        self.calls += 1
        result = CityCognitionResultV2(
            schema_version=2,
            request_id=request.request_id,
            affect="curious",
            relevance="high",
            credibility="medium",
            salience="high",
            safety_flags=(),
            disclosure="synthetic-bounded-cognition-not-observed-behavior",
        )
        return CityProviderAttemptV2(
            schema_version=2, status="valid", result=result, repair_context=None, usage=None
        )

    async def repair_call(self, *_args: object, **_kwargs: object) -> CityProviderAttemptV2:
        raise AssertionError("a valid signal never needs repair")

    async def aclose(self) -> None:
        self.closed = True


class _HeldSignalProvider(_SignalProvider):
    """A slow fake model: every answer waits until the test releases one permit."""

    def __init__(self, permits: threading.Semaphore, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.permits = permits

    async def call(self, request: Any) -> CityProviderAttemptV2:
        while not self.permits.acquire(blocking=False):
            await asyncio.sleep(0.01)
        return await super().call(request)
