"""AIDO-69: implicit fixture lifespan and explicit context own one set of streams.

@author Rodrigo Mason
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_explicit_testclient_context_does_not_reenter_implicit_fixture_lifespan():
    events = []

    @asynccontextmanager
    async def lifespan(_app):
        events.append("start")
        yield
        events.append("stop")

    client = TestClient(FastAPI(lifespan=lifespan))
    portal = client.portal
    with client:
        assert client.portal is portal
        assert events == ["start"]
    assert events == ["start", "stop"]
    assert client.portal is None
