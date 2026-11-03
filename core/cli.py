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
import stat
import sys
from pathlib import Path

from .discovery import HardwareDiscovery
from .authorization import AuthorizationDenied, authorize_plan_from_tty
from .sentinel import SentinelGate, SentinelVerdict
from .verifier import AntiFakeVerifier, VerificationVerdict
from .rollback import RollbackController
from .executor import DryRunExecutor, SyntheticWorkloadExecutor
from .experiment import SyntheticExperimentPlan, SyntheticExperimentRunner
from .planning import ProposalValidationError, SyntheticExperimentProposal
from .recovery import inspect_synthetic_campaign
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


def cmd_dry_run(args):
    receipt = DryRunExecutor().replay_fixture(args.fixture)
    print(json.dumps(receipt.to_dict(), indent=2))


def cmd_run_synthetic(args):
    receipt = SyntheticWorkloadExecutor().run(args.workload)
    print(json.dumps(receipt.to_dict(), indent=2))
    if receipt.status != "COMPLETED":
        sys.exit(1)


def cmd_show_synthetic_plan(args):
    plan = SyntheticExperimentPlan.fixed_repeatability_plan()
    proposal = _fixed_synthetic_proposal(plan)
    print(json.dumps({
        "plan": plan.to_dict(),
        "plan_sha256": plan.sha256,
        "proposal": proposal.to_dict(),
        "proposal_sha256": proposal.sha256,
        "authorization": "Run with --proposal FILE in a local interactive terminal",
    }, indent=2))


def _fixed_synthetic_proposal(plan=None):
    return SyntheticExperimentProposal(
        schema_version=1,
        proposal_id="ihra-fixed-synthetic-repeatability",
        rationale="Verify five identical results from the reviewed fixed workload.",
        plan=plan or SyntheticExperimentPlan.fixed_repeatability_plan(),
    )


def cmd_show_synthetic_proposal(args):
    proposal = _fixed_synthetic_proposal()
    print(json.dumps(proposal.to_dict(), indent=2))


def _load_synthetic_proposal(path: str) -> SyntheticExperimentProposal:
    fd = os.open(
        path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    )
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise ProposalValidationError("Proposal must be a regular file")
        if metadata.st_size > SyntheticExperimentProposal.MAX_PROPOSAL_BYTES:
            raise ProposalValidationError("Proposal exceeds the size limit")
        chunks = []
        remaining = SyntheticExperimentProposal.MAX_PROPOSAL_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(4096, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
    finally:
        os.close(fd)
    return SyntheticExperimentProposal.from_bytes(data)


def cmd_review_synthetic_proposal(args):
    try:
        proposal = _load_synthetic_proposal(args.proposal)
    except (OSError, ProposalValidationError) as exc:
        print(json.dumps({"status": "DENIED", "verdict": "DENY", "reason": str(exc)}))
        sys.exit(2)
    print(json.dumps({
        "status": "REVIEWED",
        "eligible": True,
        "proposal": proposal.to_dict(),
        "proposal_sha256": proposal.sha256,
        "plan_sha256": proposal.plan.sha256,
        "execution_authority": "none; review does not authorize or execute",
    }, indent=2))


def cmd_run_synthetic_experiment(args):
    try:
        proposal = _load_synthetic_proposal(args.proposal)
        authorization = authorize_plan_from_tty(
            proposal.plan.sha256,
            proposal.sha256,
            proposal.canonical_bytes().decode("ascii"),
        )
    except (OSError, ProposalValidationError, AuthorizationDenied) as exc:
        print(json.dumps({"status": "DENIED", "verdict": "DENY", "reason": str(exc)}))
        sys.exit(1)
    except KeyboardInterrupt:
        print(json.dumps({
            "status": "DENIED",
            "verdict": "DENY",
            "reason": "TTY authorization cancelled",
        }))
        sys.exit(130)
    result = SyntheticExperimentRunner().run(
        proposal.plan,
        authorization=authorization,
        proposal_bytes=proposal.canonical_bytes(),
    )
    print(json.dumps(result.to_dict(), indent=2))
    if result.verdict != "DEMONSTRATED":
        sys.exit(1)


def cmd_inspect_synthetic_campaign(args):
    result = inspect_synthetic_campaign(args.campaign_id)
    print(json.dumps(result.to_dict(), indent=2))
    if result.inspection_status != "COMPLETED_VALID":
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

    # dry-run
    p_dry = subparsers.add_parser(
        "dry-run",
        help="Replay bundled synthetic evidence without executing commands or accessing hardware",
    )
    p_dry.add_argument(
        "fixture",
        choices=sorted(DryRunExecutor.FIXTURES),
        help="Bundled synthetic fixture to replay",
    )
    p_dry.set_defaults(func=cmd_dry_run)

    # run a single reviewed synthetic workload inside a rootless sandbox
    p_run = subparsers.add_parser(
        "run-synthetic",
        help="Run the fixed synthetic CPU workload in a rootless bubblewrap sandbox",
    )
    p_run.add_argument(
        "workload",
        choices=[SyntheticWorkloadExecutor.WORKLOAD_ID],
        help="Reviewed workload identifier; arbitrary commands are not accepted",
    )
    p_run.set_defaults(func=cmd_run_synthetic)

    p_plan = subparsers.add_parser(
        "show-synthetic-plan",
        help="Display the only eligible five-sample plan and a proposal example",
    )
    p_plan.set_defaults(func=cmd_show_synthetic_plan)

    p_proposal = subparsers.add_parser(
        "show-synthetic-proposal",
        help="Emit the strict JSON proposal for the only eligible synthetic plan",
    )
    p_proposal.set_defaults(func=cmd_show_synthetic_proposal)

    p_review = subparsers.add_parser(
        "review-synthetic-proposal",
        help="Validate a proposal as inert data; this does not authorize or execute it",
    )
    p_review.add_argument("proposal", help="Path to a strict JSON proposal file")
    p_review.set_defaults(func=cmd_review_synthetic_proposal)

    p_campaign = subparsers.add_parser(
        "run-synthetic-experiment",
        help="Review and locally authorize a proposal before its fixed sandbox campaign",
    )
    p_campaign.add_argument(
        "--proposal",
        required=True,
        help="Path to a strict JSON proposal file; a controlling TTY is mandatory",
    )
    p_campaign.set_defaults(func=cmd_run_synthetic_experiment)

    p_inspect_campaign = subparsers.add_parser(
        "inspect-synthetic-campaign",
        help="Inspect campaign evidence read-only without resuming or executing it",
    )
    p_inspect_campaign.add_argument(
        "campaign_id",
        help="Campaign UUID from the evidence directory; arbitrary paths are rejected",
    )
    p_inspect_campaign.set_defaults(func=cmd_inspect_synthetic_campaign)

    # setup
    p_setup = subparsers.add_parser("setup", help="Verify stack paths and dependencies")
    p_setup.set_defaults(func=cmd_setup)

    parsed = parser.parse_args()
    parsed.func(parsed)


if __name__ == "__main__":
    main()
