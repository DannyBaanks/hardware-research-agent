import hashlib
import json
import math
import os
import re
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .cpu_power import MONITORED_STATE_FIELDS
from .experiment import SyntheticExperimentPlan
from .planning import ProposalValidationError, SyntheticExperimentProposal
from .rollback import (
    ROLLBACK_FAILED_STATE_DIFF,
    ROLLBACK_RESTORED_VERIFIED,
    ROLLBACK_STATUSES,
    ROLLBACK_VERIFIED_UNCHANGED,
    RollbackController,
)


_CAMPAIGN_ID = re.compile(r"^[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAMPLE_FILE = re.compile(
    r"^sample-(0[1-5])\.(started|receipt|interrupted|error)\.json$"
)
_MAX_FILE_BYTES = 1024 * 1024
_MAX_DIRECTORY_ENTRIES = 64
_BASE_FILES = {"campaign.json", "plan.json", "proposal.json", "checkpoint.json"}
# A campaign that executed also has to evidence that the host is where it said it
# would be; these three are as load-bearing as the receipts.
_ROLLBACK_FILES = {"prestate.json", "poststate.json", "rollback.json"}
_ROLLBACK_FIELDS = set(MONITORED_STATE_FIELDS)
_PRESTATE_FIELDS = {"campaign_id", "plan_sha256", "recorded_at", "state"}
_POSTSTATE_FIELDS = _PRESTATE_FIELDS | {"observation_error"}
_ROLLBACK_RECORD_FIELDS = {
    "campaign_id",
    "plan_sha256",
    "recorded_at",
    "status",
    "reason",
    "differences",
    "restoration_executed",
    "restoration_command",
    "restoration_receipt",
    "observation_error",
    "close_state",
    "verified_state",
    "prestate_sha256",
    "poststate_sha256",
}


def _is_campaign_entry(name: str) -> bool:
    return (
        name == "SHA256SUMS.txt"
        or name in _BASE_FILES
        or name in _ROLLBACK_FILES
        or _SAMPLE_FILE.fullmatch(name) is not None
        or name.startswith(".tmp-")
    )


@dataclass(frozen=True)
class CampaignInspection:
    campaign_id: str
    inspection_status: str
    verdict: str
    reason: str
    manifest_entries: int
    samples_verified: int
    issues: List[str]

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


