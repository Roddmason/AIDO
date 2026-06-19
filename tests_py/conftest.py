from __future__ import annotations

import asyncio
import gc
import sys
from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient as StarletteTestClient

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(autouse=True)
def manage_testclient_event_loops(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    created_clients: list[StarletteTestClient] = []
    original_init = StarletteTestClient.__init__

    def auto_entering_init(self: StarletteTestClient, *args, **kwargs) -> None:
        original_init(self, *args, **kwargs)
        self.__enter__()
        self._aido_auto_entered = True
        created_clients.append(self)

    monkeypatch.setattr(StarletteTestClient, "__init__", auto_entering_init)
    yield
    for client in reversed(created_clients):
        if getattr(client, "_aido_auto_entered", False):
            client.__exit__(None, None, None)
    gc.collect()
