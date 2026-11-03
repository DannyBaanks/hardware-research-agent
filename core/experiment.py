import hashlib
import json
import os
import platform
import re
import stat
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

from .authorization import AuthorizationDenied, PlanAuthorization
from .executor import (
    SandboxUnavailableError,
    SyntheticWorkloadExecutor,
    WorkloadReceipt,
)


class PlanValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SyntheticExperimentPlan:
    schema_version: int
    plan_id: str
    workload_id: str
    workload_version: str
    runner_digest: str
    expected_result_digest: str
    samples: int
    timeout_per_sample: int
    max_total_duration: int
    resource_limits: Tuple[Tuple[str, int], ...]
    failure_policy: str
    evidence_policy: str

    @classmethod
    def fixed_repeatability_plan(cls):
        return cls(
            schema_version=1,
            plan_id="ihra-repeatability-sha256-chain-v1",
            workload_id=SyntheticWorkloadExecutor.WORKLOAD_ID,
            workload_version="1",
            runner_digest=SyntheticWorkloadExecutor.RUNNER_SHA256,
            expected_result_digest=SyntheticWorkloadExecutor.EXPECTED_OUTPUT_SHA256,
            samples=5,
            timeout_per_sample=SyntheticWorkloadExecutor.WALL_TIMEOUT_SECONDS,
            max_total_duration=60,
            resource_limits=(
                ("cpu_seconds", SyntheticWorkloadExecutor.CPU_LIMIT_SECONDS),
                ("address_space_bytes", SyntheticWorkloadExecutor.ADDRESS_SPACE_LIMIT_BYTES),
                ("file_size_bytes", 0),
                ("open_files", SyntheticWorkloadExecutor.NOFILE_LIMIT),
                ("processes", 4),
                ("core_dump_bytes", 0),
            ),
            failure_policy="abort_campaign_no_retries",
            evidence_policy="private_xdg_atomic_sha256_manifest",
        )

    def to_dict(self) -> Dict[str, object]:
        value = asdict(self)
        value["resource_limits"] = dict(self.resource_limits)
        return value

    def canonical_bytes(self) -> bytes:
        self.validate()
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def validate(self):
        expected = self.fixed_repeatability_plan()
        if type(self.schema_version) is not int or self.schema_version != expected.schema_version:
            raise PlanValidationError("Unsupported plan schema_version")
        for field in (
            "plan_id",
            "workload_id",
            "workload_version",
            "runner_digest",
            "expected_result_digest",
            "failure_policy",
            "evidence_policy",
        ):
            if getattr(self, field) != getattr(expected, field):
                raise PlanValidationError(f"Plan field is not authorized: {field}")
        for field in ("samples", "timeout_per_sample", "max_total_duration"):
            value = getattr(self, field)
            if type(value) is not int or value != getattr(expected, field):
                raise PlanValidationError(f"Plan field is not authorized: {field}")
        if (
            len(self.resource_limits) != len(expected.resource_limits)
            or dict(self.resource_limits) != dict(expected.resource_limits)
        ):
            raise PlanValidationError("Plan resource_limits are not authorized")

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise PlanValidationError("Plan must be a JSON object")
        expected = cls.fixed_repeatability_plan().to_dict()
        if set(value) != set(expected):
            raise PlanValidationError("Plan contains missing or unknown fields")
        if not isinstance(value["resource_limits"], dict):
            raise PlanValidationError("resource_limits must be an object")
        if set(value["resource_limits"]) != set(expected["resource_limits"]):
            raise PlanValidationError("resource_limits contains missing or unknown fields")
        plan = cls(
            schema_version=value["schema_version"],
            plan_id=value["plan_id"],
            workload_id=value["workload_id"],
            workload_version=value["workload_version"],
            runner_digest=value["runner_digest"],
            expected_result_digest=value["expected_result_digest"],
            samples=value["samples"],
            timeout_per_sample=value["timeout_per_sample"],
            max_total_duration=value["max_total_duration"],
            resource_limits=tuple(value["resource_limits"].items()),
            failure_policy=value["failure_policy"],
            evidence_policy=value["evidence_policy"],
        )
        plan.validate()
        return plan


