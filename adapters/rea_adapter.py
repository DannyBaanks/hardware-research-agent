# SPDX-License-Identifier: MIT
"""
REA (Reverse Engineer Anything) Adapter for Hardware Research Agent.
Provides lightweight CLI integration with DannyBaanks/rea without heavy MCP server bloat.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional
from .base import HardwareAdapter, CapabilityStatus, AdapterProbeResult


class ReaAdapter(HardwareAdapter):
    name = "rea_reverse_tools"
    is_read_only = True

    DEFAULT_TOOL = "inspect-artifact"
    # These exist only on REA's MCP surface (src/cliCommandNames.ts,
    # MCP_TOOLS_WITHOUT_DEDICATED_CLI); the CLI answers COMMAND_NOT_FOUND.
    MCP_ONLY_TOOLS = frozenset({
        "inspect-malbolge", "trace-malbolge", "decompile-malbolge",
        "rea-discover", "rea-describe", "rea-execute",
    })
    _TOOL_NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")

    def __init__(self, rea_path: Optional[str] = None):
        self.rea_path = rea_path or os.environ.get(
            "REA_HOME",
            str(Path.home() / "Development" / "rea")
        )

    def probe(self) -> AdapterProbeResult:
        # A local clone or an installed `rea` binary. A bare `npx` is not REA:
        # running `npx rea-agents` would download and execute code from npm.
        has_local = os.path.exists(os.path.join(self.rea_path, "scripts", "rea.mjs")) \
            and shutil.which("node") is not None
        has_cli = shutil.which("rea") is not None

        if not (has_local or has_cli):
            return AdapterProbeResult(
                status=CapabilityStatus.NOT_APPLICABLE,
                reason="Neither a local REA clone (with node) nor an installed rea CLI was found"
            )

        details = {}
        if has_local:
            details["rea_local_path"] = self.rea_path
        if has_cli:
            details["rea_cli_path"] = shutil.which("rea")

        return AdapterProbeResult(
            status=CapabilityStatus.SUPPORTED,
            device_id="rea_binary_analysis_suite",
            telemetry=details
        )

    def read_telemetry(self) -> Dict[str, Any]:
        probe = self.probe()
        if probe.status != CapabilityStatus.SUPPORTED:
            return {"status": probe.status.value, "reason": probe.reason}

        info = {
            "status": "ready",
            "rea_path": self.rea_path if os.path.exists(self.rea_path) else None,
            "default_cli_tool": self.DEFAULT_TOOL,
            "capabilities_verified": False,
        }
        if probe.telemetry:
            info.update(probe.telemetry)
        return info

    def inspect_binary(self, file_path: str, tool_name: str = DEFAULT_TOOL) -> Dict[str, Any]:
        """
        Executes one REA CLI command on the target file without the MCP daemon.
        """
        tool = (tool_name or self.DEFAULT_TOOL).strip().lower().replace("_", "-")
        if not self._TOOL_NAME.fullmatch(tool):
            return {"status": "ERROR", "error": f"Invalid REA tool name: {tool_name!r}"}
        if tool in self.MCP_ONLY_TOOLS:
            return {
                "status": "ERROR",
                "error": f"'{tool}' is only available through REA's MCP server, not its CLI",
            }
        if not os.path.isfile(file_path):
            return {"status": "ERROR", "error": f"Target file does not exist: {file_path}"}
        # Absolute path so a target named like '-x' can never be parsed as a flag.
        target = os.path.abspath(file_path)

        script_mjs = os.path.join(self.rea_path, "scripts", "rea.mjs")
        if os.path.exists(script_mjs) and shutil.which("node"):
            cmd = ["node", script_mjs, tool, target]
        elif shutil.which("rea"):
            cmd = ["rea", tool, target]
        else:
            return {"status": "ERROR", "error": "No local REA clone with node and no rea CLI found"}

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return {
                "status": "OK" if res.returncode == 0 else "FAIL",
                "tool": tool,
                "exit_code": res.returncode,
                "stdout": res.stdout.strip(),
                "stderr": res.stderr.strip()
            }
        except subprocess.TimeoutExpired:
            return {"status": "TIMEOUT", "tool": tool, "error": "REA tool execution timed out"}
        except OSError as e:
            return {"status": "ERROR", "tool": tool, "error": str(e)}
