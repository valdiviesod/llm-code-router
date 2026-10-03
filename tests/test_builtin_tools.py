"""The wired tools subsystem (debt plan P1, option (b)).

What these tests prove, in order: grep and edit_file exist and work;
run_command actually runs commands (bounded, sandboxed, gated); ASK verdicts
have a real approval flow; the selection the budget paid for is what the MCP
bridge advertises and enforces; and outputs are bounded by kind, not by a
blind byte cut.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from coderouter.config import PermissionsConfig, SandboxConfig, SecurityConfig
from coderouter.core.models import Risk
from coderouter.security.permissions import PermissionsEngine
from coderouter.security.policy import CommandPolicy, Decision
from coderouter.security.sandbox import Sandbox
from coderouter.tools import (
    BuiltinMCPServer,
    BuiltinToolHost,
    bound_output,
    builtin_tools,
)
from coderouter.tools.mcp_server import mcp_config_for
from coderouter.tools.models import MCPServer


def _host(tmp_path: Path, **kw) -> BuiltinToolHost:
    defaults = dict(
        project_root=tmp_path,
        policy=CommandPolicy(SecurityConfig()),
    )
    defaults.update(kw)
    return BuiltinToolHost(**defaults)


# --- grep -------------------------------------------------------------------


def test_grep_finds_matches_with_anchors(tmp_path: Path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "other.py").write_text("x = 1\n")
    host = _host(tmp_path)
    result = host.grep("return a + b")
    assert result["ok"] is True
    assert result["count"] == 1
    m = result["matches"][0]
    assert m["file"] == "calc.py"
    assert m["line"] == 2
    assert m["col"] == 5
    assert "calc.py:2:5" in result["output"]


def test_grep_skips_vendored_trees(tmp_path: Path):
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "x.js").write_text("needle")
    (tmp_path / "real.py").write_text("needle")
    host = _host(tmp_path)
    result = host.grep("needle")
    assert [m["file"] for m in result["matches"]] == ["real.py"]


def test_grep_caps_results_and_reports_truncation(tmp_path: Path):
    (tmp_path / "big.txt").write_text("needle\n" * 100)
    host = _host(tmp_path)
    result = host.grep("needle", max_results=10)
    assert result["count"] == 10
    assert result["truncated"] is True


# --- edit_file ---------------------------------------------------------------


def test_edit_file_applies_unique_replacement(tmp_path: Path):
    f = tmp_path / "calc.py"
    f.write_text("def add(a, b):\n    return a + b\n")
    host = _host(tmp_path)
    result = host.edit_file("calc.py", "return a + b", "return a - b")
    assert result["ok"] is True
    assert "return a - b" in f.read_text()


def test_edit_file_refuses_ambiguous_match(tmp_path: Path):
    (tmp_path / "dup.py").write_text("same\nsame\n")
    host = _host(tmp_path)
    result = host.edit_file("dup.py", "same", "other")
    assert result["ok"] is False
    assert "2 times" in result["error"]


def test_edit_file_refuses_missing_match(tmp_path: Path):
    (tmp_path / "calc.py").write_text("x = 1\n")
    host = _host(tmp_path)
    result = host.edit_file("calc.py", "not there", "y")
    assert result["ok"] is False
    assert "not found" in result["error"]


def test_edit_file_respects_path_boundary(tmp_path: Path):
    host = _host(tmp_path)
    result = host.edit_file("../outside.py", "a", "b")
    assert result["ok"] is False


def test_edit_file_denied_by_permissions_engine(tmp_path: Path):
    (tmp_path / "calc.py").write_text("x = 1\n")
    engine = PermissionsEngine(PermissionsConfig(rules=[
        {"capability": "filesystem.write", "decision": "deny"},
    ]))
    host = _host(tmp_path, permissions=engine)
    result = host.edit_file("calc.py", "x = 1", "x = 2")
    assert result["ok"] is False
    assert "denied" in result["error"]
    assert (tmp_path / "calc.py").read_text() == "x = 1\n"


# --- run_command: it runs commands -------------------------------------------


def test_run_command_executes_safe_commands(tmp_path: Path):
    host = _host(tmp_path)
    result = host.run_command("echo hello-router")
    assert result["ok"] is True
    assert result["exit_code"] == 0
    assert "hello-router" in result["output"]


def test_run_command_reports_failure_exit_codes(tmp_path: Path):
    host = _host(tmp_path)
    result = host.run_command("exit 3")
    assert result["ok"] is False
    assert result["exit_code"] == 3


def test_run_command_blocks_dangerous_commands(tmp_path: Path):
    host = _host(tmp_path)
    result = host.run_command("rm -rf /")
    assert result["ok"] is False
    assert "blocked" in result["error"]


def test_run_command_ask_is_denied_without_an_approver(tmp_path: Path):
    host = _host(tmp_path)
    result = host.run_command("git push origin main")
    assert result["ok"] is False
    assert "requires_approval" in result["error"]


def test_run_command_ask_is_satisfied_by_the_approval_flow(tmp_path: Path):
    seen: list[str] = []
    host = _host(tmp_path, approver=lambda cmd, reason: seen.append(cmd) or True)
    # ASK from the command policy + an approver that says yes => the command
    # actually runs. `sudo true` is a self-contained ASK verdict.
    result = host.run_command("sudo true")
    assert seen == ["sudo true"]
    assert result["ok"] is True
    assert "requires_approval" not in result


def test_run_command_ask_rejected_by_the_approver(tmp_path: Path):
    host = _host(tmp_path, approver=lambda cmd, reason: False)
    result = host.run_command("git push origin main")
    assert result["ok"] is False
    assert "requires_approval" in result["error"]


def test_run_command_under_permissions_deny(tmp_path: Path):
    engine = PermissionsEngine(PermissionsConfig(rules=[
        {"capability": "shell.execute", "decision": "deny"},
    ]))
    host = _host(tmp_path, permissions=engine)
    result = host.run_command("echo never")
    assert result["ok"] is False
    assert "blocked" in result["error"]
    assert "shell.execute" in result["error"]


def test_run_command_git_push_denied_by_capability_rule(tmp_path: Path):
    engine = PermissionsEngine(PermissionsConfig(rules=[
        {"capability": "git.push", "decision": "deny"},
    ]))
    host = _host(tmp_path, permissions=engine)
    # The command policy alone would say ASK; the capability rule says deny,
    # and the stricter verdict wins.
    result = host.run_command("git push origin main")
    assert result["ok"] is False
    assert "git.push" in result["error"]


def test_run_command_times_out(tmp_path: Path):
    host = _host(tmp_path, command_timeout_s=2)
    result = host.run_command("sleep 30")
    assert result["ok"] is False
    assert result["exit_code"] == 124
    assert "timed out" in result["output"]


def test_run_command_output_is_collapsed_and_bounded(tmp_path: Path):
    host = _host(tmp_path)
    result = host.run_command("printf 'spam\\n%.0s' $(seq 1 500)")
    assert result["ok"] is True
    assert "x 5" in result["output"] or "x 4" in result["output"] or "x 500" in result["output"], (
        f"repeats must collapse to a count, got: {result['output'][:200]!r}"
    )


# --- dispatch + selection enforcement ----------------------------------------


def test_call_dispatches_and_validates(tmp_path: Path):
    (tmp_path / "f.txt").write_text("content\n")
    host = _host(tmp_path)
    assert host.call("read_file", {"path": "f.txt"})["ok"] is True
    assert host.call("nope", {})["ok"] is False
    assert "unknown tool" in host.call("nope", {})["error"]


def test_call_refuses_tools_outside_the_run_selection(tmp_path: Path):
    host = _host(tmp_path, allowed_tools=frozenset({"read_file"}))
    ok = host.call("read_file", {"path": "anything"})
    refused = host.call("run_command", {"command": "echo hi"})
    assert ok["ok"] is False  # file does not exist, but it was *allowed* to try
    assert ok["error"] == "not_found"
    assert refused["ok"] is False
    assert "not in this run's selection" in refused["error"]


# --- the MCP bridge -----------------------------------------------------------


def _serve_line(server: BuiltinMCPServer, message: dict) -> dict | None:
    return server.handle(message)


def test_mcp_server_initialize_handshake(tmp_path: Path):
    server = BuiltinMCPServer(tmp_path)
    resp = _serve_line(server, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                "params": {}})
    assert resp is not None
    assert resp["result"]["serverInfo"]["name"] == "coderouter"
    assert resp["result"]["protocolVersion"] == "2024-11-05"


def test_mcp_server_lists_only_allowed_tools(tmp_path: Path):
    server = BuiltinMCPServer(tmp_path, ["read_file", "grep"])
    resp = _serve_line(server, {"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                                "params": {}})
    assert resp is not None
    names = {t["name"] for t in resp["result"]["tools"]}
    assert names == {"read_file", "grep"}


def test_mcp_server_calls_a_tool_end_to_end(tmp_path: Path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    server = BuiltinMCPServer(tmp_path)
    resp = _serve_line(server, {
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "grep", "arguments": {"pattern": "return a + b"}},
    })
    assert resp is not None
    assert resp["result"]["isError"] is False
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["ok"] is True
    assert payload["matches"][0]["file"] == "calc.py"


def test_mcp_bridge_over_a_real_subprocess(tmp_path: Path):
    """The bridge as an agent CLI sees it: a spawned stdio JSON-RPC process,
    driven by the router's own MCP client."""
    import asyncio

    from coderouter.tools import MCPClient

    (tmp_path / "ok.py").write_text("print('via bridge')\n")
    server = MCPServer(
        name="coderouter-bridge",
        command=sys.executable,
        args=("-m", "coderouter.tools.mcp_server", "--root", str(tmp_path),
              "--allow", "read_file"),
    )

    async def scenario() -> None:
        client = MCPClient(server)
        try:
            tools = await client.list_tools()
            assert [t.name for t in tools] == ["read_file"]
            result = await client.call_tool("read_file", {"path": "ok.py"})
            assert result is not None and result["ok"] is True
            assert "via bridge" in result["result"]["content"][0]["text"]
        finally:
            await client.close()

    asyncio.run(scenario())