class _InspectionError(ValueError):
    pass


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _InspectionError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_json(data: bytes, name: str) -> Dict[str, object]:
    try:
        value = json.loads(
            data,
            object_pairs_hook=_unique_json_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                _InspectionError(f"Invalid JSON constant in {name}: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise _InspectionError(f"{name} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise _InspectionError(f"{name} must contain a JSON object")
    return value


def _open_directory_path(path: Path) -> int:
    if not path.is_absolute() or any(part in (".", "..") for part in path.parts[1:]):
        raise _InspectionError("Evidence base must be an absolute normalized path")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path.anchor, flags)
    try:
        for component in path.parts[1:]:
            next_fd = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except Exception:
        os.close(fd)
        raise


def _check_private_directory(fd: int, name: str):
    metadata = os.fstat(fd)
    if not stat.S_ISDIR(metadata.st_mode):
        raise _InspectionError(f"{name} is not a directory")
    if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
        raise _InspectionError(f"{name} is not private and owned by the current user")


def _finite_number(value) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _read_regular_file(directory_fd: int, name: str) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(name, flags, dir_fd=directory_fd)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise _InspectionError(f"{name} is not a regular file")
        if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
            raise _InspectionError(f"{name} is not private and owned by the current user")
        if metadata.st_size > _MAX_FILE_BYTES:
            raise _InspectionError(f"{name} exceeds the inspection size limit")
        chunks = []
        remaining = _MAX_FILE_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > _MAX_FILE_BYTES:
            raise _InspectionError(f"{name} exceeds the inspection size limit")
        return data
    finally:
        os.close(fd)


def _parse_manifest(data: bytes) -> Dict[str, str]:
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise _InspectionError("SHA256SUMS.txt is not ASCII") from exc
    entries = {}
    for line_number, line in enumerate(text.splitlines(), 1):
        match = re.fullmatch(
            r"([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._-]{0,127})", line
        )
        if match is None:
            raise _InspectionError(f"Malformed manifest line {line_number}")
        digest, name = match.groups()
        if name == "SHA256SUMS.txt":
            raise _InspectionError("Manifest must not include its own digest")
        if name in entries:
            raise _InspectionError(f"Duplicate manifest entry: {name}")
        if not _is_campaign_entry(name):
            raise _InspectionError(f"Unexpected manifest entry: {name}")
        entries[name] = digest
    if not entries:
        raise _InspectionError("Manifest is empty")
    return entries


def _validate_receipt(
    receipt: Dict[str, object],
    started: Dict[str, object],
    sample: int,
    campaign_id: str,
    plan_digest: str,
    expected_result_digest: str,
    workload_id: str,
    runner_digest: str,
    timeout_per_sample: int,
) -> Optional[str]:
    required = {
        "workload_id",
        "status",
        "output_sha256",
        "elapsed_seconds",
        "sandbox",
        "hardware_telemetry_reads",
        "device_control_operations",
        "persistent_host_state_changed",
        "sandboxed_processes_started",
        "reason",
        "runner_sha256",
        "process_exit_code",
        "validation",
        "campaign_id",
        "plan_sha256",
        "sample",
        "started_at",
        "measured_elapsed_seconds",
    }
    if not required.issubset(receipt):
        return "receipt is missing required fields"
    elapsed = receipt["elapsed_seconds"]
    measured_elapsed = receipt["measured_elapsed_seconds"]
    if (
        type(receipt["sample"]) is not int
        or receipt["sample"] != sample
        or receipt["campaign_id"] != campaign_id
        or receipt["plan_sha256"] != plan_digest
        or receipt["started_at"] != started.get("started_at")
        or receipt["workload_id"] != workload_id
        or receipt["status"] != "COMPLETED"
        or receipt["validation"] != "PASS"
        or receipt["output_sha256"] != expected_result_digest
        or receipt["runner_sha256"] != runner_digest
        or receipt["process_exit_code"] != 0
        or receipt["sandbox"] != "bubblewrap-rootless"
        or receipt["hardware_telemetry_reads"] != 0
        or receipt["device_control_operations"] != 0
        or receipt["persistent_host_state_changed"] is not False
        or receipt["sandboxed_processes_started"] != 1
        or not _finite_number(elapsed)
        or not 0 < elapsed <= timeout_per_sample
        or not _finite_number(measured_elapsed)
        or not 0 < measured_elapsed <= timeout_per_sample
        or type(receipt["process_exit_code"]) is not int
        or type(receipt["hardware_telemetry_reads"]) is not int
        or type(receipt["device_control_operations"]) is not int
        or type(receipt["sandboxed_processes_started"]) is not int
    ):
        return "receipt does not satisfy the fixed sample contract"
    return None


def _observed_state_issue(name: str, value) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != _ROLLBACK_FIELDS:
        return f"{name} must list exactly the monitored CPU power fields"
    for field, reading in value.items():
        if reading is None or type(reading) is bool or (type(reading) is str and reading):
            continue
        return f"{name} field {field} is not a readable scalar or absent"
    return None


def _state_differences(prestate_state, poststate_state):
    if not isinstance(prestate_state, dict) or not isinstance(poststate_state, dict):
        return []
    return [
        {
            "field": field,
            "prestate": prestate_state.get(field),
            "poststate": poststate_state.get(field),
        }
        for field in sorted(prestate_state)
        if prestate_state.get(field) != poststate_state.get(field)
    ]


def _is_supported_restoration(command) -> bool:
    if not isinstance(command, str):
        return False
    try:
        argv = RollbackController._parse_command(command)
    except ValueError:
        return False
    return (
        len(argv) == 3
        and argv[0] == "powerprofilesctl"
        and argv[1] == "set"
        and argv[2] in RollbackController.PROFILES
    )


def _derived_rollback_status(record, prestate_state, poststate_state) -> str:
    """Recomputes the status from the sealed states, never trusting the label."""
    if poststate_state == prestate_state:
        return ROLLBACK_VERIFIED_UNCHANGED
    receipt = record.get("restoration_receipt")
    if (
        record.get("restoration_executed") is True
        and isinstance(receipt, dict)
        and receipt.get("success") is True
        and record.get("verified_state") == prestate_state
    ):
        return ROLLBACK_RESTORED_VERIFIED
    return ROLLBACK_FAILED_STATE_DIFF


def _validate_rollback_evidence(
    directory_fd: int, present: set, campaign: Dict[str, object], campaign_id: str, plan_digest: str
) -> List[str]:
    """Checks the prestate/poststate/rollback triple against itself.

    The three files are evidence only because they can contradict one another: a
    status label that does not follow from the two states sealed into the rollback
    digest is not a claim the campaign is allowed to make about the host.
    """
    missing = sorted(_ROLLBACK_FILES - present)
    if missing:
        return [f"Execution-coupled rollback evidence is missing: {', '.join(missing)}"]
    raw = {
        name: _read_regular_file(directory_fd, name)
        for name in ("prestate.json", "poststate.json", "rollback.json")
    }
    prestate = _parse_json(raw["prestate.json"], "prestate.json")
    poststate = _parse_json(raw["poststate.json"], "poststate.json")
    record = _parse_json(raw["rollback.json"], "rollback.json")
    issues: List[str] = []
    if set(prestate) != _PRESTATE_FIELDS:
        issues.append("Prestate record has missing or unknown fields")
    if set(poststate) != _POSTSTATE_FIELDS:
        issues.append("Poststate record has missing or unknown fields")
    if set(record) != _ROLLBACK_RECORD_FIELDS:
        issues.append("Rollback record has missing or unknown fields")
    if issues:
        return issues

    for name, artifact in (
        ("prestate.json", prestate),
        ("poststate.json", poststate),
        ("rollback.json", record),
    ):
        if artifact.get("campaign_id") != campaign_id:
            issues.append(f"{name} does not belong to this campaign")
        if artifact.get("plan_sha256") != plan_digest:
            issues.append(f"{name} does not bind the recorded plan")
        if not isinstance(artifact.get("recorded_at"), str) or not artifact["recorded_at"]:
            issues.append(f"{name} has no recording timestamp")

    for name, value in (
        ("prestate", prestate.get("state")),
        ("poststate", poststate.get("state")),
        ("rollback close_state", record.get("close_state")),
        ("rollback verified_state", record.get("verified_state")),
    ):
        problem = _observed_state_issue(name, value)
        if problem:
            issues.append(problem)
    if record.get("status") not in ROLLBACK_STATUSES:
        issues.append("Rollback status is outside the derived vocabulary")
    if not isinstance(record.get("reason"), str) or not record["reason"]:
        issues.append("Rollback record has no reason")
    if record.get("observation_error") is not None and not isinstance(
        record.get("observation_error"), str
    ):
        issues.append("Rollback observation error is neither absent nor a string")
    if poststate.get("state") is None and poststate.get("observation_error") is None:
        issues.append("Poststate records neither an observation nor an observation error")
    executed = record.get("restoration_executed")
    receipt = record.get("restoration_receipt")
    if type(executed) is not bool:
        issues.append("Rollback restoration flag is not a boolean")
    elif executed is (receipt is None):
        issues.append("Rollback execution flag disagrees with the restoration receipt")
    if receipt is not None and not isinstance(receipt, dict):
        issues.append("Rollback restoration receipt is not an object")
    if record.get("close_state") != poststate.get("state"):
        issues.append("Rollback close state is not the recorded poststate")
    if record.get("differences") != _state_differences(prestate["state"], poststate["state"]):
        issues.append("Rollback differences are not the recorded state difference")
    if record.get("prestate_sha256") != hashlib.sha256(raw["prestate.json"]).hexdigest():
        issues.append("Rollback record does not bind the recorded prestate bytes")
    if record.get("poststate_sha256") != hashlib.sha256(raw["poststate.json"]).hexdigest():
        issues.append("Rollback record does not bind the recorded poststate bytes")
    if record["status"] == ROLLBACK_VERIFIED_UNCHANGED and record["restoration_executed"] is not False:
        issues.append("Unchanged rollback reports an executed restoration")
    if executed is True:
        if not _is_supported_restoration(record.get("restoration_command")):
            issues.append("Recorded restoration command is outside the supported profile surface")
        if isinstance(receipt, dict) and receipt.get("target_command") != record.get(
            "restoration_command"
        ):
            issues.append("Restoration receipt does not name the recorded restoration command")
    derived = _derived_rollback_status(record, prestate["state"], poststate["state"])
    if record["status"] != derived:
        issues.append(f"Rollback status says {record['status']} but the states say {derived}")
    declared = campaign.get("rollback_status")
    if declared != record["status"]:
        issues.append(f"Campaign record declares rollback status {declared}")
    if campaign.get("status") == "COMPLETED" and derived == ROLLBACK_FAILED_STATE_DIFF:
        issues.append("Completed campaign carries unrestored host state")
    return issues


def inspect_synthetic_campaign(
    campaign_id: str, evidence_base: Optional[Path] = None
) -> CampaignInspection:
    if not isinstance(campaign_id, str) or not _CAMPAIGN_ID.fullmatch(campaign_id):
        return CampaignInspection(
            str(campaign_id),
            "INCOMPLETE",
            "NOT_DEMONSTRATED",
            "Invalid campaign identifier",
            0,
            0,
            ["invalid campaign identifier"],
        )

    if evidence_base is None:
        configured = os.environ.get("XDG_STATE_HOME")
        if configured:
            state_home = Path(configured)
            if not state_home.is_absolute():
                return CampaignInspection(
                    campaign_id, "INCOMPLETE", "NOT_DEMONSTRATED",
                    "XDG_STATE_HOME must be absolute", 0, 0,
                    ["invalid XDG_STATE_HOME"],
                )
        else:
            state_home = Path.home() / ".local" / "state"
        evidence_base = state_home / "ihra" / "synthetic-experiments"
    else:
        evidence_base = Path(evidence_base)

    base_fd = campaign_fd = None
    manifest_entries = {}
    issues: List[str] = []
    try:
        base_fd = _open_directory_path(evidence_base)
        _check_private_directory(base_fd, "Evidence base")
        campaign_fd = os.open(
            campaign_id,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=base_fd,
        )
        _check_private_directory(campaign_fd, "Campaign directory")
        names = os.listdir(campaign_fd)
        if len(names) > _MAX_DIRECTORY_ENTRIES:
            raise _InspectionError("Campaign directory exceeds the entry limit")
        for name in names:
            if not _is_campaign_entry(name):
                raise _InspectionError(f"Unexpected campaign entry: {name}")

        manifest_data = _read_regular_file(campaign_fd, "SHA256SUMS.txt")
        manifest_entries = _parse_manifest(manifest_data)
        for name, expected_digest in manifest_entries.items():
            try:
                data = _read_regular_file(campaign_fd, name)
            except OSError as exc:
                issues.append(f"Manifest file missing or unreadable: {name}: {exc}")
                continue
            actual_digest = hashlib.sha256(data).hexdigest()
            if actual_digest != expected_digest:
                issues.append(f"SHA-256 mismatch: {name}")
        listed = set(manifest_entries)
        present = set(names) - {"SHA256SUMS.txt"}
        missing_entries = present - listed
        missing_files = listed - present
        if missing_entries:
            issues.append("Unmanifested files: " + ", ".join(sorted(missing_entries)))
        if missing_files:
            issues.append("Missing manifest files: " + ", ".join(sorted(missing_files)))
        if any(name.startswith(".tmp-") for name in present):
            issues.append("Atomic-write temporary file remains")

        campaign = _parse_json(_read_regular_file(campaign_fd, "campaign.json"), "campaign.json")
        if campaign.get("campaign_id") != campaign_id:
            issues.append("Campaign record identifier does not match directory")
        status = campaign.get("status")
        if status == "RUNNING":
            issues.append("Campaign record is still RUNNING")
            return CampaignInspection(
                campaign_id, "ACTIVE_OR_UNKNOWN", "NOT_DEMONSTRATED",
                "The evidence does not establish whether the campaign process is still active",
                len(manifest_entries), 0, issues,
            )
        if status != "COMPLETED" or campaign.get("verdict") != "DEMONSTRATED":
            issues.append("Campaign does not have a successful terminal record")
            return CampaignInspection(
                campaign_id, "INCOMPLETE", "NOT_DEMONSTRATED",
                "Campaign is terminal but not demonstrated complete",
                len(manifest_entries), 0, issues,
            )

        plan_bytes = _read_regular_file(campaign_fd, "plan.json")
        plan_data = _parse_json(plan_bytes, "plan.json")
        expected_plan = SyntheticExperimentPlan.fixed_repeatability_plan().to_dict()
        if set(plan_data) != set(expected_plan):
            raise _InspectionError("Plan contains missing or unknown fields")
        if not isinstance(plan_data.get("resource_limits"), dict):
            raise _InspectionError("Plan resource_limits must be an object")
        if (
            set(plan_data["resource_limits"]) != set(expected_plan["resource_limits"])
            or any(
                type(plan_data.get(field)) is not int
                for field in (
                    "schema_version",
                    "samples",
                    "timeout_per_sample",
                    "max_total_duration",
                )
            )
            or any(
                type(value) is not int
                for value in plan_data["resource_limits"].values()
            )
            or any(
                plan_data.get(field) != expected_value
                for field, expected_value in expected_plan.items()
                if field != "runner_digest"
            )
            or not isinstance(plan_data.get("runner_digest"), str)
            or not _SHA256.fullmatch(plan_data["runner_digest"])
        ):
            raise _InspectionError("Plan is outside the fixed repeatability contract")
        canonical_plan = json.dumps(
            plan_data,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
        plan_digest = hashlib.sha256(canonical_plan).hexdigest()
        if plan_digest != campaign.get("plan_sha256"):
            issues.append("Plan digest does not match campaign record")
        if plan_data["runner_digest"] != expected_plan["runner_digest"]:
            issues.append("Plan uses a runner digest outside the current pin")
        campaign_runner = campaign.get("runner")
        if (
            not isinstance(campaign_runner, dict)
            or campaign_runner.get("runner_sha256") != plan_data["runner_digest"]
            or campaign_runner.get("workload_id") != plan_data["workload_id"]
            or campaign_runner.get("workload_version") != plan_data["workload_version"]
        ):
            issues.append("Campaign runner identity does not match the recorded plan")

        proposal_digest = None
        if "proposal.json" in present:
            proposal_bytes = _read_regular_file(campaign_fd, "proposal.json")
            proposal = SyntheticExperimentProposal.from_bytes(proposal_bytes)
            if proposal.canonical_bytes() != proposal_bytes:
                issues.append("Proposal is not canonical")
            proposal_digest = hashlib.sha256(proposal_bytes).hexdigest()
            if proposal.plan.canonical_bytes() != canonical_plan:
                issues.append("Proposal does not contain the recorded plan")

        if "checkpoint.json" in present:
            checkpoint = _parse_json(
                _read_regular_file(campaign_fd, "checkpoint.json"), "checkpoint.json"
            )
            if (
                checkpoint.get("schema_version") != 1
                or checkpoint.get("campaign_id") != campaign_id
                or checkpoint.get("plan_sha256") != plan_digest
                or checkpoint.get("state") != "TERMINAL"
                or checkpoint.get("current_sample") is not None
                or type(checkpoint.get("samples_completed")) is not int
                or checkpoint.get("samples_completed") != plan_data["samples"]
            ):
                issues.append("Checkpoint does not confirm a consistent terminal campaign")
        else:
            issues.append("Historical campaign has no checkpoint")

        authorization = campaign.get("authorization")
        if not isinstance(authorization, dict):
            issues.append("Campaign authorization record is missing")
        elif (
            authorization.get("method") != "local_tty_confirmation"
            or authorization.get("plan_sha256") != plan_digest
            or authorization.get("proposal_sha256") != proposal_digest
        ):
            issues.append("Campaign authorization does not bind the recorded proposal and plan")

        issues.extend(
            _validate_rollback_evidence(
                campaign_fd, present, campaign, campaign_id, plan_digest
            )
        )

        samples_verified = 0
        for sample in range(1, plan_data["samples"] + 1):
            receipt_name = f"sample-{sample:02d}.receipt.json"
            start_name = f"sample-{sample:02d}.started.json"
            if receipt_name not in present or start_name not in present:
                issues.append(f"Sample {sample} is missing its start marker or receipt")
                continue
            start = _parse_json(_read_regular_file(campaign_fd, start_name), start_name)
            if (
                start.get("campaign_id") != campaign_id
                or start.get("plan_sha256") != plan_digest
                or type(start.get("sample")) is not int
                or start.get("sample") != sample
                or start.get("status") != "RUNNING"
            ):
                issues.append(f"Sample {sample} start marker is inconsistent")
                continue
            receipt = _parse_json(_read_regular_file(campaign_fd, receipt_name), receipt_name)
            problem = _validate_receipt(
                receipt,
                start,
                sample,
                campaign_id,
                plan_digest,
                plan_data["expected_result_digest"],
                plan_data["workload_id"],
                plan_data["runner_digest"],
                plan_data["timeout_per_sample"],
            )
            if problem:
                issues.append(f"Sample {sample}: {problem}")
            else:
                samples_verified += 1

        required_present = {"campaign.json", "plan.json", "proposal.json", "checkpoint.json"}
        required_present.update(
            f"sample-{sample:02d}.{kind}.json"
            for sample in range(1, plan_data["samples"] + 1)
            for kind in ("started", "receipt")
        )
        if not required_present.issubset(present):
            issues.append("Required campaign artifacts are missing")
        if set(manifest_entries) != present:
            issues.append("Manifest does not cover exactly the campaign files")
        if (
            type(campaign.get("samples_completed")) is not int
            or campaign.get("samples_completed") != plan_data["samples"]
            or type(campaign.get("samples_expected")) is not int
            or campaign.get("samples_expected") != plan_data["samples"]
        ):
            issues.append("Campaign sample counters do not match the fixed plan")
        if issues:
            return CampaignInspection(
                campaign_id, "INCOMPLETE", "NOT_DEMONSTRATED",
                "Campaign evidence is incomplete, inconsistent, or unverifiable",
                len(manifest_entries), samples_verified, issues,
            )
        return CampaignInspection(
            campaign_id, "COMPLETED_VALID", "DEMONSTRATED",
            "All five fixed-workload receipts and linked evidence verified",
            len(manifest_entries), samples_verified, [],
        )
    except (
        OSError,
        _InspectionError,
        ProposalValidationError,
        ValueError,
        TypeError,
        RecursionError,
    ) as exc:
        issues.append(str(exc))
        return CampaignInspection(
            campaign_id, "INCOMPLETE", "NOT_DEMONSTRATED",
            "Campaign evidence is incomplete, inconsistent, or unverifiable",
            len(manifest_entries), 0, issues,
        )
    finally:
        if campaign_fd is not None:
            os.close(campaign_fd)
        if base_fd is not None:
            os.close(base_fd)


class FaultStateError(RuntimeError):
    """The emergency fault state could not be read, so nothing may execute."""


# Emergency fault state (docs/SAFETY_CONTRACTS.md 5). A Tier-1 run that could not
# put the host back writes one record here and every later campaign is denied until
# a person resolves it. It lives beside, never inside, the campaign evidence so the
# inspection allowlists and sealed bundles stay immutable.
FAULT_UNRESOLVED = "UNRESOLVED"
FAULT_RESOLVED = "RESOLVED"
_FAULT_STATUSES = (FAULT_UNRESOLVED, FAULT_RESOLVED)
_FAULT_ID = re.compile(r"^[0-9a-f]{32}$")
_FAULT_FILE = re.compile(r"^([0-9a-f]{32})\.json$")
_FAULT_FIELDS = {
    "schema_version",
    "fault_id",
    "recorded_at",
    "status",
    "reason",
    "rollback_status",
    "experiment_id",
    "campaign_id",
    "plan_sha256",
    "expected_state",
    "observed_state",
    "restoration_command",
    "expected_restored_state",
    "remediation",
    "evidence_dir",
    "resolved_at",
    "resolved_state",
    "resolved_profile",
}
_MAX_FAULT_RECORDS = 64


def tier1_fault_base() -> Path:
    configured = os.environ.get("XDG_STATE_HOME")
    if configured:
        state_home = Path(configured)
        if not state_home.is_absolute():
            raise FaultStateError("XDG_STATE_HOME must be absolute")
    else:
        state_home = Path.home() / ".local" / "state"
    return state_home / "ihra" / "tier1-faults"


def _open_fault_base(base: Optional[Path]) -> int:
    root = tier1_fault_base() if base is None else Path(base)
    try:
        return _open_directory_path(root)
    except FileNotFoundError:
        return None
    except (OSError, _InspectionError) as exc:
        raise FaultStateError(f"Emergency fault state is unreadable: {exc}") from exc


def _parse_fault_record(data: bytes, name: str, expected_id: str) -> Dict[str, object]:
    record = _parse_json(data, name)
    if set(record) != _FAULT_FIELDS:
        raise FaultStateError(f"{name} has missing or unknown fields")
    if record["schema_version"] != 1 or record["fault_id"] != expected_id:
        raise FaultStateError(f"{name} identifier does not match its filename")
    if record["status"] not in _FAULT_STATUSES:
        raise FaultStateError(f"{name} status is outside the fault vocabulary")
    if not isinstance(record["recorded_at"], str) or not record["recorded_at"]:
        raise FaultStateError(f"{name} has no recording timestamp")
    if record["campaign_id"] is not None and not _FAULT_ID.fullmatch(str(record["campaign_id"])):
        raise FaultStateError(f"{name} references an unknown campaign")
    for field in ("expected_state", "observed_state", "resolved_state"):
        problem = _observed_state_issue(field, record[field])
        if problem:
            raise FaultStateError(f"{name}: {problem}")
    if not isinstance(record["remediation"], list) or not all(
        isinstance(line, str) and line for line in record["remediation"]
    ):
        raise FaultStateError(f"{name} remediation is not a list of commands")
    if record["status"] == FAULT_RESOLVED and not isinstance(record["resolved_at"], str):
        raise FaultStateError(f"{name} is resolved without a resolution timestamp")
    if record["status"] == FAULT_UNRESOLVED and record["resolved_at"] is not None:
        raise FaultStateError(f"{name} is unresolved but carries a resolution timestamp")
    return record


def find_unresolved_faults(base: Optional[Path] = None) -> List[Dict[str, object]]:
    """Lists the faults that still deny execution, reading only."""
    directory_fd = _open_fault_base(base)
    if directory_fd is None:
        return []
    try:
        entries = os.listdir(directory_fd)
        names = []
        for name in sorted(entries):
            if name.startswith(".tmp-"):
                continue
            if _FAULT_FILE.fullmatch(name) is None:
                raise FaultStateError(f"Unexpected entry in emergency fault state: {name}")
            names.append(name)
        if len(names) > _MAX_FAULT_RECORDS:
            raise FaultStateError("Emergency fault state exceeds the record limit")
        faults = []
        for name in names:
            try:
                data = _read_regular_file(directory_fd, name)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise FaultStateError(f"Emergency fault record is unreadable: {name}: {exc}") from exc
            record = _parse_fault_record(data, name, name[:32])
            if record["status"] == FAULT_UNRESOLVED:
                faults.append(record)
        return faults
    except OSError as exc:
        raise FaultStateError(f"Emergency fault state could not be listed: {exc}") from exc
    finally:
        os.close(directory_fd)


def write_fault_record(record: Dict[str, object], base: Optional[Path] = None) -> Path:
    """Seals one emergency fault; the write path is the only mutable one here."""
    if set(record) != _FAULT_FIELDS:
        raise FaultStateError("Fault record has missing or unknown fields")
    if not _FAULT_ID.fullmatch(str(record["fault_id"])):
        raise FaultStateError("Fault identifier must be a 32-character hexadecimal campaign id")
    root = tier1_fault_base() if base is None else Path(base)
    try:
        os.makedirs(root, mode=0o700, exist_ok=True)
    except OSError as exc:
        raise FaultStateError(f"Emergency fault state cannot be created: {exc}") from exc
    target = root / f"{record['fault_id']}.json"
    temp = root / f".tmp-{record['fault_id']}-{os.getpid()}"
    data = json.dumps(
        record, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii") + b"\n"
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb", closefd=True) as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, target)
        directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise FaultStateError(f"Emergency fault record could not be written: {exc}") from exc
    return target


def resolve_fault(
    fault_id: str,
    observed_state,
    observed_profile,
    resolved_at: str,
    base: Optional[Path] = None,
) -> Dict[str, object]:
    """Closes an emergency fault, but only against the state the campaign owed.

    The caller supplies a fresh observation taken after the remediation commands;
    this function never runs anything itself, so a resolution is a claim the host
    has been verified to be back where the fault found it.
    """
    if not _FAULT_ID.fullmatch(str(fault_id)):
        raise FaultStateError("Invalid fault identifier")
    directory_fd = _open_fault_base(base)
    if directory_fd is None:
        raise FaultStateError("No emergency fault state exists")
    name = f"{fault_id}.json"
    try:
        try:
            data = _read_regular_file(directory_fd, name)
        except FileNotFoundError as exc:
            raise FaultStateError(f"No emergency fault record is stored for {fault_id}") from exc
        except OSError as exc:
            raise FaultStateError(f"Emergency fault record is unreadable: {exc}") from exc
        record = _parse_fault_record(data, name, fault_id)
        if record["status"] == FAULT_RESOLVED:
            return record
        if observed_profile is None or observed_profile != record["expected_restored_state"]:
            raise FaultStateError(
                "Fault stays unresolved: the power profile is "
                f"{observed_profile!r}, not the restored state "
                f"{record['expected_restored_state']!r}"
            )
        if record["expected_state"] is not None and observed_state != record["expected_state"]:
            differences = _state_differences(record["expected_state"], observed_state)
            raise FaultStateError(
                "Fault stays unresolved: the monitored state still differs ("
                + ", ".join(str(item["field"]) for item in differences)
                + ")"
            )
        record["status"] = FAULT_RESOLVED
        record["resolved_at"] = resolved_at
        record["resolved_state"] = observed_state
        record["resolved_profile"] = observed_profile
    finally:
        os.close(directory_fd)
    write_fault_record(record, base=base)
    return record
