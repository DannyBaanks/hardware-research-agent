import json
import unittest
from unittest.mock import Mock, patch

from core.executor import SandboxUnavailableError, SyntheticWorkloadExecutor


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
        self.assertEqual(command[-1], "/app/workload_runner.py")

    def test_rejects_mismatched_hash(self):
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

    def test_rejects_unsupported_workload_id(self):
        with self.assertRaisesRegex(ValueError, "Unsupported workload"):
            self.executor.run("user-provided-command")

    def test_reports_sandbox_timeout_as_failure(self):
        binary_patch, popen_patch, collect_patch = self._mock_process(
            output=b"", failure="Workload exceeded wall-clock timeout"
        )
        with binary_patch, popen_patch, collect_patch:
            with patch("core.executor.time.monotonic", side_effect=[10.0, 18.0]):
                receipt = self.executor.run(self.executor.WORKLOAD_ID)

        self.assertEqual(receipt.status, "FAILED")
        self.assertIn("timeout", receipt.reason)

    def test_fails_closed_when_runner_differs_from_reviewed_version(self):
        binaries = ("/usr/bin/bwrap", "/usr/bin/python3")
        with (
            patch.object(self.executor, "_sandbox_binaries", return_value=binaries),
            patch.object(self.executor, "runner_path") as runner_path,
            patch("core.executor.subprocess.Popen") as popen,
        ):
            runner_path.is_file.return_value = True
            runner_path.read_bytes.return_value = b"unreviewed runner"
            receipt = self.executor.run(self.executor.WORKLOAD_ID)

        self.assertEqual(receipt.status, "FAILED")
        self.assertIn("does not match the reviewed version", receipt.reason)
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
