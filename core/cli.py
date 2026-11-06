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
from .cpu_power import CpuPowerInspector
from .authorization import AuthorizationDenied, authorize_plan_from_tty
from .sentinel import SentinelGate, SentinelVerdict, canonical_plan_digest, tier1_intervention_digest
from .tier1 import Tier1ExperimentRunner, build_tier1_plan
from .recovery import FaultStateError, find_unresolved_faults, inspect_synthetic_campaign
from .verifier import AntiFakeVerifier, VerificationVerdict
from .rollback import RollbackController
from .executor import DryRunExecutor, SyntheticWorkloadExecutor
from .experiment import SyntheticExperimentPlan, SyntheticExperimentRunner
from .planning import ProposalValidationError, SyntheticExperimentProposal
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
    cpu_power = CpuPowerInspector().inspect()

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
        "cpu_power": cpu_power.to_dict(),
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
        boost = {True: "on", False: "OFF", None: "unknown"}[cpu_power.boost_enabled]
        power = {True: "charger", False: "battery", None: "unknown"}[cpu_power.on_ac_power]
        print(f"CPU boost: {boost} (max now {cpu_power.current_max_mhz} MHz, hardware {cpu_power.hardware_max_mhz} MHz) | Power: {power} | Busy: {cpu_power.busy_percent}%")
        if cpu_power.hints:
            print("\nThings to check before measuring anything:")
            for hint in cpu_power.hints:
                print(f"  [{hint['id']}] {hint['detail']}")
        else:
            print("\nNo obvious hidden switches found. Measure before you change anything.")


def cmd_inspect(args):
    rea = ReaAdapter()
    tool = args.tool or ReaAdapter.DEFAULT_TOOL
    result = rea.inspect_binary(args.target, tool_name=tool)
    print(json.dumps(result, indent=2))
    if result.get("status") != "OK":
        sys.exit(1)


