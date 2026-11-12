import json
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.executor import SandboxUnavailableError, SyntheticWorkloadExecutor
from tests.fake_host import MEMFD_SEALS_AVAILABLE, skip_or_fail


class TestSyntheticWorkloadExecutor(unittest.TestCase):
    def setUp(self):
        self.executor = SyntheticWorkloadExecutor()
        self.output = json.dumps(
            {
                "workload_id": self.executor.WORKLOAD_ID,
                "iterations": 20000,
                "output_sha256": self.executor.EXPECTED_OUTPUT_SHA256,
                "elapsed_seconds": 0.01,
            }
        ).encode()
        self.process = Mock(returncode=0)

    def _require_sealing(self):
        # These tests fake the sandbox process but seal the real runner bytes.
        if not MEMFD_SEALS_AVAILABLE:
            skip_or_fail(self, "this Python build exposes no memfd sealing constants")

    def _mock_process(self, output=None, failure=None):
        return (
            patch.object(self.executor, "_sandbox_binaries", return_value=("/usr/bin/bwrap", "/usr/bin/python3")),
            patch("core.executor.subprocess.Popen", return_value=self.process),
            patch.object(
                self.executor,
                "_collect_output",
                return_value=(self.output if output is None else output, b"", failure),
            ),
        )

    def test_runs_only_the_fixed_workload_and_verifies_parent_digest(self):
        self._require_sealing()
        binary_patch, popen_patch, collect_patch = self._mock_process()
        with binary_patch, popen_patch as popen, collect_patch:
            with patch("core.executor.time.monotonic", side_effect=[10.0, 10.25]):
                receipt = self.executor.run(self.executor.WORKLOAD_ID)

        self.assertEqual(receipt.status, "COMPLETED")
        self.assertEqual(receipt.output_sha256, self.executor.EXPECTED_OUTPUT_SHA256)
        self.assertEqual(receipt.elapsed_seconds, 0.25)
        self.assertEqual(receipt.sandboxed_processes_started, 1)
        self.assertEqual(receipt.hardware_telemetry_reads, 0)
        self.assertEqual(receipt.device_control_operations, 0)
        self.assertFalse(receipt.persistent_host_state_changed)
        command = popen.call_args.args[0]
        self.assertIn("--unshare-all", command)
        self.assertIn("--clearenv", command)
        self.assertIn("--ro-bind-data", command)
        self.assertEqual(command[-1], "/app/workload_runner.py")
        self.assertEqual(len(popen.call_args.kwargs["pass_fds"]), 1)

    def test_rejects_mismatched_hash(self):
        self._require_sealing()
        result = json.loads(self.output)
        result["output_sha256"] = "0" * 64
        binary_patch, popen_patch, collect_patch = self._mock_process(
            output=json.dumps(result).encode()
        )
        with binary_patch, popen_patch, collect_patch:
            with patch("core.executor.time.monotonic", side_effect=[10.0, 10.25]):
                receipt = self.executor.run(self.executor.WORKLOAD_ID)

        self.assertEqual(receipt.status, "FAILED")
        self.assertIsNone(receipt.output_sha256)
        self.assertIn("fixed contract", receipt.reason)

    def test_rejects_nonfinite_workload_elapsed_time(self):
        self._require_sealing()
        result = json.loads(self.output)
        result["elapsed_seconds"] = float("nan")
        binary_patch, popen_patch, collect_patch = self._mock_process(
            output=json.dumps(result).encode()
        )
        with binary_patch, popen_patch, collect_patch:
            with patch("core.executor.time.monotonic", side_effect=[10.0, 10.25]):
                receipt = self.executor.run(self.executor.WORKLOAD_ID)

        self.assertEqual(receipt.status, "FAILED")
        self.assertIsNone(receipt.output_sha256)

    def test_rejects_unreasonably_large_elapsed_integer(self):
        self._require_sealing()
        result = json.loads(self.output)
        result["elapsed_seconds"] = 10 ** 1000
        binary_patch, popen_patch, collect_patch = self._mock_process(
            output=json.dumps(result).encode()
        )
        with binary_patch, popen_patch, collect_patch:
            with patch("core.executor.time.monotonic", side_effect=[10.0, 10.25]):
                receipt = self.executor.run(self.executor.WORKLOAD_ID)

        self.assertEqual(receipt.status, "FAILED")
        self.assertIsNone(receipt.output_sha256)

    def test_denies_execution_when_sandbox_is_unavailable(self):
        with patch.object(
            self.executor,
            "_sandbox_binaries",
            side_effect=SandboxUnavailableError("bubblewrap unavailable"),
        ), patch("core.executor.subprocess.Popen") as popen:
            receipt = self.executor.run(self.executor.WORKLOAD_ID)

        self.assertEqual(receipt.status, "DENIED")
        self.assertEqual(receipt.sandboxed_processes_started, 0)
        popen.assert_not_called()

    def test_missing_sealing_support_is_a_failed_receipt_not_a_crash(self):
        binaries = ("/usr/bin/bwrap", "/usr/bin/python3")
        with (
            patch.object(self.executor, "_sandbox_binaries", return_value=binaries),
            patch("core.executor.fcntl", SimpleNamespace()),
            patch("core.executor.subprocess.Popen") as popen,
        ):
            receipt = self.executor.run(self.executor.WORKLOAD_ID)

        self.assertEqual(receipt.status, "FAILED")
        self.assertIn("Sealed in-memory runner files are required", receipt.reason)
        self.assertEqual(receipt.sandboxed_processes_started, 0)
        popen.assert_not_called()

    def test_rejects_unsupported_workload_id(self):
        with self.assertRaisesRegex(ValueError, "Unsupported workload"):
            self.executor.run("user-provided-command")

    def test_rejects_setuid_sandbox_binary(self):
        executable = Path("/trusted/bwrap")
        metadata = SimpleNamespace(
            st_uid=0,
            st_mode=stat.S_IFREG | stat.S_ISUID | 0o755,
        )
        with patch.object(Path, "is_file", return_value=True), \
             patch.object(Path, "resolve", return_value=executable), \
             patch.object(Path, "stat", return_value=metadata), \
             patch("core.executor.os.access", return_value=True):
            trusted = self.executor._trusted_executable(("/usr/bin/bwrap",))

        self.assertIsNone(trusted)

    def test_reports_sandbox_timeout_as_failure(self):
        self._require_sealing()
        binary_patch, popen_patch, collect_patch = self._mock_process(
            output=b"", failure="Workload exceeded wall-clock timeout"
        )
        with binary_patch, popen_patch, collect_patch:
            with patch("core.executor.time.monotonic", side_effect=[10.0, 18.0]):
                receipt = self.executor.run(self.executor.WORKLOAD_ID)

        self.assertEqual(receipt.status, "FAILED")
        self.assertIn("timeout", receipt.reason)

    def test_interrupt_terminates_the_process_group(self):
        self._require_sealing()
        self.process.pid = 12345
        binary_patch, popen_patch, _ = self._mock_process()
        with binary_patch, popen_patch, \
             patch.object(self.executor, "_collect_output", side_effect=KeyboardInterrupt()), \
             patch("core.executor.os.killpg") as killpg:
            with self.assertRaises(KeyboardInterrupt):
                self.executor.run(self.executor.WORKLOAD_ID)

        killpg.assert_called_once()
        self.assertEqual(killpg.call_args.args[0], self.process.pid)
        self.process.wait.assert_called()

    def test_fails_closed_when_runner_differs_from_reviewed_version(self):
        binaries = ("/usr/bin/bwrap", "/usr/bin/python3")
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        altered_runner = Path(temp.name) / "altered-workload-runner.py"
        altered_runner.write_bytes(b"unreviewed runner")
        with (
            patch.object(self.executor, "_sandbox_binaries", return_value=binaries),
            patch.object(self.executor, "runner_path", altered_runner),
            patch("core.executor.subprocess.Popen") as popen,
        ):
            receipt = self.executor.run(self.executor.WORKLOAD_ID)

        self.assertEqual(receipt.status, "FAILED")
        self.assertIn("does not match the reviewed version", receipt.reason)
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
