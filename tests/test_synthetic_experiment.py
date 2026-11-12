import hashlib
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core.experiment import (
    _CampaignEvidence,
    PlanValidationError,
    SyntheticExperimentPlan,
    SyntheticExperimentRunner,
)
from core.executor import SandboxUnavailableError, SyntheticWorkloadExecutor, WorkloadReceipt
from core.planning import SyntheticExperimentProposal

from tests.fake_host import MEMFD_SEALS_AVAILABLE, hermetic_contract, skip_or_fail
from tests.tty_test_utils import authorize_in_pty


ADVERSARIAL_CASES = {
    "NC-1": {
        "condition": "Runner bytes differ from the pinned SHA-256",
        "expected": "DENY before any sample starts",
        "evidence": "Campaign runner integrity check and zero executor calls",
        "verdict": "DENY",
    },
    "NC-2": {
        "condition": "Exit-zero receipt reports a non-oracle result digest",
        "expected": "Abort after the corrupt sample",
        "evidence": "Failed receipt is retained and later samples are absent",
        "verdict": "REFUTED",
    },
    "NC-3": {
        "condition": "Executor reports timeout / nonzero process exit",
        "expected": "Abort with no retry",
        "evidence": "Timeout receipt and one executor invocation",
        "verdict": "REFUTED",
    },
    "NC-4": {
        "condition": "Bubblewrap is unavailable",
        "expected": "DENY without fallback",
        "evidence": "Campaign records DENIED and zero samples",
        "verdict": "DENY",
    },
    "NC-5": {
        "condition": "Plan object is mutated during an authorized campaign",
        "expected": "Abort before the next sample",
        "evidence": "One receipt is retained; second sample never starts",
        "verdict": "REFUTED",
    },
    "NC-6": {
        "condition": "Child result omits the required iterations field",
        "expected": "Executor rejects output despite exit code zero",
        "evidence": "Failed parent receipt is preserved and the campaign stops",
        "verdict": "REFUTED",
    },
    "NC-7": {
        "condition": "Third sample returns a bad digest",
        "expected": "Samples four and five are not executed",
        "evidence": "Two valid and one failed receipt; exactly three calls",
        "verdict": "REFUTED",
    },
    "NC-8": {
        "condition": "Evidence destination is a symlink",
        "expected": "Reject destination before workload execution",
        "evidence": "ABORTED / NOT_DEMONSTRATED, zero executor calls, target empty",
        "verdict": "NOT_DEMONSTRATED",
    },
    "NC-9": {
        "condition": "PATH contains an impostor Bubblewrap executable",
        "expected": "Use only fixed absolute trusted executable paths",
        "evidence": "Resolved sandbox binaries are independent of PATH",
        "verdict": "DEMONSTRATED",
    },
    "NC-10": {
        "condition": "Campaign is interrupted during a sample",
        "expected": "Persist an interrupted/aborted state, never complete",
        "evidence": "Interrupted marker and incomplete campaign record",
        "verdict": "DEMONSTRATED",
    },
}


class FakeExecutor:
    def __init__(self, outcomes=None, runner_path=None):
        self.runner_path = runner_path or (
            Path(__file__).resolve().parents[1] / "core" / "workload_runner.py"
        )
        self.outcomes = list(outcomes or [])
        self.calls = 0

    def _sandbox_binaries(self):
        raise AssertionError("Tests replace the campaign binary verifier")

    def run(self, workload_id):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if callable(outcome):
            return outcome(workload_id)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class TestSyntheticExperimentPlan(unittest.TestCase):
    def test_fixed_plan_is_canonical_and_rejects_unknown_fields(self):
        plan = SyntheticExperimentPlan.fixed_repeatability_plan()
        self.assertEqual(len(plan.sha256), 64)
        self.assertEqual(plan.samples, 5)
        serialized = plan.to_dict()
        self.assertEqual(SyntheticExperimentPlan.from_dict(serialized).sha256, plan.sha256)
        serialized["shell"] = "not allowed"
        with self.assertRaisesRegex(PlanValidationError, "unknown fields"):
            SyntheticExperimentPlan.from_dict(serialized)

    def test_plan_rejects_mutable_authority_values(self):
        plan = SyntheticExperimentPlan.fixed_repeatability_plan()
        object.__setattr__(plan, "samples", 6)
        with self.assertRaisesRegex(PlanValidationError, "samples"):
            plan.validate()


