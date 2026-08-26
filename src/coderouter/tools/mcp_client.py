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

from ..logging import get_logger
from .models import MCPServer, Tool

logger = get_logger("tools.mcp_client")


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

    async def _ensure_started(self) -> bool:
        if self._proc is not None and self._proc.returncode is None:
            return True
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
        return True

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
        async with self._lock:
            if not await self._ensure_started():
                return None
            self._id += 1
            req = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
            assert self._proc is not None
            try:
                self._proc.stdin.write((json.dumps(req) + "\n").encode())
                await self._proc.stdin.drain()
                line = await asyncio.wait_for(self._proc.stdout.readline(),
                                              timeout=self._timeout)
            except (TimeoutError, BrokenPipeError, ConnectionResetError) as exc:
                logger.warning("MCP %r %s failed: %s", self._server.name, method, exc)
                return None
            if not line:
                return None
            try:
                return json.loads(line.decode())
            except json.JSONDecodeError:
                return None

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