@dataclass(frozen=True)
class ExperimentCampaignResult:
    campaign_id: Optional[str]
    plan_sha256: Optional[str]
    status: str
    verdict: str
    samples_completed: int
    evidence_dir: Optional[str]
    reason: str

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


class _CampaignEvidence:
    NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

    def __init__(self, base: Path, campaign_id: str, repository_root: Path):
        if not self.NAME_PATTERN.fullmatch(campaign_id):
            raise ValueError("Invalid campaign identifier")
        self._assert_no_symlink_components(base)
        missing = []
        ancestor = base
        while not ancestor.exists():
            missing.append(ancestor)
            ancestor = ancestor.parent
        for directory in reversed(missing):
            directory.mkdir(mode=0o700)
        self._assert_no_symlink_components(base)
        self.base = base.resolve(strict=True)
        repo = repository_root.resolve(strict=True)
        if self.base == repo or repo in self.base.parents:
            raise ValueError("Evidence directory must be outside the Git repository")
        for directory in (self.base.parent, self.base):
            metadata = directory.stat()
            if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
                raise PermissionError(
                    "Evidence directories must be private and owned by the current user"
                )
        self.path = self.base / campaign_id
        self.path.mkdir(mode=0o700)
        self._assert_no_symlink_components(self.path)
        self.path = self.path.resolve(strict=True)
        self._dir_fd = os.open(
            self.path,
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
        )
        self._digests = {}

    @staticmethod
    def _assert_no_symlink_components(path: Path):
        absolute = path.absolute()
        current = Path(absolute.anchor)
        for component in absolute.parts[1:]:
            current = current / component
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(metadata.st_mode):
                raise ValueError(f"Symlink path component is not allowed: {current}")

    @classmethod
    def default_base(cls) -> Path:
        configured = os.environ.get("XDG_STATE_HOME")
        if configured:
            path = Path(configured)
            if not path.is_absolute():
                raise ValueError("XDG_STATE_HOME must be absolute")
            state_home = path
        else:
            state_home = Path.home() / ".local" / "state"
        return state_home / "ihra" / "synthetic-experiments"

    def write_bytes(self, name: str, data: bytes):
        if not self.NAME_PATTERN.fullmatch(name) or name in {".", ".."}:
            raise ValueError("Invalid evidence filename")
        self._write_atomic(name, data)
        if name == "SHA256SUMS.txt":
            return
        self._digests[name] = hashlib.sha256(data).hexdigest()
        self.write_manifest()

    def _write_atomic(self, name: str, data: bytes):
        temp_name = f".tmp-{uuid.uuid4().hex}"
        fd = os.open(
            temp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=self._dir_fd,
        )
        try:
            with os.fdopen(fd, "wb", closefd=True) as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(
                temp_name,
                name,
                src_dir_fd=self._dir_fd,
                dst_dir_fd=self._dir_fd,
            )
            os.fsync(self._dir_fd)
        except Exception:
            try:
                os.unlink(temp_name, dir_fd=self._dir_fd)
            except FileNotFoundError:
                pass
            raise

    def write_json(self, name: str, value):
        data = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii") + b"\n"
        self.write_bytes(name, data)

    def write_checkpoint(
        self,
        campaign_id: str,
        plan_digest: str,
        state: str,
        current_sample: Optional[int],
        samples_completed: int,
    ):
        self.write_json(
            "checkpoint.json",
            {
                "schema_version": 1,
                "campaign_id": campaign_id,
                "plan_sha256": plan_digest,
                "state": state,
                "current_sample": current_sample,
                "samples_completed": samples_completed,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    def write_manifest(self):
        lines = [
            f"{digest}  {name}"
            for name, digest in sorted(self._digests.items())
            if name != "SHA256SUMS.txt"
        ]
        self._write_atomic("SHA256SUMS.txt", ("\n".join(lines) + "\n").encode("ascii"))

    def close(self):
        if self._dir_fd is not None:
            os.close(self._dir_fd)
            self._dir_fd = None


class SyntheticExperimentRunner:
    """Runs the single TTY-authorized five-sample repeatability campaign."""

    def __init__(
        self,
        executor=None,
        evidence_base: Optional[Path] = None,
        repository_root: Optional[Path] = None,
    ):
        self.executor = executor or SyntheticWorkloadExecutor()
        self.evidence_base = evidence_base
        self.repository_root = repository_root or Path(__file__).resolve().parent.parent

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _verify_runner(self, plan: SyntheticExperimentPlan):
        runner = self.executor.runner_path
        if runner.is_symlink() or not runner.is_file():
            raise ValueError("RUNNER_INTEGRITY_FAILURE")
        digest = hashlib.sha256(runner.read_bytes()).hexdigest()
        if digest != plan.runner_digest:
            raise ValueError("RUNNER_INTEGRITY_FAILURE")
        return digest

    def _verify_sandbox_binaries(self):
        bubblewrap, python = self.executor._sandbox_binaries()
        return {
            "bubblewrap_path": bubblewrap,
            "bubblewrap_sha256": hashlib.sha256(Path(bubblewrap).read_bytes()).hexdigest(),
            "bubblewrap_version": self._dpkg_package_version("bubblewrap"),
            "python_path": python,
            "python_sha256": hashlib.sha256(Path(python).read_bytes()).hexdigest(),
        }

    @staticmethod
    def _dpkg_package_version(package_name: str) -> str:
        status_path = Path("/var/lib/dpkg/status")
        try:
            if status_path.stat().st_size > 8 * 1024 * 1024:
                return "NOT_DEMONSTRATED"
            for paragraph in status_path.read_text(encoding="utf-8").split("\n\n"):
                fields = {}
                for line in paragraph.splitlines():
                    if line and not line[0].isspace() and ": " in line:
                        key, value = line.split(": ", 1)
                        fields[key] = value
                if (
                    fields.get("Package") == package_name
                    and fields.get("Status") == "install ok installed"
                ):
                    return fields.get("Version", "NOT_DEMONSTRATED")
        except (OSError, UnicodeError):
            pass
        return "NOT_DEMONSTRATED"

    @staticmethod
    def _machine_model() -> str:
        try:
            model_path = Path("/sys/class/dmi/id/product_name")
            if model_path.is_file():
                return model_path.read_text(encoding="utf-8").strip()[:128]
        except (OSError, UnicodeError):
            pass
        return "NOT_DEMONSTRATED"

    @staticmethod
    def _plan_is_unchanged(plan, original_bytes: bytes, original_digest: str) -> bool:
        try:
            return plan.canonical_bytes() == original_bytes and plan.sha256 == original_digest
        except (PlanValidationError, TypeError, ValueError):
            return False

    @staticmethod
    def _validate_receipt(receipt, plan: SyntheticExperimentPlan, measured_elapsed: float):
        if not isinstance(receipt, WorkloadReceipt):
            raise ValueError("Receipt has an unexpected structure")
        if set(receipt.to_dict()) != {
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
        }:
            raise ValueError("Receipt is incomplete")
        elapsed = receipt.elapsed_seconds
        if (
            receipt.workload_id != plan.workload_id
            or receipt.status != "COMPLETED"
            or receipt.runner_sha256 != plan.runner_digest
            or receipt.process_exit_code != 0
            or receipt.output_sha256 != plan.expected_result_digest
            or receipt.sandbox != "bubblewrap-rootless"
            or receipt.hardware_telemetry_reads != 0
            or receipt.device_control_operations != 0
            or receipt.persistent_host_state_changed is not False
            or receipt.sandboxed_processes_started != 1
            or type(elapsed) not in (int, float)
            or not 0 < elapsed <= plan.timeout_per_sample
            or measured_elapsed > plan.timeout_per_sample
        ):
            raise ValueError("Receipt failed the fixed campaign contract")

    def run(
        self,
        plan: SyntheticExperimentPlan,
        authorization: Optional[PlanAuthorization] = None,
        proposal_bytes: Optional[bytes] = None,
    ) -> ExperimentCampaignResult:
        try:
            from .planning import SyntheticExperimentProposal

            if not isinstance(plan, SyntheticExperimentPlan):
                raise PlanValidationError("A fixed synthetic experiment plan is required")
            plan.validate()
            canonical_plan = plan.canonical_bytes()
            plan_digest = hashlib.sha256(canonical_plan).hexdigest()
            if type(authorization) is not PlanAuthorization:
                raise AuthorizationDenied("A one-time local TTY authorization is required")
            if not isinstance(proposal_bytes, bytes):
                raise AuthorizationDenied("Canonical proposal bytes are required")
            proposal = SyntheticExperimentProposal.from_bytes(proposal_bytes)
            if (
                proposal.plan.canonical_bytes() != canonical_plan
                or proposal.canonical_bytes() != proposal_bytes
            ):
                raise AuthorizationDenied(
                    "Proposal bytes are non-canonical or do not contain the authorized plan"
                )
            proposal_digest = authorization.consume(plan_digest, proposal.sha256)
            runner_digest = self._verify_runner(plan)
        except ValueError as exc:
            verdict = "DENY"
            reason = str(exc)
            if "RUNNER_INTEGRITY_FAILURE" in reason:
                reason = "RUNNER_INTEGRITY_FAILURE"
            return ExperimentCampaignResult(None, None, "DENIED", verdict, 0, None, reason)
        except AuthorizationDenied as exc:
            return ExperimentCampaignResult(None, None, "DENIED", "DENY", 0, None, str(exc))
        except SandboxUnavailableError as exc:
            return ExperimentCampaignResult(
                None, None, "DENIED", "DENY", 0, None, f"SANDBOX_UNAVAILABLE: {exc}"
            )
        except OSError as exc:
            verdict = "DENY"
            reason = str(exc)
            if "RUNNER_INTEGRITY_FAILURE" in reason:
                reason = "RUNNER_INTEGRITY_FAILURE"
            return ExperimentCampaignResult(None, None, "DENIED", verdict, 0, None, reason)

        campaign_id = uuid.uuid4().hex
        store = None
        completed = []
        started_monotonic = time.monotonic()
        started_at = self._utc_now()
        status = "ABORTED"
        verdict = "REFUTED"
        reason = "EXPERIMENT_ABORTED"
        try:
            evidence_base = self.evidence_base or _CampaignEvidence.default_base()
            store = _CampaignEvidence(evidence_base, campaign_id, self.repository_root)
            store.write_bytes("plan.json", canonical_plan)
            store.write_bytes("proposal.json", proposal_bytes)
            campaign_record = {
                "campaign_id": campaign_id,
                "plan_sha256": plan_digest,
                "authorization": {
                    "method": "local_tty_confirmation",
                    "plan_sha256": plan_digest,
                    "proposal_sha256": proposal_digest,
                },
                "status": "RUNNING",
                "verdict": "NOT_DEMONSTRATED",
                "started_at": started_at,
                "ended_at": None,
                "host": {
                    "kernel": platform.release(),
                    "architecture": platform.machine(),
                    "python": platform.python_version(),
                    "machine_model": self._machine_model(),
                },
                "runner": {
                    "workload_id": plan.workload_id,
                    "workload_version": plan.workload_version,
                    "runner_sha256": runner_digest,
                },
                "samples_completed": 0,
            }
            store.write_json("campaign.json", campaign_record)
            store.write_checkpoint(
                campaign_id, plan_digest, "RUNNING", None, len(completed)
            )
            try:
                sandbox_binaries = self._verify_sandbox_binaries()
            except SandboxUnavailableError as exc:
                status = "DENIED"
                verdict = "DENY"
                reason = f"SANDBOX_UNAVAILABLE: {exc}"
                campaign_record.update(
                    {
                        "status": status,
                        "verdict": verdict,
                        "reason": reason,
                        "ended_at": self._utc_now(),
                        "duration_seconds": time.monotonic() - started_monotonic,
                        "samples_completed": 0,
                        "samples_expected": plan.samples,
                        "performance_claim": False,
                    }
                )
                store.write_json("campaign.json", campaign_record)
                store.write_checkpoint(
                    campaign_id, plan_digest, "TERMINAL", None, len(completed)
                )
                return ExperimentCampaignResult(
                    campaign_id, plan_digest, status, verdict, 0, str(store.path), reason
                )
            campaign_record["runner"].update(sandbox_binaries)
            store.write_json("campaign.json", campaign_record)

            current_sample = None
            for sample_number in range(1, plan.samples + 1):
                if not self._plan_is_unchanged(plan, canonical_plan, plan_digest):
                    reason = "Plan changed after authorization"
                    break
                if time.monotonic() - started_monotonic >= plan.max_total_duration:
                    reason = "Campaign exceeded maximum total duration"
                    break
                try:
                    current_runner_digest = self._verify_runner(plan)
                    current_sandbox_binaries = self._verify_sandbox_binaries()
                except SandboxUnavailableError as exc:
                    status = "DENIED"
                    verdict = "DENY"
                    reason = f"SANDBOX_UNAVAILABLE: {exc}"
                    break
                except (OSError, ValueError) as exc:
                    reason = "RUNNER_INTEGRITY_FAILURE" if "RUNNER" in str(exc) else str(exc)
                    break
                if current_runner_digest != runner_digest:
                    reason = "RUNNER_INTEGRITY_FAILURE"
                    break
                if current_sandbox_binaries != sandbox_binaries:
                    reason = "SANDBOX_BINARY_INTEGRITY_FAILURE"
                    break
                sample_started_at = self._utc_now()
                store.write_json(
                    f"sample-{sample_number:02d}.started.json",
                    {
                        "campaign_id": campaign_id,
                        "plan_sha256": plan_digest,
                        "sample": sample_number,
                        "started_at": sample_started_at,
                        "status": "RUNNING",
                    },
                )
                current_sample = sample_number
                store.write_checkpoint(
                    campaign_id,
                    plan_digest,
                    "SAMPLE_RUNNING",
                    sample_number,
                    len(completed),
                )
                sample_started = time.monotonic()
                try:
                    receipt = self.executor.run(plan.workload_id)
                except KeyboardInterrupt:
                    reason = "Campaign interrupted during sample"
                    store.write_json(
                        f"sample-{sample_number:02d}.interrupted.json",
                        {
                            "campaign_id": campaign_id,
                            "plan_sha256": plan_digest,
                            "sample": sample_number,
                            "status": "INTERRUPTED",
                            "recorded_at": self._utc_now(),
                        },
                    )
                    break
                except Exception as exc:
                    reason = f"Executor failure: {type(exc).__name__}: {exc}"
                    store.write_json(
                        f"sample-{sample_number:02d}.error.json",
                        {
                            "campaign_id": campaign_id,
                            "plan_sha256": plan_digest,
                            "sample": sample_number,
                            "status": "FAILED",
                            "reason": reason,
                            "recorded_at": self._utc_now(),
                        },
                    )
                    break
                measured_elapsed = time.monotonic() - sample_started
                receipt_record = receipt.to_dict() if isinstance(receipt, WorkloadReceipt) else {
                    "received_type": type(receipt).__name__,
                }
                try:
                    self._validate_receipt(receipt, plan, measured_elapsed)
                    receipt_record["validation"] = "PASS"
                except (ValueError, TypeError) as exc:
                    receipt_record["validation"] = "FAIL"
                    receipt_record["validation_reason"] = str(exc)
                receipt_record.update(
                    {
                        "campaign_id": campaign_id,
                        "plan_sha256": plan_digest,
                        "sample": sample_number,
                        "started_at_monotonic_offset_seconds": sample_started - started_monotonic,
                        "started_at": sample_started_at,
                        "finished_at": self._utc_now(),
                        "measured_elapsed_seconds": measured_elapsed,
                        "recorded_at": self._utc_now(),
                    }
                )
                store.write_json(f"sample-{sample_number:02d}.receipt.json", receipt_record)
                if receipt_record["validation"] != "PASS":
                    if isinstance(receipt, WorkloadReceipt) and receipt.status == "DENIED":
                        verdict = "DENY"
                        reason = f"SANDBOX_UNAVAILABLE: {receipt.reason}"
                        status = "DENIED"
                    elif isinstance(receipt, WorkloadReceipt):
                        reason = (
                            f"EXPERIMENT_ABORTED: sample {sample_number} "
                            f"status={receipt.status} exit_code={receipt.process_exit_code}: "
                            f"{receipt.reason}"
                        )
                    else:
                        reason = f"Sample {sample_number} failed validation"
                    break
                if not self._plan_is_unchanged(plan, canonical_plan, plan_digest):
                    reason = "Plan changed during the campaign"
                    break
                try:
                    unchanged_runner = self._verify_runner(plan) == runner_digest
                    unchanged_binaries = (
                        self._verify_sandbox_binaries() == sandbox_binaries
                    )
                except SandboxUnavailableError as exc:
                    status = "DENIED"
                    verdict = "DENY"
                    reason = f"SANDBOX_UNAVAILABLE: {exc}"
                    break
                except (OSError, ValueError):
                    unchanged_runner = False
                    unchanged_binaries = False
                if not unchanged_runner:
                    reason = "RUNNER_INTEGRITY_FAILURE"
                    break
                if not unchanged_binaries:
                    reason = "SANDBOX_BINARY_INTEGRITY_FAILURE"
                    break
                completed.append(receipt_record)
                store.write_checkpoint(
                    campaign_id,
                    plan_digest,
                    "SAMPLE_RECORDED",
                    sample_number,
                    len(completed),
                )
                current_sample = None
            else:
                if (
                    len(completed) == plan.samples
                    and len({sample["output_sha256"] for sample in completed}) == 1
                    and all(sample["output_sha256"] == plan.expected_result_digest for sample in completed)
                    and self._plan_is_unchanged(plan, canonical_plan, plan_digest)
                    and self._verify_runner(plan) == runner_digest
                    and time.monotonic() - started_monotonic <= plan.max_total_duration
                ):
                    status = "COMPLETED"
                    verdict = "DEMONSTRATED"
                    reason = "Five deterministic samples satisfied the fixed repeatability contract"

            ended_at = self._utc_now()
            campaign_record.update(
                {
                    "status": status,
                    "verdict": verdict,
                    "reason": reason,
                    "ended_at": ended_at,
                    "duration_seconds": time.monotonic() - started_monotonic,
                    "samples_completed": len(completed),
                    "samples_expected": plan.samples,
                    "performance_claim": False,
                }
            )
            store.write_json("campaign.json", campaign_record)
            store.write_checkpoint(
                campaign_id,
                plan_digest,
                "TERMINAL",
                None if status == "COMPLETED" else current_sample,
                len(completed),
            )
            return ExperimentCampaignResult(
                campaign_id,
                plan_digest,
                status,
                verdict,
                len(completed),
                str(store.path),
                reason,
            )
        except KeyboardInterrupt:
            return ExperimentCampaignResult(
                campaign_id,
                plan_digest,
                "INTERRUPTED",
                "NOT_DEMONSTRATED",
                len(completed),
                str(store.path) if store else None,
                "Campaign interrupted; persisted status remains non-complete",
            )
        except Exception as exc:
            return ExperimentCampaignResult(
                campaign_id,
                plan_digest,
                "ABORTED",
                "NOT_DEMONSTRATED",
                len(completed),
                str(store.path) if store else None,
                f"Evidence or campaign error: {type(exc).__name__}: {exc}",
            )
        finally:
            if store is not None:
                store.close()