def test_mcp_config_payload_points_at_the_server(tmp_path: Path):
    payload = mcp_config_for(["read_file", "grep"], tmp_path)
    entry = payload["mcpServers"]["coderouter"]
    assert entry["command"] == sys.executable
    assert "--allow" in entry["args"]
    i = entry["args"].index("--allow")
    assert entry["args"][i + 1] == "read_file,grep"
    assert str(tmp_path) in entry["args"]


def test_builtin_tools_declared_and_implemented():
    """The gap the debt plan recorded: four tools declared, only two
    implemented. The dispatch table in the host is the proof of parity."""
    host = BuiltinToolHost(project_root=Path("."), policy=CommandPolicy(SecurityConfig()))
    declared = {t.name for t in builtin_tools()}
    assert declared == {"read_file", "grep", "edit_file", "run_command"}
    for name in declared:
        assert host.call(name, {}).get("error") != f"unknown tool: {name}"


# --- output bounds -------------------------------------------------------------


def test_failures_only_keeps_failures_and_the_summary():
    text = "PASSED t1\nPASSED t2\nFAILED t3 - assert 1 == 2\n1 failed, 2 passed in 0.1s"
    out = bound_output(text, "tests", 4_000)
    assert "FAILED t3" in out
    assert "1 failed, 2 passed" in out
    assert "PASSED t1" not in out


