import hashlib
import fcntl
import json
import math
import os
import selectors
import signal
import stat
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from .verifier import AntiFakeVerifier


@dataclass(frozen=True)
class DryRunReceipt:
    experiment_id: str
    fixture_id: str
    replay_status: str
    verdict: str
    reason: str
    median_speedup_factor: float
    simulation_only: bool
    hardware_accessed: bool
    commands_executed: int
    system_state_changed: bool
    rollback_status: str
    trace: Tuple[str, ...]

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


class DryRunExecutor:
    """Replays bundled synthetic evidence without running workloads or touching the host."""

    FIXTURES = {
        "ada_d3cold_false_positive": "ada_d3cold_false_positive",
        "no_safe_gain_found": "no_safe_gain_found",
    }
    MAX_FIXTURE_BYTES = 1024 * 1024

    def __init__(self):
        self.fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"
        self.verifier = AntiFakeVerifier()

    def _load_fixture_file(self, fixture_id: str, filename: str) -> Dict[str, object]:
        path = self.fixtures_dir / self.FIXTURES[fixture_id] / filename
        if path.stat().st_size > self.MAX_FIXTURE_BYTES:
            raise ValueError(f"Fixture file exceeds {self.MAX_FIXTURE_BYTES} bytes: {filename}")
        with path.open(encoding="utf-8") as fixture_file:
            data = json.load(fixture_file)
        if not isinstance(data, dict):
            raise ValueError(f"Fixture file must contain a JSON object: {filename}")
        return data

    def replay_fixture(self, fixture_id: str) -> DryRunReceipt:
        if fixture_id not in self.FIXTURES:
            raise ValueError(f"Unknown fixture: {fixture_id}")

        benchmark = self._load_fixture_file(fixture_id, "mock_benchmark.json")
        telemetry = self._load_fixture_file(fixture_id, "mock_telemetry.json")
        verification = self.verifier.verify_experiment(
            baseline_runs=benchmark.get("baseline_runs"),
            intervention_runs=benchmark.get("intervention_runs"),
            software_telemetry=telemetry,
            negative_control_delta=benchmark.get("negative_control_delta"),
        )

        return DryRunReceipt(
            experiment_id=f"DRY_RUN_{fixture_id.upper()}",
            fixture_id=fixture_id,
            replay_status="COMPLETED",
            verdict=verification.verdict.value,
            reason=verification.reason,
            median_speedup_factor=verification.median_speedup_factor,
            simulation_only=True,
            hardware_accessed=False,
            commands_executed=0,
            system_state_changed=False,
            rollback_status="NOT_REQUIRED_NO_STATE_CHANGE",
            trace=(
                "FIXTURE_LOADED",
                "SYNTHETIC_EVIDENCE_REPLAYED",
                "VERIFIER_COMPLETED",
                "NO_HOST_ACTIONS_PERFORMED",
            ),
        )


@dataclass(frozen=True)
class WorkloadReceipt:
    workload_id: str
    status: str
    output_sha256: Optional[str]
    elapsed_seconds: Optional[float]
    sandbox: str
    hardware_telemetry_reads: int
    device_control_operations: int
    persistent_host_state_changed: bool
    sandboxed_processes_started: int
    reason: str
    runner_sha256: Optional[str] = None
    process_exit_code: Optional[int] = None

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


class SandboxUnavailableError(RuntimeError):
    pass


