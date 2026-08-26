"""Tools subsystem.

A Tool is a callable capability. The router collects tools from three
sources: builtin (read_file, grep, edit_file, run_command), adapter
declarations, and MCP servers. The selector picks a budgeted subset
per task, honouring the task's risk profile.
"""

from .builtin import BuiltinToolHost, builtin_tools
from .mcp_client import MCPClient
from .models import MCPServer, Tool
from .registry import ToolRegistry
from .selector import ToolSelector

__all__ = [
    "BuiltinToolHost",
    "MCPClient",
    "MCPServer",
    "Tool",
    "ToolRegistry",
    "ToolSelector",
    "builtin_tools",
]