def test_changed_files_first_lists_files_before_the_diff():
    diff = ("diff --git a/x.py b/x.py\n+++ b/x.py\n@@\n-old\n+new\n"
            "diff --git a/y.py b/y.py\n+++ b/y.py\n@@\n-o\n+n\n")
    out = bound_output(diff, "diff", 4_000)
    assert out.startswith("changed files:")
    assert "- x.py" in out and "- y.py" in out
    assert "+new" in out


def test_log_repeats_collapse():
    out = bound_output("tick\ntick\ntick\nboom\n", "log", 4_000)
    assert "x 3" in out
    assert "boom" in out


def test_prose_bound_keeps_head_and_tail():
    text = "A" * 3000 + "MIDDLE" + "B" * 3000
    out = bound_output(text, "prose", 1_000)
    assert len(out) < 1_500
    assert "elided" in out
    assert out.startswith("A") and out.endswith("B")


# --- sandbox --------------------------------------------------------------------


def test_sandbox_path_boundary():
    sandbox = Sandbox(SandboxConfig())
    root = Path("/tmp")
    assert sandbox.check_path(Path("/tmp/project/x.py"), root) is True
    assert sandbox.check_path(Path("/etc/passwd"), root) is False


def test_sandbox_scrubs_credential_env():
    sandbox = Sandbox(SandboxConfig())
    env = sandbox.scrub_env({
        "PATH": "/bin", "API_TOKEN": "x", "MY_KEY": "y",
        "AWS_SECRET_ACCESS_KEY": "z", "HOME": "/root",
    })
    assert env == {"PATH": "/bin", "HOME": "/root"}


