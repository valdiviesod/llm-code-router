"""Tools subsystem.

A Tool is a callable capability. The router collects tools from three
sources: builtin (read_file, grep, edit_file, run_command — implemented by
`BuiltinToolHost` and served to agents over the MCP bridge), adapter
declarations, and MCP servers. The selector picks a budgeted subset
per task, honouring the task's risk profile, and the chosen subset is what
the MCP bridge advertises for that run — so the budget is enforced, not
just recorded.
"""

from .builtin import BuiltinToolHost, builtin_tools
from .mcp_client import MCPClient
from .mcp_server import BuiltinMCPServer, mcp_config_for
from .models import MCPServer, Tool
from .output_bounds import bound_output
from .registry import ToolRegistry
from .selector import ToolSelector

__all__ = [
    "BuiltinMCPServer",
    "BuiltinToolHost",
    "MCPClient",
    "MCPServer",
    "Tool",
    "ToolRegistry",
    "ToolSelector",
    "builtin_tools",
    "bound_output",
    "mcp_config_for",
]
