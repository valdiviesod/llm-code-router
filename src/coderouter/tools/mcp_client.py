"""Minimal MCP client over stdio JSON-RPC.

The Model Context Protocol is just JSON-RPC 2.0 over a subprocess
pipe. The router needs `list_tools` and `call_tool`; we don't need
streaming, sampling, or notifications for the first cut, so the
client is small enough to own rather than import.

Laziness: a connection is opened on the first call to `list_tools`
or `call_tool` and closed on `close()`. A failed subprocess is
logged and turned into `None`/empty results — the orchestrator
treats MCP failures as "tool unavailable" rather than as errors,
because one broken MCP server must not bring down the run.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from .. import __version__
from ..logging import get_logger
from .models import MCPServer, Tool

logger = get_logger("tools.mcp_client")

#: MCP revision this client speaks. Sent in `initialize`; servers negotiate down.
PROTOCOL_VERSION = "2024-11-05"
#: How many stray stdout lines to skip while hunting for a response id.
MAX_INTERLEAVED_LINES = 64


class MCPClient:
    """Stdio JSON-RPC client for an MCP server.

    The wire format is line-delimited JSON. Each request is one JSON
    object on one line; each response is one JSON object on one line.
    """

    def __init__(self, server: MCPServer, *, timeout_s: float = 30.0) -> None:
        self._server = server
        self._timeout = timeout_s
        self._proc: asyncio.subprocess.Process | None = None
        self._id = 0
        self._lock = asyncio.Lock()
        self._initialized = False

    async def _ensure_started(self) -> bool:
        if self._proc is not None and self._proc.returncode is None:
            return True
        self._initialized = False
        try:
            self._proc = await asyncio.create_subprocess_exec(
                self._server.command, *self._server.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, OSError) as exc:
            logger.warning("MCP server %r failed to start: %s", self._server.name, exc)
            self._proc = None
            return False
        return await self._handshake()

    async def _handshake(self) -> bool:
        """Perform the MCP `initialize` exchange.

        The protocol requires this before any other request: a server that
        receives `tools/list` first is entitled to reject it, so skipping the
        handshake makes the client fail against every conformant server.
        """
        resp = await self._send("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "coderouter", "version": __version__},
        })
        if resp is None or "result" not in resp:
            logger.warning("MCP server %r did not complete initialize", self._server.name)
            return False
        # `notifications/initialized` carries no id and gets no reply.
        if not await self._notify("notifications/initialized", {}):
            return False
        self._initialized = True
        return True

    async def _notify(self, method: str, params: dict[str, Any]) -> bool:
        proc = self._proc
        if proc is None or proc.stdin is None:
            return False
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        try:
            proc.stdin.write((json.dumps(msg) + "\n").encode())
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            logger.warning("MCP %r %s failed: %s", self._server.name, method, exc)
            return False
        return True

    async def _send(self, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """Write one request and read the response with the matching id.

        Servers are free to interleave notifications and log messages on
        stdout, so a bare `readline()` can return something that is not the
        answer. Lines are drained until the id matches or the budget runs out.
        """
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdout is None:
            return None
        self._id += 1
        req_id = self._id
        req = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        try:
            proc.stdin.write((json.dumps(req) + "\n").encode())
            await proc.stdin.drain()
            for _ in range(MAX_INTERLEAVED_LINES):
                line = await asyncio.wait_for(proc.stdout.readline(),
                                              timeout=self._timeout)
                if not line:
                    return None
                try:
                    msg = json.loads(line.decode())
                except json.JSONDecodeError:
                    continue  # server chatter on stdout, not our answer
                if isinstance(msg, dict) and msg.get("id") == req_id:
                    return msg
            logger.warning("MCP %r %s: no response among %d lines",
                           self._server.name, method, MAX_INTERLEAVED_LINES)
            return None
        except (TimeoutError, BrokenPipeError, ConnectionResetError) as exc:
            logger.warning("MCP %r %s failed: %s", self._server.name, method, exc)
            return None

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
        async with self._lock:
            if not await self._ensure_started() or not self._initialized:
                return None
            return await self._send(method, params)

    async def list_tools(self) -> list[Tool]:
        resp = await self._request("tools/list", {})
        if resp is None:
            return []
        tools_raw = (resp.get("result") or {}).get("tools") or []
        out: list[Tool] = []
        for raw in tools_raw:
            if not isinstance(raw, dict):
                continue
            out.append(Tool(
                name=str(raw.get("name", "")),
                description=str(raw.get("description", "")),
                input_schema=raw.get("inputSchema") or {},
                source=f"mcp:{self._server.name}",
                token_cost=max(len(str(raw.get("description", ""))) // 4, 1),
            ))
        return out

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        resp = await self._request("tools/call", {"name": name, "arguments": arguments})
        if resp is None:
            return None
        if "error" in resp:
            return {"ok": False, "error": str(resp["error"])}
        return {"ok": True, "result": (resp.get("result") or {})}

    async def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5)
        except (TimeoutError, ProcessLookupError):
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
