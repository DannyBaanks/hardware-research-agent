# SPDX-License-Identifier: MIT
"""
REA (Reverse Engineer Anything) Adapter for Hardware Research Agent.
Provides lightweight CLI integration with DannyBaanks/rea without heavy MCP server bloat.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional
from .base import HardwareAdapter, CapabilityStatus, AdapterProbeResult


class ReaAdapter(HardwareAdapter):
    name = "rea_reverse_tools"
    is_read_only = True

    def __init__(self, rea_path: Optional[str] = None):
        self.rea_path = rea_path or os.environ.get(
            "REA_HOME",
            str(Path.home() / "Development" / "rea")
        )

    def probe(self) -> AdapterProbeResult:
        # Check if local rea clone exists or if rea / npx rea-agents is on PATH
        has_local = os.path.exists(os.path.join(self.rea_path, "scripts", "rea.mjs"))
        has_cli = shutil.which("rea") is not None or shutil.which("npx") is not None

        if not (has_local or has_cli):
            return AdapterProbeResult(
                status=CapabilityStatus.NOT_APPLICABLE,
                reason="Neither local REA clone nor rea/npx CLI found on host"
            )

        details = {}
        if has_local:
            details["rea_local_path"] = self.rea_path
            details["compact_profile_supported"] = True
            details["malbolge_suite_supported"] = True
        if has_cli:
            details["npx_available"] = shutil.which("npx") is not None

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
            "capabilities": [
                "compact_mcp_catalog",
                "static_binary_disassembly",
                "bounded_vm_tracing",
                "decompilation_projections",
                "cryptographic_state_digests"
            ]
        }
        if probe.telemetry:
            info.update(probe.telemetry)
        return info

    def inspect_binary(self, file_path: str, tool_name: str = "inspect_malbolge") -> Dict[str, Any]:
        """
        Executes a reverse-engineering tool on the target file via REA CLI without MCP daemon.
        """
        if not os.path.exists(file_path):
            return {"status": "ERROR", "error": f"Target file does not exist: {file_path}"}

        script_mjs = os.path.join(self.rea_path, "scripts", "rea.mjs")
        if os.path.exists(script_mjs) and shutil.which("node"):
            cmd = ["node", script_mjs, tool_name, file_path]
        elif shutil.which("npx"):
            cmd = ["npx", "rea-agents", tool_name, file_path]
        else:
            return {"status": "ERROR", "error": "No execution runner (node or npx) found"}

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return {
                "status": "OK" if res.returncode == 0 else "FAIL",
                "exit_code": res.returncode,
                "stdout": res.stdout.strip(),
                "stderr": res.stderr.strip()
            }
        except subprocess.TimeoutExpired:
            return {"status": "TIMEOUT", "error": "REA tool execution timed out"}
        except Exception as e:
            return {"status": "ERROR", "error": str(e)}
