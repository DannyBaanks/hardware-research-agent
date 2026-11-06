import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

from core.cli import cmd_inspect_synthetic_campaign
from core.cpu_power import MONITORED_STATE_FIELDS
from core.experiment import (
    _CampaignEvidence,
    SyntheticExperimentPlan,
    SyntheticExperimentRunner,
)
from core.executor import (
    SandboxUnavailableError,
    SyntheticWorkloadExecutor,
    WorkloadReceipt,
)
from core.planning import SyntheticExperimentProposal
from core.recovery import inspect_synthetic_campaign
from tests.tty_test_utils import authorize_in_pty


class _SimulatedAbruptStop(BaseException):
    pass


class TestCampaignRecoveryInspection(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        os.chmod(self.root, 0o700)
        self.evidence_base = self.root / "state" / "ihra" / "synthetic-experiments"
        self.campaign_id = "0123456789abcdef0123456789abcdef"
        self.campaign_dir = self.evidence_base / self.campaign_id
        self.plan = SyntheticExperimentPlan.fixed_repeatability_plan()
        self.proposal = SyntheticExperimentProposal(
            schema_version=1,
            proposal_id="test-inspection",
            rationale="Exercise read-only campaign evidence inspection.",
            plan=self.plan,
        )
        self._write_campaign()

    @staticmethod
    def _json_bytes(value):
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("ascii") + b"\n"

    @staticmethod
    def _host_state():
        readings = {
            "scaling_driver": "amd-pstate-epp",
            "governor": "performance",
            "energy_performance_preference": "performance",
            "boost_enabled": True,
            "boost_control": "/sys/devices/system/cpu/cpufreq/boost",
            "platform_profile": "performance",
        }
        assert set(readings) == set(MONITORED_STATE_FIELDS)
        return {field: readings[field] for field in MONITORED_STATE_FIELDS}

    def _write_campaign(self, status="COMPLETED", modern=True, samples=5):
        self.evidence_base.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.evidence_base, 0o700)
        if self.campaign_dir.exists():
            for path in self.campaign_dir.iterdir():
                path.unlink()
            self.campaign_dir.rmdir()
        self.campaign_dir.mkdir(parents=True, mode=0o700)
        plan_bytes = self.plan.canonical_bytes()
        proposal_bytes = self.proposal.canonical_bytes()
        host_state = self._host_state()
        prestate_bytes = self._json_bytes(
            {
                "campaign_id": self.campaign_id,
                "plan_sha256": self.plan.sha256,
                "recorded_at": "2026-11-03T00:00:00+00:00",
                "state": host_state,
            }
        )
        poststate_bytes = self._json_bytes(
            {
                "campaign_id": self.campaign_id,
                "plan_sha256": self.plan.sha256,
                "recorded_at": "2026-11-03T00:00:05+00:00",
                "state": host_state,
                "observation_error": None,
            }
        )
        rollback_bytes = self._json_bytes(
            {
                "campaign_id": self.campaign_id,
                "plan_sha256": self.plan.sha256,
                "recorded_at": "2026-11-03T00:00:05+00:00",
                "status": "VERIFIED_UNCHANGED",
                "reason": "Monitored host state at close equals the captured prestate",
                "differences": [],
                "restoration_executed": False,
                "restoration_command": None,
                "restoration_receipt": None,
                "observation_error": None,
                "close_state": host_state,
                "verified_state": host_state,
                "prestate_sha256": hashlib.sha256(prestate_bytes).hexdigest(),
                "poststate_sha256": hashlib.sha256(poststate_bytes).hexdigest(),
            }
        )
        campaign_record = {
            "campaign_id": self.campaign_id,
            "plan_sha256": self.plan.sha256,
            "authorization": (
                {
                    "method": "local_tty_confirmation",
                    "plan_sha256": self.plan.sha256,
                    "proposal_sha256": self.proposal.sha256,
                }
                if modern
                else {
                    "method": "explicit_operator_plan_digest",
                    "authorized_plan_sha256": self.plan.sha256,
                }
            ),
            "status": status,
            "verdict": "DEMONSTRATED" if status == "COMPLETED" else "NOT_DEMONSTRATED",
            "samples_completed": samples if status == "COMPLETED" else 0,
            "samples_expected": self.plan.samples,
            "performance_claim": False,
            "runner": {
                "workload_id": self.plan.workload_id,
                "workload_version": self.plan.workload_version,
                "runner_sha256": self.plan.runner_digest,
            },
        }
        if modern:
            campaign_record["rollback_status"] = "VERIFIED_UNCHANGED"
        files = {
            "plan.json": plan_bytes,
            "campaign.json": self._json_bytes(campaign_record),
        }
        if modern:
            files["proposal.json"] = proposal_bytes
            files["prestate.json"] = prestate_bytes
            files["poststate.json"] = poststate_bytes
            files["rollback.json"] = rollback_bytes
            files["checkpoint.json"] = self._json_bytes(
                {
                    "schema_version": 1,
                    "campaign_id": self.campaign_id,
                    "plan_sha256": self.plan.sha256,
                    "state": "TERMINAL" if status == "COMPLETED" else "RUNNING",
                    "current_sample": None,
                    "samples_completed": samples if status == "COMPLETED" else 0,
                    "updated_at": "2026-11-03T00:00:00+00:00",
                }
            )
        for sample in range(1, samples + 1):
            files[f"sample-{sample:02d}.started.json"] = self._json_bytes(
                {
                    "campaign_id": self.campaign_id,
                    "plan_sha256": self.plan.sha256,
                    "sample": sample,
                    "started_at": "2026-11-03T00:00:00+00:00",
                    "status": "RUNNING",
                }
            )
            files[f"sample-{sample:02d}.receipt.json"] = self._json_bytes(
                {
                    "workload_id": self.plan.workload_id,
                    "status": "COMPLETED",
                    "output_sha256": self.plan.expected_result_digest,
                    "elapsed_seconds": 0.02,
                    "sandbox": "bubblewrap-rootless",
                    "hardware_telemetry_reads": 0,
                    "device_control_operations": 0,
                    "persistent_host_state_changed": False,
                    "sandboxed_processes_started": 1,
                    "reason": "fixed workload completed",
                    "runner_sha256": self.plan.runner_digest,
                    "process_exit_code": 0,
                    "validation": "PASS",
                    "campaign_id": self.campaign_id,
                    "plan_sha256": self.plan.sha256,
                    "sample": sample,
                    "started_at": "2026-11-03T00:00:00+00:00",
                    "measured_elapsed_seconds": 0.021,
                }
            )
        for name, data in files.items():
            path = self.campaign_dir / name
            path.write_bytes(data)
            path.chmod(0o600)
        manifest = "".join(
            f"{hashlib.sha256(data).hexdigest()}  {name}\n"
            for name, data in sorted(files.items())
        )
        manifest_path = self.campaign_dir / "SHA256SUMS.txt"
        manifest_path.write_text(manifest, encoding="ascii")
        manifest_path.chmod(0o600)

    def _refresh_manifest(self):
        manifest_path = self.campaign_dir / "SHA256SUMS.txt"
        lines = []
        for path in sorted(self.campaign_dir.iterdir()):
            if path.name == "SHA256SUMS.txt":
                continue
            lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
        manifest_path.write_text("\n".join(lines) + "\n", encoding="ascii")
        manifest_path.chmod(0o600)

    @staticmethod
    def _read_proc_entry(pid):
        try:
            proc_path = Path("/proc") / str(pid)
            data = (proc_path / "stat").read_text()
            executable = os.readlink(proc_path / "exe")
            command_line = (
                proc_path / "cmdline"
            ).read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            return None
        fields = data.rsplit(")", 1)[1].split()
        return {
            "state": fields[0],
            "parent_pid": int(fields[1]),
            "start_time": fields[19],
            "executable": executable,
            "command_line": command_line,
        }

    @classmethod
    def _proc_descendants(cls, root_pid):
        entries = {}
        for path in Path("/proc").iterdir():
            if not path.name.isdigit():
                continue
            entry = cls._read_proc_entry(int(path.name))
            if entry is not None:
                entries[int(path.name)] = entry
        descendants = {}
        parents = {root_pid}
        while parents:
            children = {
                pid: entry
                for pid, entry in entries.items()
                if entry["parent_pid"] in parents and pid not in descendants
            }
            descendants.update(children)
            parents = set(children)
        return descendants

    @classmethod
    def _matching_live_processes(cls, identities):
        live = []
        for pid, start_time in identities.items():
            entry = cls._read_proc_entry(pid)
            if (
                entry is not None
                and entry["start_time"] == start_time
                and entry["state"] != "Z"
            ):
                live.append(pid)
        return live

    @classmethod
    def _cleanup_harness(cls, process, identities):
        if process.poll() is None:
            try:
                os.kill(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
        for pid in cls._matching_live_processes(identities):
            entry = cls._read_proc_entry(pid)
            if entry is not None and entry["start_time"] == identities[pid]:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def _start_sigkill_harness(self, phase):
        plan = SyntheticExperimentPlan.fixed_repeatability_plan()
        campaign_id = f"{100 + (phase == 'during-sample'):032x}"
        evidence_base = (
            self.root / f"m4-{phase}" / "state" / "ihra" / "synthetic-experiments"
        )
        ready_path = self.root / f"m4-{phase}.ready.json"
        error_path = self.root / f"m4-{phase}.error.txt"
        child_code = r'''
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.experiment import SyntheticExperimentPlan, SyntheticExperimentRunner
from core.executor import SyntheticWorkloadExecutor
from core.planning import SyntheticExperimentProposal
from tests.tty_test_utils import authorize_in_pty

evidence_base = Path(sys.argv[1])
campaign_id = sys.argv[2]
ready_path = Path(sys.argv[3])
error_path = Path(sys.argv[4])
phase = sys.argv[5]

def publish_ready(
    child_pid=None,
    child_start_time=None,
    child_running=None,
    runner_pid=None,
    runner_start_time=None,
    process_identities=None,
    error=None,
):
    value = {
        "campaign_id": campaign_id,
        "child_pid": child_pid,
        "child_start_time": child_start_time,
        "child_running": child_running,
        "runner_pid": runner_pid,
        "runner_start_time": runner_start_time,
        "process_identities": process_identities or [],
        "error": error,
    }
    temporary = ready_path.with_name(ready_path.name + ".tmp")
    temporary.write_text(json.dumps(value), encoding="ascii")
    os.replace(temporary, ready_path)

try:
    plan = SyntheticExperimentPlan.fixed_repeatability_plan()
    proposal = SyntheticExperimentProposal(
        schema_version=1,
        proposal_id="test-m4-process-interruption",
        rationale="Exercise controlled parent termination under the fixed sandbox contract.",
        plan=plan,
    )
    authorization = authorize_in_pty(
        plan.sha256,
        proposal.sha256,
        proposal.canonical_bytes().decode("ascii"),
    )
    run_count = [0]

    if phase == "after-receipt":
        original_run = SyntheticWorkloadExecutor.run
        def pause_before_second_dispatch(self, workload_id):
            run_count[0] += 1
            if run_count[0] == 2:
                publish_ready()
                while True:
                    time.sleep(1)
            return original_run(self, workload_id)
        SyntheticWorkloadExecutor.run = pause_before_second_dispatch
    else:
        original_popen = subprocess.Popen
        bwrap_count = [0]
        def capture_descendant_identities(root_pid):
            entries = {}
            for name in os.listdir("/proc"):
                if not name.isdigit():
                    continue
                try:
                    fields = (
                        Path("/proc") / name / "stat"
                    ).read_text().rsplit(")", 1)[1].split()
                    entries[int(name)] = {
                        "parent_pid": int(fields[1]),
                        "start_time": fields[19],
                    }
                except OSError:
                    continue
            identities = {}
            parents = {root_pid}
            while parents:
                children = {
                    pid: entry
                    for pid, entry in entries.items()
                    if entry["parent_pid"] in parents and pid not in identities
                }
                identities.update(children)
                parents = set(children)
            return [
                {"pid": pid, "start_time": entry["start_time"]}
                for pid, entry in identities.items()
            ]

        def find_runner_and_kill_parent(process, parent_pid, bwrap_start_time):
            deadline = time.monotonic() + 1
            runner_identity = None
            try:
                while time.monotonic() < deadline and process.poll() is None:
                    pending = [process.pid]
                    visited = set()
                    while pending:
                        pid = pending.pop()
                        if pid in visited:
                            continue
                        visited.add(pid)
                        proc_path = Path("/proc") / str(pid)
                        try:
                            children = (proc_path / "task" / str(pid) / "children").read_text()
                            pending.extend(int(child) for child in children.split())
                            executable = os.readlink(proc_path / "exe")
                            command_line = (proc_path / "cmdline").read_bytes()
                        except OSError:
                            continue
                        if (
                            Path(executable).name.startswith("python3")
                            and b"/app/workload_runner.py" in command_line
                        ):
                            stat_fields = (
                                proc_path / "stat"
                            ).read_text().rsplit(")", 1)[1].split()
                            runner_identity = (pid, stat_fields[19])
                            os.kill(pid, signal.SIGSTOP)
                            identities = capture_descendant_identities(parent_pid)
                            publish_ready(
                                process.pid,
                                bwrap_start_time,
                                process.poll() is None,
                                runner_identity[0],
                                runner_identity[1],
                                identities,
                            )
                            os.kill(parent_pid, signal.SIGKILL)
                            return
                    time.sleep(0.001)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            else:
                error = "fixed workload process was not observed before its deadline"
            publish_ready(
                process.pid,
                bwrap_start_time,
                process.poll() is None,
                runner_identity[0] if runner_identity else None,
                runner_identity[1] if runner_identity else None,
                capture_descendant_identities(parent_pid),
                error,
            )
            os.kill(parent_pid, signal.SIGKILL)

        def terminate_parent_after_second_sandbox_start(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            command = args[0] if args else kwargs.get("args")
            if command and command[0] == "/usr/bin/bwrap":
                bwrap_count[0] += 1
                if bwrap_count[0] == 2:
                    stat_fields = (
                        Path("/proc") / str(process.pid) / "stat"
                    ).read_text().rsplit(")", 1)[1].split()
                    watcher = threading.Thread(
                        target=find_runner_and_kill_parent,
                        args=(
                            process,
                            os.getpid(),
                            stat_fields[19],
                        ),
                        daemon=True,
                    )
                    watcher.start()
            return process
        subprocess.Popen = terminate_parent_after_second_sandbox_start

    from core import experiment
    with patch.object(experiment.uuid, "uuid4", return_value=SimpleNamespace(hex=campaign_id)):
        runner = SyntheticExperimentRunner(evidence_base=evidence_base)
        result = runner.run(
            plan,
            authorization=authorization,
            proposal_bytes=proposal.canonical_bytes(),
        )
        error_path.write_text(json.dumps(result.to_dict()), encoding="utf-8")
except BaseException as exc:
    error_path.write_text(f"{type(exc).__name__}: {exc}", encoding="utf-8")
    raise
'''
        with error_path.open("wb") as error_output:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    child_code,
                    str(evidence_base),
                    campaign_id,
                    str(ready_path),
                    str(error_path),
                    phase,
                ],
                cwd=Path(__file__).resolve().parents[1],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=error_output,
                close_fds=True,
            )
        deadline = time.monotonic() + 15
        while not ready_path.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                detail = error_path.read_text() or "empty harness error output"
                self.fail(f"M4 harness exited before its gate: {detail}")
            time.sleep(0.01)
        if not ready_path.exists():
            process.kill()
            process.wait(timeout=5)
            detail = error_path.read_text() if error_path.exists() else "gate timed out"
            self.fail(f"M4 harness did not reach its interruption gate: {detail}")
        ready = json.loads(ready_path.read_text())
        identities = {
            pid: entry["start_time"]
            for pid, entry in self._proc_descendants(process.pid).items()
        }
        if ready["child_pid"] is not None and ready["child_start_time"] is not None:
            identities[ready["child_pid"]] = ready["child_start_time"]
        if ready["runner_pid"] is not None and ready["runner_start_time"] is not None:
            identities[ready["runner_pid"]] = ready["runner_start_time"]
        identities.update({
            item["pid"]: item["start_time"]
            for item in ready["process_identities"]
        })
        self.addCleanup(self._cleanup_harness, process, identities)
        return process, ready, evidence_base

    def _terminate_harness_and_verify_children(self, process, child_identities):
        try:
            os.kill(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.assertEqual(process.wait(timeout=5), -signal.SIGKILL)
        deadline = time.monotonic() + 5
        while self._matching_live_processes(child_identities) and time.monotonic() < deadline:
            time.sleep(0.02)
        survivors = self._matching_live_processes(child_identities)
        if survivors:
            survivor_details = {
                pid: self._read_proc_entry(pid) for pid in survivors
            }
            for pid in survivors:
                entry = self._read_proc_entry(pid)
                if entry is not None and entry["start_time"] == child_identities[pid]:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            self.fail(
                f"Bubblewrap descendants survived parent SIGKILL: {survivor_details}"
            )
        return True

    @staticmethod
    def _assert_manifest_matches_files(test_case, campaign_dir):
        entries = {}
        for line in (campaign_dir / "SHA256SUMS.txt").read_text(encoding="ascii").splitlines():
            digest, name = line.split("  ", 1)
            entries[name] = digest
        present = {path.name for path in campaign_dir.iterdir() if path.name != "SHA256SUMS.txt"}
        test_case.assertEqual(set(entries), present)
        for name, digest in entries.items():
            test_case.assertEqual(
                hashlib.sha256((campaign_dir / name).read_bytes()).hexdigest(),
                digest,
                name,
            )

    def test_sigkill_during_real_bubblewrap_sample_kills_child_and_keeps_campaign_incomplete(self):
        executor = SyntheticWorkloadExecutor()
        try:
            executor._sandbox_binaries()
        except SandboxUnavailableError as exc:
            self.skipTest(f"Trusted Bubblewrap unavailable: {exc}")

        process, ready, evidence_base = self._start_sigkill_harness("during-sample")
        campaign_id = ready["campaign_id"]
        campaign_dir = evidence_base / campaign_id
        child_pid = ready["child_pid"]
        self.assertIsInstance(child_pid, int)
        self.assertTrue(ready["child_running"])
        self.assertIsInstance(ready["child_start_time"], str)
        self.assertIsInstance(ready["runner_pid"], int)
        self.assertIsInstance(ready["runner_start_time"], str)
        self.assertIsNone(ready["error"])
        child_identities = {
            item["pid"]: item["start_time"]
            for item in ready["process_identities"]
        }
        self.assertIn(ready["runner_pid"], child_identities)
        descendants = self._proc_descendants(process.pid)
        if child_pid in descendants:
            self.assertEqual(descendants[child_pid]["start_time"], ready["child_start_time"])
        child_identities.update({
            pid: entry["start_time"] for pid, entry in descendants.items()
        })
        child_identities[child_pid] = ready["child_start_time"]
        self.assertTrue(child_identities)
        self.assertTrue((campaign_dir / "sample-01.receipt.json").is_file())
        self.assertTrue((campaign_dir / "sample-02.started.json").is_file())
        checkpoint = json.loads((campaign_dir / "checkpoint.json").read_text())
        self.assertEqual(checkpoint["state"], "SAMPLE_RUNNING")
        self.assertEqual(checkpoint["current_sample"], 2)

        self._terminate_harness_and_verify_children(process, child_identities)

        self._assert_manifest_matches_files(self, campaign_dir)
        receipt = json.loads((campaign_dir / "sample-01.receipt.json").read_text())
        self.assertEqual(receipt["validation"], "PASS")
        before = self._evidence_snapshot(campaign_dir)
        first = inspect_synthetic_campaign(campaign_id, evidence_base)
        second = inspect_synthetic_campaign(campaign_id, evidence_base)
        self.assertEqual(first.inspection_status, "ACTIVE_OR_UNKNOWN")
        self.assertEqual(first.verdict, "NOT_DEMONSTRATED")
        self.assertEqual(second.to_dict(), first.to_dict())
        self.assertEqual(self._evidence_snapshot(campaign_dir), before)

    def test_sigkill_after_durable_receipt_preserves_it_without_claiming_completion(self):
        executor = SyntheticWorkloadExecutor()
        try:
            executor._sandbox_binaries()
        except SandboxUnavailableError as exc:
            self.skipTest(f"Trusted Bubblewrap unavailable: {exc}")

        process, ready, evidence_base = self._start_sigkill_harness("after-receipt")
        campaign_id = ready["campaign_id"]
        campaign_dir = evidence_base / campaign_id
        self.assertEqual(ready["child_pid"], None)
        self.assertEqual(self._proc_descendants(process.pid), {})
        self.assertTrue((campaign_dir / "sample-01.receipt.json").is_file())
        self.assertTrue((campaign_dir / "sample-02.started.json").is_file())
        checkpoint = json.loads((campaign_dir / "checkpoint.json").read_text())
        self.assertEqual(checkpoint["state"], "SAMPLE_RUNNING")
        self.assertEqual(checkpoint["samples_completed"], 1)

        self._terminate_harness_and_verify_children(process, {})

        self._assert_manifest_matches_files(self, campaign_dir)
        receipt = json.loads((campaign_dir / "sample-01.receipt.json").read_text())
        self.assertEqual(receipt["validation"], "PASS")
        before = self._evidence_snapshot(campaign_dir)
        result = inspect_synthetic_campaign(campaign_id, evidence_base)
        self.assertEqual(result.inspection_status, "ACTIVE_OR_UNKNOWN")
        self.assertEqual(result.verdict, "NOT_DEMONSTRATED")
        self.assertEqual(self._evidence_snapshot(campaign_dir), before)

    def _run_until_simulated_cut(self, cut):
        campaign_id = f"{cut['id']:032x}"
        evidence_base = (
            self.root / f"m3-cut-{cut['id']}" / "state" / "ihra" / "synthetic-experiments"
        )
        plan = SyntheticExperimentPlan.fixed_repeatability_plan()
        proposal = SyntheticExperimentProposal(
            schema_version=1,
            proposal_id="test-m3-cut",
            rationale="Exercise recovery inspection after a simulated persistence cut.",
            plan=plan,
        )
        executor_calls = []

        class FakeExecutor:
            runner_path = Path(__file__).resolve().parents[1] / "core" / "workload_runner.py"

            def _sandbox_binaries(self):
                raise AssertionError("Campaign tests replace sandbox binary verification")

            def run(inner_self, workload_id):
                executor_calls.append(workload_id)
                if cut["point"] == "during-executor":
                    raise _SimulatedAbruptStop()
                return WorkloadReceipt(
                    workload_id=plan.workload_id,
                    status="COMPLETED",
                    output_sha256=plan.expected_result_digest,
                    elapsed_seconds=0.02,
                    sandbox="bubblewrap-rootless",
                    hardware_telemetry_reads=0,
                    device_control_operations=0,
                    persistent_host_state_changed=False,
                    sandboxed_processes_started=1,
                    reason="fixed workload completed",
                    runner_sha256=plan.runner_digest,
                    process_exit_code=0,
                )

        runner = SyntheticExperimentRunner(
            executor=FakeExecutor(),
            evidence_base=evidence_base,
            repository_root=Path(__file__).resolve().parents[1],
        )
        runner._verify_sandbox_binaries = Mock(
            return_value={
                "bubblewrap_path": "/usr/bin/bwrap",
                "bubblewrap_sha256": "a" * 64,
                "python_path": "/usr/bin/python3",
                "python_sha256": "b" * 64,
            }
        )
        authorization = authorize_in_pty(
            plan.sha256,
            proposal.sha256,
            proposal.canonical_bytes().decode("ascii"),
        )
        write_atomic = _CampaignEvidence._write_atomic
        write_checkpoint = _CampaignEvidence.write_checkpoint
        write_json = _CampaignEvidence.write_json

        def inject_atomic(store, name, data):
            point = cut["point"]
            if point == "before-start-marker" and name == "sample-01.started.json":
                raise _SimulatedAbruptStop()
            write_atomic(store, name, data)
            if point == "after-start-replace-before-manifest" and name == "sample-01.started.json":
                raise _SimulatedAbruptStop()
            if point == "after-receipt-replace-before-manifest" and name == "sample-01.receipt.json":
                raise _SimulatedAbruptStop()
            if point == "after-campaign-replace-before-manifest" and name == "campaign.json":
                campaign = json.loads(data)
                if campaign.get("status") == "COMPLETED":
                    raise _SimulatedAbruptStop()
            if point == "after-terminal-checkpoint-replace-before-manifest" and name == "checkpoint.json":
                checkpoint = json.loads(data)
                if checkpoint.get("state") == "TERMINAL":
                    raise _SimulatedAbruptStop()

        def inject_checkpoint(store, *args, **kwargs):
            state = args[2]
            if (
                cut["point"] == "after-start-before-running-checkpoint"
                and state == "SAMPLE_RUNNING"
            ):
                raise _SimulatedAbruptStop()
            if (
                cut["point"] == "before-recorded-checkpoint"
                and state == "SAMPLE_RECORDED"
            ):
                raise _SimulatedAbruptStop()
            result = write_checkpoint(store, *args, **kwargs)
            if (
                cut["point"] == "after-recorded-checkpoint"
                and state == "SAMPLE_RECORDED"
            ):
                raise _SimulatedAbruptStop()
            if cut["point"] == "after-terminal-checkpoint" and state == "TERMINAL":
                raise _SimulatedAbruptStop()
            return result

        def inject_json(store, name, value):
            if (
                cut["point"] == "after-executor-before-receipt"
                and name == "sample-01.receipt.json"
            ):
                raise _SimulatedAbruptStop()
            if (
                cut["point"] == "before-campaign-complete-record"
                and name == "campaign.json"
                and value.get("status") == "COMPLETED"
            ):
                raise _SimulatedAbruptStop()
            return write_json(store, name, value)

        with (
            patch.object(_CampaignEvidence, "_write_atomic", inject_atomic),
            patch.object(_CampaignEvidence, "write_checkpoint", inject_checkpoint),
            patch.object(_CampaignEvidence, "write_json", inject_json),
            patch("core.experiment.uuid.uuid4", return_value=SimpleNamespace(hex=campaign_id)),
        ):
            with self.assertRaises(_SimulatedAbruptStop):
                runner.run(
                    plan,
                    authorization=authorization,
                    proposal_bytes=proposal.canonical_bytes(),
                )

        campaign_dir = evidence_base / campaign_id
        self.assertTrue(campaign_dir.is_dir())
        self.assertEqual(len(executor_calls), cut["executor_calls"])
        return campaign_id, evidence_base, campaign_dir

    @staticmethod
    def _evidence_snapshot(campaign_dir):
        files = {
            path.name: (
                path.read_bytes(),
                path.stat().st_mode,
                path.stat().st_mtime_ns,
            )
            for path in campaign_dir.iterdir()
        }
        return files, campaign_dir.stat().st_mtime_ns

    def test_simulated_persistence_cuts_reinspect_conservatively_without_mutation(self):
        cuts = [
            {
                "id": 1,
                "point": "before-start-marker",
                "executor_calls": 0,
                "receipts": 0,
                "status": "ACTIVE_OR_UNKNOWN",
            },
            {
                "id": 2,
                "point": "after-start-replace-before-manifest",
                "executor_calls": 0,
                "receipts": 0,
                "status": "ACTIVE_OR_UNKNOWN",
            },
            {
                "id": 3,
                "point": "after-start-before-running-checkpoint",
                "executor_calls": 0,
                "receipts": 0,
                "status": "ACTIVE_OR_UNKNOWN",
            },
            {
                "id": 4,
                "point": "during-executor",
                "executor_calls": 1,
                "receipts": 0,
                "status": "ACTIVE_OR_UNKNOWN",
            },
            {
                "id": 5,
                "point": "after-executor-before-receipt",
                "executor_calls": 1,
                "receipts": 0,
                "status": "ACTIVE_OR_UNKNOWN",
            },
            {
                "id": 6,
                "point": "after-receipt-replace-before-manifest",
                "executor_calls": 1,
                "receipts": 1,
                "status": "ACTIVE_OR_UNKNOWN",
            },
            {
                "id": 7,
                "point": "before-recorded-checkpoint",
                "executor_calls": 1,
                "receipts": 1,
                "status": "ACTIVE_OR_UNKNOWN",
            },
            {
                "id": 8,
                "point": "after-recorded-checkpoint",
                "executor_calls": 1,
                "receipts": 1,
                "status": "ACTIVE_OR_UNKNOWN",
            },
            {
                "id": 9,
                "point": "before-campaign-complete-record",
                "executor_calls": 5,
                "receipts": 5,
                "status": "ACTIVE_OR_UNKNOWN",
            },
            {
                "id": 10,
                "point": "after-campaign-replace-before-manifest",
                "executor_calls": 5,
                "receipts": 5,
                "status": "INCOMPLETE",
            },
            {
                "id": 11,
                "point": "after-terminal-checkpoint-replace-before-manifest",
                "executor_calls": 5,
                "receipts": 5,
                "status": "INCOMPLETE",
            },
            {
                "id": 12,
                "point": "after-terminal-checkpoint",
                "executor_calls": 5,
                "receipts": 5,
                "status": "COMPLETED_VALID",
            },
        ]
        for cut in cuts:
            with self.subTest(point=cut["point"]):
                campaign_id, evidence_base, campaign_dir = self._run_until_simulated_cut(cut)
                before = self._evidence_snapshot(campaign_dir)

                first = inspect_synthetic_campaign(campaign_id, evidence_base)
                second = inspect_synthetic_campaign(campaign_id, evidence_base)

                self.assertEqual(first.inspection_status, cut["status"])
                self.assertEqual(first.verdict == "DEMONSTRATED", cut["status"] == "COMPLETED_VALID")
                self.assertEqual(second.to_dict(), first.to_dict())
                self.assertEqual(self._evidence_snapshot(campaign_dir), before)
                files = before[0]
                receipt_names = sorted(
                    name for name in files if name.endswith(".receipt.json")
                )
                self.assertEqual(len(receipt_names), cut["receipts"])
                manifest = files["SHA256SUMS.txt"][0].decode("ascii")
                manifest_entries = {
                    line.split("  ", 1)[1]: line.split("  ", 1)[0]
                    for line in manifest.splitlines()
                }
                for name in receipt_names:
                    if name in manifest_entries:
                        self.assertEqual(
                            hashlib.sha256(files[name][0]).hexdigest(),
                            manifest_entries[name],
                        )
                if cut["point"] == "after-receipt-replace-before-manifest":
                    self.assertNotIn("sample-01.receipt.json", manifest_entries)
                if cut["status"] != "COMPLETED_VALID":
                    self.assertNotEqual(first.verdict, "DEMONSTRATED")

    def test_orphan_atomic_temporary_is_incomplete_and_inspection_is_non_mutating(self):
        temporary = self.campaign_dir / ".tmp-abrupt-cut"
        temporary.write_bytes(b"partial atomic write")
        temporary.chmod(0o600)
        before = self._evidence_snapshot(self.campaign_dir)

        first = inspect_synthetic_campaign(self.campaign_id, self.evidence_base)
        second = inspect_synthetic_campaign(self.campaign_id, self.evidence_base)

        self.assertEqual(first.inspection_status, "INCOMPLETE")
        self.assertEqual(first.verdict, "NOT_DEMONSTRATED")
        self.assertTrue(any("temporary file remains" in issue for issue in first.issues))
        self.assertEqual(second.to_dict(), first.to_dict())
        self.assertEqual(self._evidence_snapshot(self.campaign_dir), before)

    def test_completed_campaign_is_valid_and_read_only(self):
        before = {
            path.name: (
                path.read_bytes(),
                path.stat().st_mode,
                path.stat().st_mtime_ns,
            )
            for path in self.campaign_dir.iterdir()
        }
        directory_mtime = self.campaign_dir.stat().st_mtime_ns

        result = inspect_synthetic_campaign(self.campaign_id, self.evidence_base)

        self.assertEqual(result.inspection_status, "COMPLETED_VALID")
        self.assertEqual(result.verdict, "DEMONSTRATED")
        self.assertEqual(result.samples_verified, 5)
        self.assertEqual(result.issues, [])
        after = {
            path.name: (
                path.read_bytes(),
                path.stat().st_mode,
                path.stat().st_mtime_ns,
            )
            for path in self.campaign_dir.iterdir()
        }
        self.assertEqual(after, before)
        self.assertEqual(self.campaign_dir.stat().st_mtime_ns, directory_mtime)

    def test_inspector_never_executes_a_workload_or_starts_a_process(self):
        with (
            patch.object(SyntheticWorkloadExecutor, "run") as workload,
            patch("core.executor.subprocess.Popen") as popen,
        ):
            result = inspect_synthetic_campaign(
                self.campaign_id, self.evidence_base
            )
        self.assertEqual(result.inspection_status, "COMPLETED_VALID")
        workload.assert_not_called()
        popen.assert_not_called()

    def test_running_campaign_is_active_or_unknown_not_demonstrated(self):
        campaign_path = self.campaign_dir / "campaign.json"
        campaign = json.loads(campaign_path.read_text())
        campaign["status"] = "RUNNING"
        campaign["verdict"] = "NOT_DEMONSTRATED"
        campaign_path.write_bytes(self._json_bytes(campaign))

        result = inspect_synthetic_campaign(self.campaign_id, self.evidence_base)

        self.assertEqual(result.inspection_status, "ACTIVE_OR_UNKNOWN")
        self.assertEqual(result.verdict, "NOT_DEMONSTRATED")

    def test_historical_completed_campaign_is_readable_but_not_upgraded(self):
        self._write_campaign(modern=False)

        result = inspect_synthetic_campaign(self.campaign_id, self.evidence_base)

        self.assertEqual(result.inspection_status, "INCOMPLETE")
        self.assertEqual(result.verdict, "NOT_DEMONSTRATED")
        self.assertEqual(result.samples_verified, 5)
        self.assertTrue(any("no checkpoint" in issue for issue in result.issues))
        self.assertTrue(any("authorization" in issue for issue in result.issues))

    def test_missing_receipt_is_not_demonstrated(self):
        (self.campaign_dir / "sample-05.receipt.json").unlink()
        result = inspect_synthetic_campaign(self.campaign_id, self.evidence_base)
        self.assertEqual(result.inspection_status, "INCOMPLETE")
        self.assertEqual(result.verdict, "NOT_DEMONSTRATED")
        self.assertTrue(any("missing" in issue.lower() for issue in result.issues))

    def test_digest_mismatch_is_not_demonstrated(self):
        with (self.campaign_dir / "sample-01.receipt.json").open("ab") as output:
            output.write(b" ")
        result = inspect_synthetic_campaign(self.campaign_id, self.evidence_base)
        self.assertEqual(result.inspection_status, "INCOMPLETE")
        self.assertTrue(any("SHA-256 mismatch" in issue for issue in result.issues))

    def test_receipt_contract_failure_is_detected_even_with_matching_manifest(self):
        path = self.campaign_dir / "sample-01.receipt.json"
        receipt = json.loads(path.read_text())
        receipt["output_sha256"] = "0" * 64
        path.write_bytes(self._json_bytes(receipt))
        path.chmod(0o600)
        self._refresh_manifest()

        result = inspect_synthetic_campaign(self.campaign_id, self.evidence_base)

        self.assertEqual(result.inspection_status, "INCOMPLETE")
        self.assertEqual(result.samples_verified, 4)
        self.assertTrue(any("Sample 1" in issue for issue in result.issues))
        self.assertFalse(any("SHA-256 mismatch" in issue for issue in result.issues))

    def test_unrepresentably_large_receipt_duration_fails_closed(self):
        path = self.campaign_dir / "sample-01.receipt.json"
        receipt = json.loads(path.read_text())
        receipt["elapsed_seconds"] = 10**400
        path.write_bytes(self._json_bytes(receipt))
        path.chmod(0o600)
        self._refresh_manifest()

        result = inspect_synthetic_campaign(self.campaign_id, self.evidence_base)

        self.assertEqual(result.inspection_status, "INCOMPLETE")
        self.assertEqual(result.verdict, "NOT_DEMONSTRATED")

    def test_campaign_record_identifier_must_match_directory(self):
        path = self.campaign_dir / "campaign.json"
        campaign = json.loads(path.read_text())
        campaign["campaign_id"] = "f" * 32
        path.write_bytes(self._json_bytes(campaign))
        path.chmod(0o600)
        self._refresh_manifest()

        result = inspect_synthetic_campaign(self.campaign_id, self.evidence_base)

        self.assertEqual(result.inspection_status, "INCOMPLETE")
        self.assertEqual(result.verdict, "NOT_DEMONSTRATED")
        self.assertTrue(any("identifier" in issue for issue in result.issues))

    def test_excessively_nested_campaign_json_fails_closed(self):
        path = self.campaign_dir / "campaign.json"
        path.write_bytes(b'{"nested":' + b"[" * 1500 + b"0" + b"]" * 1500 + b"}")
        path.chmod(0o600)
        self._refresh_manifest()

        result = inspect_synthetic_campaign(self.campaign_id, self.evidence_base)

        self.assertEqual(result.inspection_status, "INCOMPLETE")
        self.assertEqual(result.verdict, "NOT_DEMONSTRATED")

    def test_symlinked_campaign_directory_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir(mode=0o700)
        linked_base = self.root / "linked-state"
        linked_base.mkdir(mode=0o700)
        (linked_base / self.campaign_id).symlink_to(outside, target_is_directory=True)
        base = linked_base

        result = inspect_synthetic_campaign(self.campaign_id, base)

        self.assertEqual(result.inspection_status, "INCOMPLETE")
        self.assertEqual(result.verdict, "NOT_DEMONSTRATED")
        self.assertEqual(list(outside.iterdir()), [])

    def test_symlinked_evidence_base_component_is_rejected(self):
        linked_root = self.root / "linked-root"
        linked_root.symlink_to(self.root, target_is_directory=True)
        linked_base = linked_root / "state" / "ihra" / "synthetic-experiments"

        result = inspect_synthetic_campaign(self.campaign_id, linked_base)

        self.assertEqual(result.inspection_status, "INCOMPLETE")
        self.assertEqual(result.verdict, "NOT_DEMONSTRATED")

    def test_symlinked_campaign_file_is_rejected(self):
        target = self.root / "external.json"
        target.write_bytes((self.campaign_dir / "campaign.json").read_bytes())
        (self.campaign_dir / "campaign.json").unlink()
        (self.campaign_dir / "campaign.json").symlink_to(target)
        result = inspect_synthetic_campaign(self.campaign_id, self.evidence_base)
        self.assertEqual(result.inspection_status, "INCOMPLETE")
        self.assertEqual(result.verdict, "NOT_DEMONSTRATED")

    def test_invalid_campaign_identifier_cannot_escape_state_directory(self):
        result = inspect_synthetic_campaign("../outside", self.evidence_base)
        self.assertEqual(result.inspection_status, "INCOMPLETE")
        self.assertIn("Invalid campaign identifier", result.reason)

    def test_manifest_symlink_fails_closed(self):
        manifest = self.campaign_dir / "SHA256SUMS.txt"
        external = self.root / "manifest.txt"
        external.write_bytes(manifest.read_bytes())
        manifest.unlink()
        manifest.symlink_to(external)
        result = inspect_synthetic_campaign(self.campaign_id, self.evidence_base)
        self.assertEqual(result.inspection_status, "INCOMPLETE")

    def test_duplicate_manifest_entries_fail_closed(self):
        manifest = self.campaign_dir / "SHA256SUMS.txt"
        line = manifest.read_text().splitlines()[0]
        with manifest.open("a", encoding="ascii") as output:
            output.write(line + "\n")
        result = inspect_synthetic_campaign(self.campaign_id, self.evidence_base)
        self.assertEqual(result.inspection_status, "INCOMPLETE")
        self.assertTrue(any("Duplicate manifest entry" in issue for issue in result.issues))

    def test_cli_inspector_does_not_construct_campaign_runner(self):
        output = StringIO()
        with (
            patch("core.cli.inspect_synthetic_campaign", return_value=type(
                "Result", (), {
                    "to_dict": lambda self: {"inspection_status": "COMPLETED_VALID"},
                    "inspection_status": "COMPLETED_VALID",
                }
            )()) as inspector,
            patch("core.cli.SyntheticExperimentRunner") as runner,
            redirect_stdout(output),
        ):
            cmd_inspect_synthetic_campaign(SimpleNamespace(campaign_id=self.campaign_id))
        inspector.assert_called_once_with(self.campaign_id)
        runner.assert_not_called()
        self.assertEqual(
            json.loads(output.getvalue())["inspection_status"], "COMPLETED_VALID"
        )


if __name__ == "__main__":
    unittest.main()
