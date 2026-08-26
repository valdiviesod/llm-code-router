"""End-to-end tests for the stdio MCP client against a fake server.

The fake server is a real subprocess speaking line-delimited JSON-RPC, so
these cover the wire behaviour the unit-level tool tests cannot: the
`initialize` handshake, response-id correlation, and the failure paths
that must degrade to "tool unavailable" rather than raise.
"""

from __future__ import annotations

import sys
import textwrap

import pytest

from coderouter.tools.mcp_client import MCPClient
from coderouter.tools.models import MCPServer

SERVER = textwrap.dedent(
    """
    import json, sys

    strict = "--strict" in sys.argv
    noisy = "--noisy" in sys.argv
    initialized = False

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method = msg.get("method")
        if method == "initialize":
            initialized = True
            reply = {"protocolVersion": "2024-11-05", "capabilities": {},
                     "serverInfo": {"name": "fake", "version": "1"}}
        elif method == "notifications/initialized":
            continue  # notifications get no reply
        elif method == "tools/list":
            if strict and not initialized:
                out = {"jsonrpc": "2.0", "id": msg["id"],
                       "error": {"code": -32002, "message": "not initialized"}}
                print(json.dumps(out), flush=True)
                continue
            if noisy:
                # Chatter with no id, plus a notification: neither is our answer.
                print("starting up, not json", flush=True)
                print(json.dumps({"jsonrpc": "2.0", "method": "notifications/log",
                                  "params": {"msg": "hi"}}), flush=True)
            reply = {"tools": [{"name": "read_file", "description": "reads",
                                "inputSchema": {"type": "object"}}]}
        else:
            reply = {}
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": reply}), flush=True)
    """
)


@pytest.fixture
def server_script(tmp_path):
    path = tmp_path / "fake_mcp_server.py"
    path.write_text(SERVER)
    return path


def _client(script, *args, **kw):
    server = MCPServer(name="fake", command=sys.executable,
                       args=(str(script), *args))
    return MCPClient(server, **kw)


async def test_list_tools_completes_the_initialize_handshake(server_script):
    """Regression: the client used to send `tools/list` first, which a
    conformant server rejects. `--strict` refuses anything pre-initialize."""
    client = _client(server_script, "--strict")
    try:
        tools = await client.list_tools()
    finally:
        await client.close()
    assert [t.name for t in tools] == ["read_file"]


async def test_response_is_matched_by_id_through_server_chatter(server_script):
    """Non-JSON lines and id-less notifications must be skipped, not
    mistaken for the response."""
    client = _client(server_script, "--noisy")
    try:
        tools = await client.list_tools()
    finally:
        await client.close()
    assert [t.name for t in tools] == ["read_file"]


async def test_missing_binary_yields_no_tools_instead_of_raising():
    server = MCPServer(name="ghost", command="/nonexistent/mcp-server-binary")
    client = MCPClient(server)
    try:
        assert await client.list_tools() == []
    finally:
        await client.close()