def test_sandbox_memory_limit_is_enforced():
    sandbox = Sandbox(SandboxConfig(enabled=True, memory_mb=64))
    code, out, err = sandbox.run(
        [sys.executable, "-c", "x = bytearray(500 * 1024 * 1024)"],
        cwd=Path("."), timeout_s=30,
    )
    assert code != 0
    assert "MemoryError" in err or "Cannot allocate" in err or code == 137


def test_sandbox_timeout_kills_the_process():
    sandbox = Sandbox(SandboxConfig(enabled=False))
    code, _, err = sandbox.run([sys.executable, "-c", "import time; time.sleep(30)"],
                               cwd=Path("."), timeout_s=2)
    assert code == 124
    assert "timed out" in err


# --- permissions engine ------------------------------------------------------------


def test_permissions_default_allow():
    engine = PermissionsEngine(PermissionsConfig())
    decision, _ = engine.check("shell.execute", "echo hi")
    assert decision is Decision.SAFE


def test_permissions_deny_rule():
    engine = PermissionsEngine(PermissionsConfig(rules=[
        {"capability": "git.push", "decision": "deny"},
    ]))
    assert engine.check("git.push", "git push origin main")[0] is Decision.BLOCK
    assert engine.check("shell.execute", "echo hi")[0] is Decision.SAFE


def test_permissions_pattern_beats_capability_only():
    engine = PermissionsEngine(PermissionsConfig(rules=[
        {"capability": "shell.execute", "decision": "allow"},
        {"capability": "shell.execute", "decision": "ask", "pattern": "docker .*"},
    ]))
    assert engine.check("shell.execute", "echo hi")[0] is Decision.SAFE
    assert engine.check("shell.execute", "docker run x")[0] is Decision.ASK


def test_permissions_bad_rule_is_ignored_with_a_warning(caplog):
    engine = PermissionsEngine(PermissionsConfig(rules=[
        {"capability": "shell.execute", "decision": "maybe"},
    ]))
    assert engine.check("shell.execute", "x")[0] is Decision.SAFE


# --- orchestrator wiring ------------------------------------------------------------


async def test_selected_tools_are_injected_into_the_prompt(orchestrator, tmp_path):
    """The selector's output used to be assigned and never read. Now the
    prompt carries the block, and the block is not duplicated on retry."""
    from coderouter.core.models import Task

    task = Task(prompt="fix the bug", project_root=tmp_path, risk=Risk.HIGH)
    outcome = await orchestrator.run_task(task)
    assert "[coderouter tools]" in task.prompt
    assert task.selected_tool_ids
    assert task.prompt.count("[coderouter tools]") == 1
    assert outcome.result is not None