def cmd_ada(args):
    ada = AdaUnlockAdapter()
    telemetry = ada.read_telemetry()
    d3cold = ada.read_d3cold_status()
    if d3cold.get("driver_parameter_indicates_prevention"):
        recommendation = (
            "The driver parameter is configured to disable runtime dynamic power management; "
            "hardware persistence and unlock safety remain NOT_DEMONSTRATED."
        )
    elif d3cold.get("status") == "NOT_EXPOSED" or "driver_parameter_indicates_prevention" not in d3cold:
        recommendation = (
            "The DynamicPowerManagement parameter could not be read (is the nvidia module loaded?); "
            "D3cold configuration and hardware state are NOT_DEMONSTRATED."
        )
    else:
        recommendation = (
            "The driver parameter does not indicate D3cold prevention; hardware state is NOT_DEMONSTRATED."
        )
    result = {
        "telemetry": telemetry,
        "d3cold_analysis": d3cold,
        "recommendation": recommendation,
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


def _load_tier1_plan(args):
    if bool(args.plan) == bool(args.set_profile):
        raise ValueError("run-tier1 needs exactly one of --plan or --set")
    if args.plan:
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        if not isinstance(plan, dict):
            raise ValueError("Tier-1 plan must be a JSON object")
        return plan
    return build_tier1_plan(
        args.set_profile, RollbackController().capture_state("powerprofilesctl get")
    )


def cmd_run_tier1(args):
    try:
        plan = _load_tier1_plan(args)
    except (OSError, ValueError, json.JSONDecodeError, UnicodeError) as exc:
        print(json.dumps({"status": "DENIED", "verdict": "DENY", "reason": str(exc)}))
        sys.exit(2)

    # The same bytes are judged twice on purpose: what an unattended caller is
    # told, and what a human at this TTY is asked to confirm.
    unattended = SentinelGate().evaluate_plan(plan)
    intervention_digest = tier1_intervention_digest(plan)
    if intervention_digest is None:
        reason = unattended.reason if unattended.verdict is SentinelVerdict.DENIED else (
            "Plan declares no verifiable Tier-1 intervention"
        )
        print(json.dumps({"status": "DENIED", "verdict": "DENY", "reason": reason}, indent=2))
        sys.exit(1)

    plan_digest = canonical_plan_digest(plan)
    summary = json.dumps(plan, sort_keys=True, separators=(",", ":"), allow_nan=False)
    try:
        authorization = authorize_plan_from_tty(
            plan_digest,
            plan_digest,
            summary,
            intervention_sha256=intervention_digest,
        )
    except (OSError, AuthorizationDenied) as exc:
        print(json.dumps({"status": "DENIED", "verdict": "DENY", "reason": str(exc)}))
        sys.exit(1)
    except KeyboardInterrupt:
        print(json.dumps({
            "status": "DENIED",
            "verdict": "DENY",
            "reason": "TTY authorization cancelled",
        }))
        sys.exit(130)

    result = Tier1ExperimentRunner().run(plan, authorization)
    output = result.to_dict()
    output["unattended_review"] = {
        "verdict": unattended.verdict.value,
        "reason": unattended.reason,
    }
    print(json.dumps(output, indent=2))
    if result.verdict != "DEMONSTRATED":
        sys.exit(1)


def cmd_list_faults(args):
    try:
        faults = find_unresolved_faults()
    except FaultStateError as exc:
        print(json.dumps({"status": "DENIED", "verdict": "DENY", "reason": str(exc)}, indent=2))
        sys.exit(1)
    print(json.dumps({"unresolved_faults": faults, "count": len(faults)}, indent=2))
    if faults:
        sys.exit(1)


def cmd_resolve_fault(args):
    try:
        record = Tier1ExperimentRunner().resolve(args.fault_id)
    except (OSError, ValueError, FaultStateError) as exc:
        print(json.dumps({"status": "DENIED", "verdict": "DENY", "reason": str(exc)}, indent=2))
        sys.exit(1)
    print(json.dumps(record, indent=2, sort_keys=True))
    if record["status"] != "RESOLVED":
        sys.exit(1)


def cmd_setup(args):
    """Checks and reports repository and toolchain status for agents."""
    # IHRA reports the repository it is actually running from, not the folder
    # layout one developer happens to keep.
    hra_path = Path(__file__).resolve().parent.parent
    # The companion toolkits are optional and live wherever their owner put them.
    # The ~/Development paths are a fallback guess; the adapters read the same two
    # variables, so what setup reports is what they would really use.
    base_dir = Path.home() / "Development"
    rea_path = Path(os.environ.get("REA_HOME") or base_dir / "rea")
    ada_path = Path(
        os.environ.get("ADA_UNLOCK_HOME")
        or base_dir / "gpu-unlock-hp" / "gpu-unlock-ada"
    )

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
            "has_cli_entrypoint": (rea_path / "scripts" / "rea.mjs").exists()
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
    p_insp.add_argument(
        "--tool",
        default=None,
        help="REA CLI command (default: inspect-artifact; e.g. inspect-with-objdump, inspect-binary-layout). "
             "MCP-only tools such as inspect_malbolge are rejected",
    )
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

    # tier1
    p_tier1 = subparsers.add_parser(
        "run-tier1",
        help="Run one reversible profile change under a live TTY authorization; never unattended",
    )
    p_tier1.add_argument("--plan", help="Path to a strict JSON Tier-1 plan file")
    p_tier1.add_argument(
        "--set",
        dest="set_profile",
        metavar="PROFILE",
        help="Build the plan for one change to PROFILE and read the way back from this host",
    )
    p_tier1.set_defaults(func=cmd_run_tier1)

    p_faults = subparsers.add_parser(
        "list-faults",
        help="List unresolved emergency fault records read-only; exit 1 when any remains",
    )
    p_faults.set_defaults(func=cmd_list_faults)

    p_resolve = subparsers.add_parser(
        "resolve-fault",
        help="Verify the host against a fault record and close it; this never changes the host",
    )
    p_resolve.add_argument(
        "fault_id",
        help="Fault identifier from the emergency fault state; arbitrary paths are rejected",
    )
    p_resolve.set_defaults(func=cmd_resolve_fault)

    # setup
    p_setup = subparsers.add_parser("setup", help="Verify stack paths and dependencies")
    p_setup.set_defaults(func=cmd_setup)

    parsed = parser.parse_args()
    parsed.func(parsed)


if __name__ == "__main__":
    main()