class SyntheticWorkloadExecutor:
    """Runs one fixed, unprivileged CPU workload inside a rootless bubblewrap sandbox."""

    WORKLOAD_ID = "sha256-chain-v1"
    RUNNER_SHA256 = "6bcbbad859e2bf01898d55196d893335fa6dee94dcc64ebcae0a379a0dde8edf"
    EXPECTED_OUTPUT_SHA256 = "40c778e38799bb8b101d4bb6af933608069274c553be25f62398fa852030ad68"
    WALL_TIMEOUT_SECONDS = 8
    CPU_LIMIT_SECONDS = 4
    ADDRESS_SPACE_LIMIT_BYTES = 256 * 1024 * 1024
    OUTPUT_LIMIT_BYTES = 16 * 1024
    NOFILE_LIMIT = 32

    def __init__(self):
        self.runner_path = Path(__file__).resolve().with_name("workload_runner.py")

    @staticmethod
    def _trusted_executable(candidates: Tuple[str, ...]) -> Optional[str]:
        for candidate in candidates:
            path = Path(candidate)
            if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
                continue
            resolved = path.resolve()
            metadata = resolved.stat()
            if metadata.st_uid != 0 or metadata.st_mode & (
                stat.S_IWGRP | stat.S_IWOTH | stat.S_ISUID | stat.S_ISGID
            ):
                continue
            return str(resolved)
        return None

    def _sandbox_binaries(self) -> Tuple[str, str]:
        bubblewrap = self._trusted_executable(("/usr/bin/bwrap", "/bin/bwrap"))
        python = self._trusted_executable(("/usr/bin/python3", "/bin/python3"))
        if bubblewrap is None or python is None:
            raise SandboxUnavailableError(
                "Trusted root-owned bubblewrap and system Python executables are required"
            )
        return bubblewrap, python

    @classmethod
    def _apply_resource_limits(cls):
        import resource

        resource.setrlimit(
            resource.RLIMIT_CPU, (cls.CPU_LIMIT_SECONDS, cls.CPU_LIMIT_SECONDS)
        )
        resource.setrlimit(
            resource.RLIMIT_AS,
            (cls.ADDRESS_SPACE_LIMIT_BYTES, cls.ADDRESS_SPACE_LIMIT_BYTES),
        )
        resource.setrlimit(
            resource.RLIMIT_NOFILE, (cls.NOFILE_LIMIT, cls.NOFILE_LIMIT)
        )
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    def _sealed_runner_fd(self) -> int:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        runner_source_fd = os.open(self.runner_path, flags)
        try:
            metadata = os.fstat(runner_source_fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("Fixed workload runner is not a regular file")
            if metadata.st_size > 256 * 1024:
                raise ValueError("Fixed workload runner exceeds the reviewed size limit")
            chunks = []
            remaining = metadata.st_size
            while remaining:
                chunk = os.read(runner_source_fd, remaining)
                if not chunk:
                    raise OSError("Fixed workload runner changed during read")
                chunks.append(chunk)
                remaining -= len(chunk)
            runner_bytes = b"".join(chunks)
        finally:
            os.close(runner_source_fd)
        runner_digest = hashlib.sha256(runner_bytes).hexdigest()
        if runner_digest != self.RUNNER_SHA256:
            raise ValueError("Fixed workload runner does not match the reviewed version")
        if not hasattr(os, "memfd_create"):
            raise OSError("Sealed in-memory runner files are required on this platform")
        runner_fd = os.memfd_create(
            "ihra-fixed-workload-runner",
            getattr(os, "MFD_ALLOW_SEALING", 0),
        )
        try:
            view = memoryview(runner_bytes)
            while view:
                written = os.write(runner_fd, view)
                view = view[written:]
            seals = (
                fcntl.F_SEAL_SEAL
                | fcntl.F_SEAL_SHRINK
                | fcntl.F_SEAL_GROW
                | fcntl.F_SEAL_WRITE
            )
            fcntl.fcntl(runner_fd, fcntl.F_ADD_SEALS, seals)
            if fcntl.fcntl(runner_fd, fcntl.F_GET_SEALS) & seals != seals:
                raise OSError("Could not seal fixed workload runner")
            if hashlib.sha256(os.pread(runner_fd, len(runner_bytes), 0)).hexdigest() != runner_digest:
                raise OSError("Sealed workload runner failed readback verification")
            os.lseek(runner_fd, 0, os.SEEK_SET)
            return runner_fd
        except Exception:
            os.close(runner_fd)
            raise

    def _sandbox_command(
        self, bubblewrap: str, python: str, runner_fd: int
    ) -> Tuple[str, ...]:
        return (
            bubblewrap,
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
            "--ro-bind", "/usr", "/usr",
            "--ro-bind-try", "/lib", "/lib",
            "--ro-bind-try", "/lib64", "/lib64",
            "--dir", "/etc",
            "--ro-bind-try", "/etc/ld.so.cache", "/etc/ld.so.cache",
            "--dir", "/app",
            "--ro-bind-data", str(runner_fd), "/app/workload_runner.py",
            "--proc", "/proc",
            "--dev", "/dev",
            "--tmpfs", "/tmp",
            "--tmpfs", "/work",
            "--chdir", "/work",
            "--clearenv",
            "--setenv", "PATH", "/usr/bin:/bin",
            "--setenv", "PYTHONHASHSEED", "0",
            python,
            "-I",
            "/app/workload_runner.py",
        )

    @staticmethod
    def _terminate(process: subprocess.Popen):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()

    def _collect_output(
        self, process: subprocess.Popen, deadline: float
    ) -> Tuple[bytes, bytes, Optional[str]]:
        selector = selectors.DefaultSelector()
        stdout = bytearray()
        stderr = bytearray()
        streams = {process.stdout: stdout, process.stderr: stderr}
        total = 0
        for stream in streams:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)

        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._terminate(process)
                    return bytes(stdout), bytes(stderr), "Workload exceeded wall-clock timeout"
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fd, min(4096, self.OUTPUT_LIMIT_BYTES + 1 - total))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    total += len(chunk)
                    if total > self.OUTPUT_LIMIT_BYTES:
                        self._terminate(process)
                        return bytes(stdout), bytes(stderr), "Workload exceeded output limit"
                    streams[key.fileobj].extend(chunk)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._terminate(process)
                return bytes(stdout), bytes(stderr), "Workload exceeded wall-clock timeout"
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                self._terminate(process)
                return bytes(stdout), bytes(stderr), "Workload exceeded wall-clock timeout"
            return bytes(stdout), bytes(stderr), None
        except KeyboardInterrupt:
            self._terminate(process)
            raise
        finally:
            selector.close()

    def run(self, workload_id: str) -> WorkloadReceipt:
        if workload_id != self.WORKLOAD_ID:
            raise ValueError(f"Unsupported workload: {workload_id}")

        try:
            bubblewrap, python = self._sandbox_binaries()
        except SandboxUnavailableError as exc:
            return WorkloadReceipt(
                workload_id=workload_id,
                status="DENIED",
                output_sha256=None,
                elapsed_seconds=None,
                sandbox="bubblewrap-rootless",
                hardware_telemetry_reads=0,
                device_control_operations=0,
                persistent_host_state_changed=False,
                sandboxed_processes_started=0,
                reason=str(exc),
            )

        runner_fd = None
        try:
            runner_fd = self._sealed_runner_fd()
            command = self._sandbox_command(bubblewrap, python, runner_fd)
        except (OSError, ValueError) as exc:
            if runner_fd is not None:
                os.close(runner_fd)
            return WorkloadReceipt(
                workload_id=workload_id,
                status="FAILED",
                output_sha256=None,
                elapsed_seconds=None,
                sandbox="bubblewrap-rootless",
                hardware_telemetry_reads=0,
                device_control_operations=0,
                persistent_host_state_changed=False,
                sandboxed_processes_started=0,
                reason=f"Fixed workload preflight failed: {exc}",
                runner_sha256=None,
                process_exit_code=None,
            )
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
                env={"PATH": "/usr/bin:/bin", "LANG": "C"},
                start_new_session=True,
                preexec_fn=self._apply_resource_limits,
                pass_fds=(runner_fd,),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return WorkloadReceipt(
                workload_id=workload_id,
                status="FAILED",
                output_sha256=None,
                elapsed_seconds=time.monotonic() - started,
                sandbox="bubblewrap-rootless",
                hardware_telemetry_reads=0,
                device_control_operations=0,
                persistent_host_state_changed=False,
                sandboxed_processes_started=0,
                reason=f"Could not start sandbox: {exc}",
                runner_sha256=self.RUNNER_SHA256,
                process_exit_code=None,
            )
        finally:
            if runner_fd is not None:
                os.close(runner_fd)

        try:
            stdout, stderr, failure = self._collect_output(
                process, started + self.WALL_TIMEOUT_SECONDS
            )
        except KeyboardInterrupt:
            self._terminate(process)
            raise
        elapsed = time.monotonic() - started
        if failure is not None:
            return WorkloadReceipt(
                workload_id=workload_id,
                status="FAILED",
                output_sha256=None,
                elapsed_seconds=elapsed,
                sandbox="bubblewrap-rootless",
                hardware_telemetry_reads=0,
                device_control_operations=0,
                persistent_host_state_changed=False,
                sandboxed_processes_started=1,
                reason=failure,
                runner_sha256=self.RUNNER_SHA256,
                process_exit_code=process.returncode,
            )

        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()[:2048]
            return WorkloadReceipt(
                workload_id=workload_id,
                status="FAILED",
                output_sha256=None,
                elapsed_seconds=elapsed,
                sandbox="bubblewrap-rootless",
                hardware_telemetry_reads=0,
                device_control_operations=0,
                persistent_host_state_changed=False,
                sandboxed_processes_started=1,
                reason=f"Sandboxed workload exited {process.returncode}: {detail}",
                runner_sha256=self.RUNNER_SHA256,
                process_exit_code=process.returncode,
            )

        try:
            result = json.loads(stdout)
        except ValueError as exc:
            result = None
            parse_error = str(exc)
        else:
            parse_error = "Workload result does not match the fixed contract"
        reported_elapsed = (
            result.get("elapsed_seconds") if isinstance(result, dict) else None
        )
        if (
            not isinstance(result, dict)
            or result.get("workload_id") != self.WORKLOAD_ID
            or result.get("iterations") != 20000
            or not isinstance(result.get("output_sha256"), str)
            or result["output_sha256"] != self.EXPECTED_OUTPUT_SHA256
            or isinstance(reported_elapsed, bool)
            or not isinstance(reported_elapsed, (int, float))
            or (
                isinstance(reported_elapsed, float)
                and not math.isfinite(reported_elapsed)
            )
            or not 0 <= reported_elapsed <= self.WALL_TIMEOUT_SECONDS
            or set(result) != {
                "workload_id",
                "iterations",
                "output_sha256",
                "elapsed_seconds",
            }
        ):
            return WorkloadReceipt(
                workload_id=workload_id,
                status="FAILED",
                output_sha256=None,
                elapsed_seconds=elapsed,
                sandbox="bubblewrap-rootless",
                hardware_telemetry_reads=0,
                device_control_operations=0,
                persistent_host_state_changed=False,
                sandboxed_processes_started=1,
                reason=parse_error,
                runner_sha256=self.RUNNER_SHA256,
                process_exit_code=process.returncode,
            )

        return WorkloadReceipt(
            workload_id=workload_id,
            status="COMPLETED",
            output_sha256=result["output_sha256"],
            elapsed_seconds=elapsed,
            sandbox="bubblewrap-rootless",
            hardware_telemetry_reads=0,
            device_control_operations=0,
            persistent_host_state_changed=False,
            sandboxed_processes_started=1,
            reason=(
                "Fixed synthetic CPU workload completed in a network-isolated sandbox "
                "with read-only host mounts and isolated temporary filesystems"
            ),
            runner_sha256=self.RUNNER_SHA256,
            process_exit_code=process.returncode,
        )