class TestSyntheticExperimentRunner(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.temp_path = Path(self.temp.name)
        os.chmod(self.temp_path, 0o700)
        self.evidence_base = self.temp_path / "xdg" / "ihra" / "synthetic-experiments"
        self.plan = SyntheticExperimentPlan.fixed_repeatability_plan()

    def test_manifest_is_incremental_and_never_hashes_itself(self):
        repository_root = Path(__file__).resolve().parents[1]
        store = _CampaignEvidence(
            self.evidence_base, "campaign-manifest-test", repository_root
        )
        self.addCleanup(store.close)

        store.write_bytes("plan.json", b'{"plan":1}\n')
        first_manifest = (store.path / "SHA256SUMS.txt").read_text()
        self.assertIn("  plan.json", first_manifest)
        self.assertNotIn("SHA256SUMS.txt", first_manifest)

        store.write_bytes("proposal.json", b'{"proposal":1}\n')
        second_manifest = (store.path / "SHA256SUMS.txt").read_text()
        self.assertIn("  plan.json", second_manifest)
        self.assertIn("  proposal.json", second_manifest)
        self.assertNotIn("SHA256SUMS.txt", second_manifest)
        self.assertNotIn(hashlib.sha256(first_manifest.encode()).hexdigest(), second_manifest)

    def test_manifest_failure_preserves_prior_artifacts_without_claiming_checkpoint(self):
        repository_root = Path(__file__).resolve().parents[1]
        store = _CampaignEvidence(
            self.evidence_base, "campaign-manifest-failure-test", repository_root
        )
        self.addCleanup(store.close)
        store.write_bytes("sample-01.receipt.json", b'{"validation":"PASS"}\n')
        previous_manifest = (store.path / "SHA256SUMS.txt").read_bytes()

        write_atomic = store._write_atomic

        def fail_manifest_update(name, data):
            if name == "SHA256SUMS.txt":
                raise OSError("injected manifest update failure")
            return write_atomic(name, data)

        with patch.object(store, "_write_atomic", side_effect=fail_manifest_update):
            with self.assertRaisesRegex(OSError, "manifest update failure"):
                store.write_checkpoint(
                    "campaign-manifest-failure-test",
                    self.plan.sha256,
                    "SAMPLE_RECORDED",
                    1,
                    1,
                )

        current_manifest = (store.path / "SHA256SUMS.txt").read_bytes()
        self.assertEqual(current_manifest, previous_manifest)
        receipt = store.path / "sample-01.receipt.json"
        receipt_digest = hashlib.sha256(receipt.read_bytes()).hexdigest()
        self.assertIn(f"{receipt_digest}  sample-01.receipt.json", current_manifest.decode())
        self.assertTrue((store.path / "checkpoint.json").is_file())
        self.assertNotIn("checkpoint.json", current_manifest.decode())

    def _receipt(self, **overrides):
        fields = {
            "workload_id": self.plan.workload_id,
            "status": "COMPLETED",
            "output_sha256": self.plan.expected_result_digest,
            "elapsed_seconds": 0.025,
            "sandbox": "bubblewrap-rootless",
            "hardware_telemetry_reads": 0,
            "device_control_operations": 0,
            "persistent_host_state_changed": False,
            "sandboxed_processes_started": 1,
            "reason": "fixed workload completed",
            "runner_sha256": self.plan.runner_digest,
            "process_exit_code": 0,
        }
        fields.update(overrides)
        return WorkloadReceipt(**fields)

    def _runner(self, executor, binary_check=None):
        runner = SyntheticExperimentRunner(
            executor=executor,
            evidence_base=self.evidence_base,
            repository_root=Path(__file__).resolve().parents[1],
            rollback_contract=hermetic_contract(self.temp_path),
            fault_base=self.temp_path / "xdg" / "ihra" / "tier1-faults",
        )
        runner._verify_sandbox_binaries = Mock(
            side_effect=binary_check
            if isinstance(binary_check, BaseException)
            else None,
            return_value={
                "bubblewrap_path": "/usr/bin/bwrap",
                "bubblewrap_sha256": "a" * 64,
                "python_path": "/usr/bin/python3",
                "python_sha256": "b" * 64,
            },
        )
        return runner

    def _run(self, executor, digest=None, binary_check=None, plan=None):
        runner = self._runner(executor, binary_check)
        plan = plan or self.plan
        proposal = SyntheticExperimentProposal(
            schema_version=1,
            proposal_id="test-fixed-proposal",
            rationale="Authorize the fixed repeatability plan in a PTY test.",
            plan=plan,
        )
        authorization = authorize_in_pty(
            digest or plan.sha256, proposal.sha256, proposal.canonical_bytes().decode("ascii")
        )
        return runner.run(
            plan,
            authorization=authorization,
            proposal_bytes=proposal.canonical_bytes(),
        )

    def _campaign_files(self, result):
        return {path.name: path for path in Path(result.evidence_dir).iterdir()}

    def test_runner_api_denies_without_a_tty_authorization(self):
        executor = FakeExecutor([self._receipt()])
        result = self._runner(executor).run(self.plan)
        self.assertEqual(result.status, "DENIED")
        self.assertEqual(result.verdict, "DENY")
        self.assertEqual(executor.calls, 0)
        self.assertFalse(self.evidence_base.exists())

    def test_runner_rejects_authorization_subclass_override(self):
        class ForgedAuthorization:
            def consume(self, plan_sha256, proposal_sha256):
                return proposal_sha256

        executor = FakeExecutor([self._receipt()])
        result = self._runner(executor).run(
            self.plan,
            authorization=ForgedAuthorization(),
            proposal_bytes=b"{}",
        )
        self.assertEqual(result.status, "DENIED")
        self.assertEqual(executor.calls, 0)

    def test_runner_denies_when_proposal_digest_differs_from_tty_consent(self):
        executor = FakeExecutor([self._receipt()])
        runner = self._runner(executor)
        proposal = SyntheticExperimentProposal(
            schema_version=1,
            proposal_id="test-fixed-proposal",
            rationale="Authorize the fixed repeatability plan in a PTY test.",
            plan=self.plan,
        )
        authorization = authorize_in_pty(
            self.plan.sha256, proposal.sha256, proposal.canonical_bytes().decode("ascii")
        )
        result = runner.run(
            self.plan,
            authorization=authorization,
            proposal_bytes=SyntheticExperimentProposal(
                schema_version=1,
                proposal_id="test-fixed-proposal",
                rationale="Changed proposal after TTY authorization.",
                plan=self.plan,
            ).canonical_bytes(),
        )
        self.assertEqual(result.status, "DENIED")
        self.assertIn("proposal digest", result.reason)
        self.assertEqual(executor.calls, 0)

    def test_plan_changed_after_tty_consent_is_denied_before_execution(self):
        executor = FakeExecutor([self._receipt()])
        runner = self._runner(executor)
        proposal = SyntheticExperimentProposal(
            schema_version=1,
            proposal_id="test-fixed-proposal",
            rationale="Authorize the fixed repeatability plan in a PTY test.",
            plan=self.plan,
        )
        authorization = authorize_in_pty(
            self.plan.sha256, proposal.sha256, proposal.canonical_bytes().decode("ascii")
        )
        mutated_plan = SyntheticExperimentPlan.fixed_repeatability_plan()
        object.__setattr__(mutated_plan, "samples", 4)
        result = runner.run(
            mutated_plan,
            authorization=authorization,
            proposal_bytes=proposal.canonical_bytes(),
        )
        self.assertEqual(result.status, "DENIED")
        self.assertEqual(executor.calls, 0)

    def test_runs_five_samples_and_writes_private_hashed_evidence(self):
        executor = FakeExecutor([self._receipt() for _ in range(5)])
        result = self._run(executor)

        self.assertEqual(result.status, "COMPLETED")
        self.assertEqual(result.verdict, "DEMONSTRATED")
        self.assertEqual(result.samples_completed, 5)
        self.assertEqual(executor.calls, 5)
        evidence = Path(result.evidence_dir)
        self.assertNotEqual(evidence, Path(__file__).resolve().parents[1])
        self.assertNotIn(Path(__file__).resolve().parents[1], evidence.parents)
        self.assertEqual(stat.S_IMODE(evidence.stat().st_mode), 0o700)
        campaign = json.loads((evidence / "campaign.json").read_text())
        self.assertEqual(campaign["status"], "COMPLETED")
        self.assertEqual(
            campaign["authorization"]["proposal_sha256"],
            SyntheticExperimentProposal(
                schema_version=1,
                proposal_id="test-fixed-proposal",
                rationale="Authorize the fixed repeatability plan in a PTY test.",
                plan=self.plan,
            ).sha256,
        )
        self.assertFalse(campaign["performance_claim"])
        manifest = (evidence / "SHA256SUMS.txt").read_text().splitlines()
        checkpoint = json.loads((evidence / "checkpoint.json").read_text())
        self.assertEqual(checkpoint["state"], "TERMINAL")
        self.assertEqual(checkpoint["samples_completed"], 5)
        self.assertIsNone(checkpoint["current_sample"])
        self.assertEqual(
            hashlib.sha256((evidence / "plan.json").read_bytes()).hexdigest(),
            result.plan_sha256,
        )
        self.assertEqual(
            hashlib.sha256((evidence / "proposal.json").read_bytes()).hexdigest(),
            campaign["authorization"]["proposal_sha256"],
        )
        self.assertIn(
            f"{hashlib.sha256((evidence / 'plan.json').read_bytes()).hexdigest()}  plan.json",
            manifest,
        )
        self.assertNotIn("SHA256SUMS.txt", "\n".join(manifest))
        self.assertIn("  checkpoint.json", "\n".join(manifest))
        self.assertEqual(len([line for line in manifest if "sample-" in line]), 10)
        manifest_entries = {}
        for line in manifest:
            digest, name = line.split("  ", 1)
            manifest_entries[name] = digest
        expected_files = {
            path.name for path in evidence.iterdir() if path.name != "SHA256SUMS.txt"
        }
        self.assertEqual(set(manifest_entries), expected_files)
        for name, digest in manifest_entries.items():
            self.assertEqual(
                hashlib.sha256((evidence / name).read_bytes()).hexdigest(), digest
            )

    def test_running_checkpoint_is_durable_before_executor_dispatch(self):
        observed = []

        def verify_checkpoint_before_dispatch(_workload_id):
            campaign_dir = next(self.evidence_base.iterdir())
            checkpoint = json.loads((campaign_dir / "checkpoint.json").read_text())
            manifest = (campaign_dir / "SHA256SUMS.txt").read_text()
            self.assertEqual(checkpoint["state"], "SAMPLE_RUNNING")
            self.assertEqual(checkpoint["current_sample"], 1)
            self.assertEqual(checkpoint["samples_completed"], 0)
            self.assertIn("  sample-01.started.json", manifest)
            self.assertIn("  checkpoint.json", manifest)
            observed.append(True)
            return self._receipt()

        executor = FakeExecutor(
            [verify_checkpoint_before_dispatch] + [self._receipt() for _ in range(4)]
        )
        result = self._run(executor)
        self.assertEqual(result.status, "COMPLETED")
        self.assertEqual(observed, [True])

    def test_authorization_must_match_exact_plan_digest(self):
        executor = FakeExecutor([self._receipt() for _ in range(5)])
        result = self._run(executor, digest="0" * 64)
        self.assertEqual(result.verdict, "DENY")
        self.assertEqual(executor.calls, 0)
        self.assertFalse(self.evidence_base.exists())

    def test_nc1_runner_tampering_is_denied_before_execution(self):
        copy = self.temp_path / "workload_runner.py"
        copy.write_bytes(b"tampered runner")
        executor = FakeExecutor([self._receipt()], runner_path=copy)
        result = self._run(executor)
        self.assertEqual(ADVERSARIAL_CASES["NC-1"]["verdict"], result.verdict)
        self.assertEqual(executor.calls, 0)

    def test_nc2_exit_zero_with_wrong_digest_aborts_and_preserves_receipt(self):
        # The sandbox process is faked, but the real runner bytes are sealed.
        if not MEMFD_SEALS_AVAILABLE:
            skip_or_fail(self, "this Python build exposes no memfd sealing constants")
        executor = SyntheticWorkloadExecutor()
        child_result = {
            "workload_id": self.plan.workload_id,
            "iterations": 20000,
            "output_sha256": "0" * 64,
            "elapsed_seconds": 0.02,
        }
        with (
            patch.object(executor, "_sandbox_binaries", return_value=("/usr/bin/bwrap", "/usr/bin/python3")),
            patch("core.executor.subprocess.Popen", return_value=Mock(returncode=0)),
            patch.object(
                executor,
                "_collect_output",
                return_value=(json.dumps(child_result).encode(), b"", None),
            ),
        ):
            result = self._run(executor)
        self.assertEqual(result.verdict, ADVERSARIAL_CASES["NC-2"]["verdict"])
        files = self._campaign_files(result)
        receipt = json.loads(files["sample-01.receipt.json"].read_text())
        self.assertEqual(receipt["process_exit_code"], 0)
        self.assertEqual(receipt["validation"], "FAIL")
        self.assertNotIn("sample-02.started.json", files)

    def test_nc3_timeout_aborts_without_retry(self):
        executor = FakeExecutor(
            [self._receipt(status="FAILED", output_sha256=None, process_exit_code=-9), self._receipt()]
        )
        result = self._run(executor)
        self.assertEqual(result.verdict, ADVERSARIAL_CASES["NC-3"]["verdict"])
        self.assertEqual(executor.calls, 1)

    def test_nc4_sandbox_absence_is_denied_and_recorded_without_fallback(self):
        executor = FakeExecutor([self._receipt()])
        result = self._run(
            executor, binary_check=SandboxUnavailableError("bubblewrap missing")
        )
        self.assertEqual(result.verdict, ADVERSARIAL_CASES["NC-4"]["verdict"])
        self.assertEqual(result.status, "DENIED")
        self.assertEqual(executor.calls, 0)
        campaign = json.loads((Path(result.evidence_dir) / "campaign.json").read_text())
        self.assertIn("SANDBOX_UNAVAILABLE", campaign["reason"])

    def test_nc5_plan_mutation_stops_before_next_sample(self):
        plan = self.plan

        def mutate_after_first_sample(_):
            object.__setattr__(plan, "samples", 4)
            return self._receipt()

        executor = FakeExecutor([mutate_after_first_sample, self._receipt()])
        result = self._run(executor, plan=plan)
        self.assertEqual(result.status, "ABORTED")
        self.assertEqual(executor.calls, 1)
        self.assertIn("Plan changed", result.reason)
        object.__setattr__(plan, "samples", 5)

    def test_nc6_incomplete_receipt_is_rejected(self):
        # The sandbox process is faked, but the real runner bytes are sealed.
        if not MEMFD_SEALS_AVAILABLE:
            skip_or_fail(self, "this Python build exposes no memfd sealing constants")
        executor = SyntheticWorkloadExecutor()
        malformed_result = {
            "workload_id": self.plan.workload_id,
            "output_sha256": self.plan.expected_result_digest,
            "elapsed_seconds": 0.02,
        }
        with (
            patch.object(executor, "_sandbox_binaries", return_value=("/usr/bin/bwrap", "/usr/bin/python3")),
            patch("core.executor.subprocess.Popen", return_value=Mock(returncode=0)),
            patch.object(
                executor,
                "_collect_output",
                return_value=(json.dumps(malformed_result).encode(), b"", None),
            ),
        ):
            result = self._run(executor)
        receipt = json.loads(
            (Path(result.evidence_dir) / "sample-01.receipt.json").read_text()
        )
        self.assertEqual(receipt["process_exit_code"], 0)
        self.assertEqual(receipt["status"], "FAILED")
        self.assertIsNone(receipt["output_sha256"])
        self.assertIn("fixed contract", receipt["reason"])
        self.assertEqual(receipt["validation"], "FAIL")
        self.assertEqual(result.verdict, ADVERSARIAL_CASES["NC-6"]["verdict"])

    def test_nc7_corrupt_sample_stops_remaining_campaign(self):
        executor = FakeExecutor(
            [
                self._receipt(),
                self._receipt(),
                self._receipt(output_sha256="f" * 64),
                self._receipt(),
                self._receipt(),
            ]
        )
        result = self._run(executor)
        self.assertEqual(result.samples_completed, 2)
        self.assertEqual(executor.calls, 3)
        files = self._campaign_files(result)
        self.assertIn("sample-03.receipt.json", files)
        self.assertNotIn("sample-04.started.json", files)
        self.assertNotIn("sample-05.started.json", files)

    def test_nc8_symlink_evidence_root_is_rejected(self):
        target = self.temp_path / "outside"
        target.mkdir(mode=0o700)
        link = self.temp_path / "linked-state"
        link.symlink_to(target, target_is_directory=True)
        self.evidence_base = link / "experiments"
        executor = FakeExecutor([self._receipt()])
        result = self._run(executor)
        self.assertEqual(result.status, "ABORTED")
        self.assertEqual(result.verdict, ADVERSARIAL_CASES["NC-8"]["verdict"])
        self.assertEqual(executor.calls, 0)
        self.assertEqual(list(target.iterdir()), [])

    def test_nc9_path_impostor_does_not_change_trusted_binary_resolution(self):
        fake_bin = self.temp_path / "bin"
        fake_bin.mkdir(mode=0o700)
        impostor = fake_bin / "bwrap"
        impostor.write_text("#!/bin/sh\nexit 0\n")
        impostor.chmod(0o700)
        executor = SyntheticWorkloadExecutor()
        with patch.dict(os.environ, {"PATH": str(fake_bin)}):
            try:
                bubblewrap, python = executor._sandbox_binaries()
            except SandboxUnavailableError as exc:
                skip_or_fail(self, f"trusted Bubblewrap is unavailable: {exc}")
        self.assertNotEqual(Path(bubblewrap), impostor)
        self.assertTrue(Path(bubblewrap).is_absolute())
        self.assertTrue(Path(python).is_absolute())

    def test_nc10_keyboard_interrupt_is_recorded_as_noncomplete(self):
        executor = FakeExecutor([KeyboardInterrupt()])
        result = self._run(executor)
        self.assertEqual(result.status, "ABORTED")
        self.assertEqual(result.verdict, "REFUTED")
        self.assertEqual(executor.calls, 1)
        files = self._campaign_files(result)
        self.assertIn("sample-01.interrupted.json", files)
        campaign = json.loads(files["campaign.json"].read_text())
        self.assertNotEqual(campaign["status"], "COMPLETED")


if __name__ == "__main__":
    unittest.main()
