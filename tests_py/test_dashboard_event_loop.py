from __future__ import annotations

import argparse
import asyncio
import os
import socket
import struct

import pytest
import uvicorn

from local_control_center import cli
from local_control_center.control_plane.runtime import ControlCenterRuntime


@pytest.fixture
def dashboard_loop(tmp_path, monkeypatch):
    """Resolve the installed server's real factory from the canonical CLI arguments."""
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "dashboard.sqlite")
    monkeypatch.setattr(cli, "ControlCenterRuntime", lambda **kwargs: runtime)
    monkeypatch.setattr(
        cli,
        "parse_args",
        lambda: argparse.Namespace(
            workspace=str(tmp_path),
            db_path=str(runtime.db_path),
            worker=False,
            no_dashboard=False,
            no_worker=True,
            dashboard_host="127.0.0.1",
            dashboard_port=0,
            static_dir=None,
        ),
    )
    loops = []

    def resolve_server(app, **options):
        config = uvicorn.Config(app, **options)
        loops.append(config.get_loop_factory()())

    monkeypatch.setattr(cli.uvicorn, "run", resolve_server)
    try:
        cli.main()
        assert len(loops) == 1
        yield loops[0]
    finally:
        for loop in loops:
            loop.close()
        runtime.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows event-loop contract")
def test_cli_selector_policy_reaches_uvicorn_factory(dashboard_loop):
    assert isinstance(dashboard_loop, asyncio.SelectorEventLoop)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows TCP reset")
def test_dashboard_loop_closes_reset_peer_without_callback_error(dashboard_loop):
    errors = []
    dashboard_loop.set_exception_handler(lambda loop, context: errors.append(context))

    async def exercise():
        connected = dashboard_loop.create_future()
        disconnected = dashboard_loop.create_future()

        class Protocol(asyncio.Protocol):
            def connection_made(self, transport):
                connected.set_result(transport)

            def connection_lost(self, exc):
                disconnected.set_result(exc)

        server = await dashboard_loop.create_server(Protocol, "127.0.0.1", 0)
        peer = socket.socket()
        peer.setblocking(False)
        transport = None
        try:
            await dashboard_loop.sock_connect(peer, server.sockets[0].getsockname())
            transport = await asyncio.wait_for(connected, 2)
            peer.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("HH", 1, 0))
            peer.close()
            await asyncio.wait_for(disconnected, 2)
            assert transport.is_closing()
            await asyncio.sleep(0)  # Drain the close callback, not a time-based retry.
        finally:
            peer.close()
            if transport is not None:
                transport.close()
            server.close()
            await server.wait_closed()

    dashboard_loop.run_until_complete(exercise())
    assert errors == []
