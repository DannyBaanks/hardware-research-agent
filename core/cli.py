# SPDX-License-Identifier: MIT
"""
Universal Hardware Research Agent (IHRA) CLI.
Connects REA (Binary Forensics), GPU-Unlock-Ada (GPU Canaries & D3cold),
and HRA (Hardware Research Protocol & Safety Sentinel) in a single unified,
zero-bloat CLI interface for agents without heavy MCP or TUI overhead.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from .discovery import HardwareDiscovery
from .sentinel import SentinelGate, SentinelVerdict
from .verifier import AntiFakeVerifier, VerificationVerdict
from .rollback import RollbackController
from adapters.adapters import (
    LinuxInventoryAdapter,
    LinuxCpuPerfAdapter,
    NvidiaTelemetryAdapter,
    AmdXdnaDiscoveryAdapter
)
from adapters.rea_adapter import ReaAdapter
from adapters.ada_adapter import AdaUnlockAdapter


def cmd_discover(args):
    discovery = HardwareDiscovery()
    inv = discovery.discover()

    # Query peripheral adapters
    rea = ReaAdapter()
    rea_info = rea.read_telemetry()

    ada = AdaUnlockAdapter()
    ada_info = ada.read_telemetry()

    xdna = AmdXdnaDiscoveryAdapter()
    xdna_info = xdna.read_telemetry() if xdna.probe().status.value == "SUPPORTED" else {"status": "ABSENT"}

    cpu_perf = LinuxCpuPerfAdapter()
    perf_status = cpu_perf.probe().status.value

    output = {
        "protocol": "IHRA/1.0",
        "system": {
            "cpu_model": inv.cpu_model,
            "physical_cores": inv.physical_cores,
            "logical_threads": inv.logical_threads,
            "vector_isa": inv.vector_isa,
            "has_avx512": inv.has_avx512,
            "platform_profile_current": inv.platform_profile_current,
            "platform_profile_choices": inv.platform_profile_choices,
            "cpu_perf_adapter": perf_status
        },
        "npu_xdna": xdna_info,
        "gpu_ada_unlock": ada_info,
        "rea_reverse_tools": rea_info
    }

    if args.json:
        print(json.dumps(output, indent=2))
    else:
        print("=== Hardware Research Agent - Environment Discovery ===")
        print(f"CPU: {inv.cpu_model} ({inv.physical_cores}C/{inv.logical_threads}T)")
        print(f"AVX-512 Vector ISA: {'Supported' if inv.has_avx512 else 'Not detected'}")
        print(f"Power Profile: {inv.platform_profile_current} (Choices: {', '.join(inv.platform_profile_choices)})")
        print(f"NPU XDNA: {xdna_info.get('device_node', 'Not detected')}")
        print(f"GPU Ada Canary: {ada_info.get('gpu_name', 'Unknown')} (Device: {ada_info.get('device_id', 'N/A')})")
        print(f"D3cold Persistence: {ada_info.get('d3cold_status', {}).get('status', 'Unknown')}")
        print(f"REA Reverse Suite: {rea_info.get('status', 'Unavailable')} (Path: {rea_info.get('rea_path', 'N/A')})")


def cmd_inspect(args):
    rea = ReaAdapter()
    tool = args.tool or "inspect_malbolge"
    result = rea.inspect_binary(args.target, tool_name=tool)
    print(json.dumps(result, indent=2))


def cmd_ada(args):
    ada = AdaUnlockAdapter()
    telemetry = ada.read_telemetry()
    d3cold = ada.read_d3cold_status()
    result = {
        "telemetry": telemetry,
        "d3cold_analysis": d3cold,
        "recommendation": (
            "The driver parameter is configured to disable runtime dynamic power management; "
            "hardware persistence and unlock safety remain NOT_DEMONSTRATED."
            if d3cold.get("driver_parameter_indicates_prevention")
            else "The driver parameter does not indicate D3cold prevention; hardware state is NOT_DEMONSTRATED."
        ),
        "recommendation_status": "NOT_DEMONSTRATED",
    }
    print(json.dumps(result, indent=2))


def cmd_sentinel(args):
    gate = SentinelGate()
    plan = {
        "experiment_id": "cli_eval_check",
        "workload": {"command": args.action},
    }
    grant = gate.evaluate_plan(plan)
    result = {
        "action": args.action,
        "risk_tier": grant.risk_tier,
        "verdict": grant.verdict.value,
        "allowed": grant.verdict == SentinelVerdict.AUTHORIZED,
        "reason": grant.reason,
        "allowed_operations": grant.allowed_operations
    }
    print(json.dumps(result, indent=2))
    if grant.verdict != SentinelVerdict.AUTHORIZED:
        sys.exit(1)


def cmd_setup(args):
    """Checks and reports repository and toolchain status for agents."""
    base_dir = Path.home() / "Development"
    rea_path = base_dir / "rea"
    ada_path = base_dir / "gpu-unlock-hp" / "gpu-unlock-ada"
    hra_path = base_dir / "ISyCo Git" / "hardware-research-agent"

    status = {
        "ihra_agent": {
            "path": str(hra_path),
            "exists": hra_path.exists(),
            "repo": "https://github.com/DannyBaanks/hardware-research-agent"
        },
        "rea_reverse_tools": {
            "path": str(rea_path),
            "exists": rea_path.exists(),
            "repo": "https://github.com/DannyBaanks/rea",
            "has_compact_profile": True
        },
        "gpu_unlock_ada": {
            "path": str(ada_path),
            "exists": ada_path.exists(),
            "repo": "https://github.com/DannyBaanks/gpu-unlock-ada",
            "canary_pr": "#134"
        }
    }
    print(json.dumps(status, indent=2))


def main():
    parser = argparse.ArgumentParser(
        prog="ihra",
        description="Hardware Research Agent CLI - Lean, verifiable hardware forensics and optimization bridge."
    )
    parser.add_argument("--json", action="store_true", default=True, help="Emit JSON output (default)")
    parser.add_argument("--text", dest="json", action="store_false", help="Emit human-readable text")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # discover
    p_disc = subparsers.add_parser("discover", help="Discover CPU, GPU, NPU, and REA capabilities")
    p_disc.set_defaults(func=cmd_discover)

    # inspect
    p_insp = subparsers.add_parser("inspect", help="Run REA binary analysis / disassembly")
    p_insp.add_argument("target", help="Target file to analyze")
    p_insp.add_argument("--tool", default="inspect_malbolge", help="Tool name (e.g. inspect_malbolge, trace_malbolge)")
    p_insp.set_defaults(func=cmd_inspect)

    # ada
    p_ada = subparsers.add_parser("ada", help="Check Ada Lovelace GPU unlock & D3cold power rail status")
    p_ada.set_defaults(func=cmd_ada)

    # sentinel
    p_sent = subparsers.add_parser("sentinel", help="Evaluate an action against safety contracts")
    p_sent.add_argument("action", help="Exact read-only command to check; no command is executed")
    p_sent.set_defaults(func=cmd_sentinel)

    # setup
    p_setup = subparsers.add_parser("setup", help="Verify stack paths and dependencies")
    p_setup.set_defaults(func=cmd_setup)

    parsed = parser.parse_args()
    parsed.func(parsed)


if __name__ == "__main__":
    main()
