import hashlib
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from core.cli import cmd_inspect_synthetic_campaign
from core.experiment import SyntheticExperimentPlan
from core.executor import SyntheticWorkloadExecutor
from core.planning import SyntheticExperimentProposal
from core.recovery import inspect_synthetic_campaign


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
        files = {
            "plan.json": plan_bytes,
            "campaign.json": self._json_bytes(
                {
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
            ),
        }
        if modern:
            files["proposal.json"] = proposal_bytes
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
