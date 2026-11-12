import hashlib
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core.cpu_power import MONITORED_STATE_FIELDS, monitored_state
from core.experiment import SyntheticExperimentPlan, SyntheticExperimentRunner
from core.executor import WorkloadReceipt
from core.planning import SyntheticExperimentProposal
from core.recovery import inspect_synthetic_campaign
from core.rollback import (
    ROLLBACK_FAILED_STATE_DIFF,
    ROLLBACK_RESTORED_VERIFIED,
    ROLLBACK_VERIFIED_UNCHANGED,
    RollbackReceipt,
    RollbackContract,
)

from tests.test_synthetic_experiment import FakeExecutor
from tests.tty_test_utils import authorize_in_pty


CPUS = "/sys/devices/system/cpu/cpu0/cpufreq"
BOOST = "/sys/devices/system/cpu/cpufreq/boost"
TRIPLE = ("prestate.json", "poststate.json", "rollback.json")
SANDBOX_BINARIES = {
    "bubblewrap_path": "/usr/bin/bwrap",
    "bubblewrap_sha256": "a" * 64,
    "python_path": "/usr/bin/python3",
    "python_sha256": "b" * 64,
}


def _write_sysfs(root, readings):
    for relative, value in readings.items():
        path = root / relative.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value + "\n", encoding="ascii")


def _fixture_host(root, governor="performance"):
    _write_sysfs(
        root,
        {
            CPUS + "/scaling_driver": "amd-pstate-epp",
            CPUS + "/scaling_governor": governor,
            CPUS + "/energy_performance_preference": governor,
            BOOST: "1",
            "/sys/firmware/acpi/platform_profile": governor,
        },
    )
    return monitored_state(str(root))


class _ScriptedContract:
    """The RollbackContract surface with scripted observations, so drift is reproducible.

    The live sysfs reader is covered by test_host_reads_use_only_allowlisted_commands;
    this script exists to make the two observations disagree on purpose.
    """

    def __init__(
        self,
        prestate,
        observations,
        restoration_command=None,
        restoration_success=False,
        prestate_error=None,
    ):
        self.prestate = dict(prestate)
        self.observations = list(observations)
        self.restoration_command = restoration_command
        self.restoration_success = restoration_success
        self.prestate_error = prestate_error
        self.observed_state = None
        self.observation_error = None
        self.restoration_receipt = None
        self.captures = 0
        self.verifies = 0
        self.executions = 0

    def capture_prestate(self):
        self.captures += 1
        if self.prestate_error is not None:
            raise self.prestate_error
        self.restoration_receipt = None
        self.observation_error = None
        self.observed_state = dict(self.prestate)
        return dict(self.prestate)

    def verify_restoration(self, prestate):
        self.verifies += 1
        if not self.observations:
            raise AssertionError("scripted rollback contract ran out of observations")
        outcome = self.observations.pop(0)
        if isinstance(outcome, BaseException):
            self.observed_state = None
            self.observation_error = str(outcome)
            raise outcome
        self.observed_state = dict(outcome)
        self.observation_error = None
        return outcome == prestate

    def execute_rollback(self, prestate):
        self.executions += 1
        if self.restoration_command is None:
            self.restoration_receipt = None
            return False
        self.restoration_receipt = RollbackReceipt(
            success=self.restoration_success,
            experiment_id="RB-SCRIPTED",
            target_command=self.restoration_command,
            verified_state="performance" if self.restoration_success else "FAILED",
            error_message=None if self.restoration_success else "injected failure",
        )
        return self.restoration_success


class TestCampaignRollback(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.temp_path = Path(self.temp.name)
        os.chmod(self.temp_path, 0o700)
        self.evidence_base = self.temp_path / "xdg" / "ihra" / "synthetic-experiments"
        self.plan = SyntheticExperimentPlan.fixed_repeatability_plan()
        self.state_a = {field: "value-a" for field in MONITORED_STATE_FIELDS}
        self.state_a["boost_enabled"] = True
        self.state_b = dict(self.state_a, governor="powersave", platform_profile="balanced")

    def _receipt(self):
        return WorkloadReceipt(
            workload_id=self.plan.workload_id,
            status="COMPLETED",
            output_sha256=self.plan.expected_result_digest,
            elapsed_seconds=0.025,
            sandbox="bubblewrap-rootless",
            hardware_telemetry_reads=0,
            device_control_operations=0,
            persistent_host_state_changed=False,
            sandboxed_processes_started=1,
            reason="fixed workload completed",
            runner_sha256=self.plan.runner_digest,
            process_exit_code=0,
        )

    def _runner(self, contract, samples=5):
        runner = SyntheticExperimentRunner(
            executor=FakeExecutor([self._receipt() for _ in range(samples)]),
            evidence_base=self.evidence_base,
            repository_root=Path(__file__).resolve().parents[1],
            rollback_contract=contract,
            fault_base=self.temp_path / "xdg" / "ihra" / "tier1-faults",
        )
        runner._verify_sandbox_binaries = Mock(return_value=dict(SANDBOX_BINARIES))
        return runner

    def _proposal(self):
        return SyntheticExperimentProposal(
            schema_version=1,
            proposal_id="test-rollback-proposal",
            rationale="Authorize the fixed plan so rollback evidence can be inspected.",
            plan=self.plan,
        )

    def _run(self, contract, authorization=None):
        proposal = self._proposal()
        authorization = authorization or authorize_in_pty(
            self.plan.sha256, proposal.sha256, proposal.canonical_bytes().decode("ascii")
        )
        result = self._runner(contract).run(
            self.plan,
            authorization=authorization,
            proposal_bytes=proposal.canonical_bytes(),
        )
        return authorization, proposal, result

    @staticmethod
    def _artifacts(result):
        directory = Path(result.evidence_dir)
        return directory, {path.name: path for path in directory.iterdir()}

    @staticmethod
    def _reseal_manifest(directory):
        lines = [
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
            for path in sorted(directory.iterdir())
            if path.name != "SHA256SUMS.txt"
        ]
        manifest = directory / "SHA256SUMS.txt"
        manifest.write_text("\n".join(lines) + "\n", encoding="ascii")
        manifest.chmod(0o600)

    @staticmethod
    def _json_of(files, name):
        return json.loads(files[name].read_text())

    def test_campaign_persists_prestate_and_rollback_receipt_in_manifest(self):
        _authorization, _proposal, result = self._run(
            _ScriptedContract(self.state_a, [self.state_a])
        )
        self.assertEqual(result.status, "COMPLETED")
        _directory, files = self._artifacts(result)

        for name in TRIPLE:
            self.assertIn(name, files)
            self.assertEqual(stat.S_IMODE(files[name].stat().st_mode), 0o600)

        manifest = {
            line.split("  ", 1)[1]: line.split("  ", 1)[0]
            for line in files["SHA256SUMS.txt"].read_text().splitlines()
        }
        self.assertTrue(set(TRIPLE) <= set(manifest))
        for name in TRIPLE:
            self.assertEqual(
                manifest[name], hashlib.sha256(files[name].read_bytes()).hexdigest()
            )

        prestate = self._json_of(files, "prestate.json")
        poststate = self._json_of(files, "poststate.json")
        record = self._json_of(files, "rollback.json")
        campaign = self._json_of(files, "campaign.json")
        self.assertEqual(prestate["state"], self.state_a)
        self.assertEqual(prestate["plan_sha256"], self.plan.sha256)
        self.assertEqual(poststate["state"], self.state_a)
        self.assertIsNone(poststate["observation_error"])
        self.assertEqual(record["status"], ROLLBACK_VERIFIED_UNCHANGED)
        self.assertEqual(record["differences"], [])
        self.assertFalse(record["restoration_executed"])
        self.assertEqual(
            record["prestate_sha256"],
            hashlib.sha256(files["prestate.json"].read_bytes()).hexdigest(),
        )
        self.assertEqual(
            record["poststate_sha256"],
            hashlib.sha256(files["poststate.json"].read_bytes()).hexdigest(),
        )
        self.assertEqual(campaign["rollback_status"], ROLLBACK_VERIFIED_UNCHANGED)

        inspection = inspect_synthetic_campaign(result.campaign_id, self.evidence_base)
        self.assertEqual(inspection.inspection_status, "COMPLETED_VALID")
        self.assertEqual(inspection.issues, [])

    def test_state_diff_triggers_rollback_and_abort(self):
        contract = _ScriptedContract(self.state_a, [self.state_b])
        _authorization, _proposal, result = self._run(contract)
        self.assertEqual(result.status, "ABORTED")
        self.assertEqual(result.verdict, "REFUTED")
        self.assertTrue(
            result.reason.startswith("Host state changed and was not restored:")
        )
        _directory, files = self._artifacts(result)

        record = self._json_of(files, "rollback.json")
        self.assertEqual(record["status"], ROLLBACK_FAILED_STATE_DIFF)
        self.assertFalse(record["restoration_executed"])
        self.assertIsNone(record["restoration_command"])
        self.assertEqual(
            record["differences"],
            [
                {"field": "governor", "prestate": "value-a", "poststate": "powersave"},
                {
                    "field": "platform_profile",
                    "prestate": "value-a",
                    "poststate": "balanced",
                },
            ],
        )
        self.assertEqual(
            self._json_of(files, "campaign.json")["rollback_status"],
            ROLLBACK_FAILED_STATE_DIFF,
        )
        # The runner asks the contract to restore; without a declared command the
        # contract runs nothing and says so, and that answer is what gets sealed.
        self.assertEqual(contract.executions, 1)
        self.assertIsNone(record["restoration_receipt"])

        inspection = inspect_synthetic_campaign(result.campaign_id, self.evidence_base)
        self.assertEqual(inspection.inspection_status, "INCOMPLETE")
        self.assertEqual(inspection.verdict, "NOT_DEMONSTRATED")

    def test_failed_restoration_records_fault_and_stops(self):
        contract = _ScriptedContract(
            self.state_a,
            [self.state_b],
            restoration_command="powerprofilesctl set performance",
            restoration_success=False,
        )
        _authorization, _proposal, result = self._run(contract)
        self.assertEqual(result.status, "ABORTED")
        _directory, files = self._artifacts(result)

        record = self._json_of(files, "rollback.json")
        self.assertEqual(record["status"], ROLLBACK_FAILED_STATE_DIFF)
        self.assertTrue(record["restoration_executed"])
        self.assertEqual(contract.executions, 1)
        # A failed restoration is not re-observed: the close reading stands.
        self.assertEqual(contract.verifies, 1)
        self.assertFalse(record["restoration_receipt"]["success"])
        self.assertEqual(
            record["restoration_receipt"]["target_command"], contract.restoration_command
        )
        self.assertIn("Restoration did not verify", record["reason"])
        inspection = inspect_synthetic_campaign(result.campaign_id, self.evidence_base)
        self.assertEqual(inspection.inspection_status, "INCOMPLETE")

    def test_successful_restoration_after_drift_is_restored_verified(self):
        contract = _ScriptedContract(
            self.state_a,
            [self.state_b, self.state_a],
            restoration_command="powerprofilesctl set performance",
            restoration_success=True,
        )
        _authorization, _proposal, result = self._run(contract)
        self.assertEqual(result.status, "COMPLETED")
        _directory, files = self._artifacts(result)

        record = self._json_of(files, "rollback.json")
        self.assertEqual(record["status"], ROLLBACK_RESTORED_VERIFIED)
        self.assertTrue(record["restoration_executed"])
        self.assertEqual(record["verified_state"], self.state_a)
        # The poststate keeps the drifted close reading: restoration is evidenced by
        # the verified state inside the sealed rollback record, never by rewriting it.
        self.assertEqual(self._json_of(files, "poststate.json")["state"], self.state_b)
        inspection = inspect_synthetic_campaign(result.campaign_id, self.evidence_base)
        self.assertEqual(inspection.inspection_status, "COMPLETED_VALID")

    def test_unobservable_close_state_is_a_failed_diff_not_a_pass(self):
        failure = OSError("CPU power surface is unreadable")
        contract = _ScriptedContract(self.state_a, [failure])
        _authorization, _proposal, result = self._run(contract)
        self.assertEqual(result.status, "ABORTED")
        _directory, files = self._artifacts(result)

        record = self._json_of(files, "rollback.json")
        self.assertEqual(record["status"], ROLLBACK_FAILED_STATE_DIFF)
        self.assertIsNone(record["close_state"])
        self.assertIn("could not be observed", record["reason"])
        poststate = self._json_of(files, "poststate.json")
        self.assertIsNone(poststate["state"])
        self.assertEqual(poststate["observation_error"], str(failure))

    def test_host_reads_use_only_allowlisted_commands(self):
        host = self.temp_path / "host"
        host.mkdir()
        observed = _fixture_host(host)
        self.assertEqual(set(observed), set(MONITORED_STATE_FIELDS))
        contract = RollbackContract(
            sysfs_root=str(host),
            restoration_command="powerprofilesctl set performance",
            expected_restored_state="performance",
            verification_query="powerprofilesctl get",
            experiment_id="RB-ALLOWLIST",
        )
        forbidden = Mock(side_effect=AssertionError("host observation executed a command"))
        with patch("core.rollback.subprocess.run", forbidden):
            _authorization, _proposal, result = self._run(contract)
        self.assertEqual(result.status, "COMPLETED")
        forbidden.assert_not_called()
        _directory, files = self._artifacts(result)
        self.assertEqual(
            self._json_of(files, "rollback.json")["status"], ROLLBACK_VERIFIED_UNCHANGED
        )
        self.assertEqual(self._json_of(files, "prestate.json")["state"], observed)

        # When a restoration is declared it is exactly the allowlisted command pair.
        _write_sysfs(host, {CPUS + "/scaling_governor": "powersave"})
        executed = []

        def record_command(argv, *args, **kwargs):
            executed.append(tuple(argv))
            return Mock(stdout="performance\n")

        drifted = monitored_state(str(host))
        with patch("core.rollback.subprocess.run", side_effect=record_command):
            self.assertTrue(contract.execute_rollback(drifted))
        self.assertEqual(
            executed,
            [("powerprofilesctl", "set", "performance"), ("powerprofilesctl", "get")],
        )

    def test_prestate_failure_denies_before_first_sample(self):
        failure = OSError("CPU power surface is unreadable at /sys")
        proposal = self._proposal()
        authorization = authorize_in_pty(
            self.plan.sha256, proposal.sha256, proposal.canonical_bytes().decode("ascii")
        )
        contract = _ScriptedContract(self.state_a, [self.state_a], prestate_error=failure)
        _authorization, _proposal, result = self._run(contract, authorization=authorization)
        self.assertEqual(result.status, "DENIED")
        self.assertEqual(result.verdict, "DENY")
        self.assertIn("unreadable", result.reason)
        self.assertIsNone(result.evidence_dir)
        self.assertFalse(self.evidence_base.exists())
        self.assertEqual(contract.captures, 1)
        self.assertEqual(contract.verifies, 0)
        # The ceremony survives the failure: the grant was never consumed.
        self.assertEqual(authorization.consume(self.plan.sha256, proposal.sha256),
                         proposal.sha256)

    def test_campaign_without_rollback_evidence_is_incomplete(self):
        _authorization, _proposal, result = self._run(
            _ScriptedContract(self.state_a, [self.state_a])
        )
        directory, files = self._artifacts(result)
        for name in TRIPLE:
            files[name].unlink()
        self._reseal_manifest(directory)

        inspection = inspect_synthetic_campaign(result.campaign_id, self.evidence_base)
        self.assertEqual(inspection.inspection_status, "INCOMPLETE")
        self.assertEqual(inspection.verdict, "NOT_DEMONSTRATED")
        self.assertIn(
            "Execution-coupled rollback evidence is missing: "
            "poststate.json, prestate.json, rollback.json",
            " ".join(inspection.issues),
        )

    def test_relabelled_rollback_status_is_rejected_by_inspection(self):
        contract = _ScriptedContract(self.state_a, [self.state_b])
        _authorization, _proposal, result = self._run(contract)
        directory, files = self._artifacts(result)
        self.assertEqual(result.status, "ABORTED")

        # Every receipt is durable, so a hand edit that calls the drifted close
        # "unchanged" has to be rejected on the sealed states, not on the label.
        record = self._json_of(files, "rollback.json")
        tampered = dict(record, status=ROLLBACK_VERIFIED_UNCHANGED, differences=[])
        files["rollback.json"].write_bytes(
            (json.dumps(tampered, sort_keys=True, separators=(",", ":")) + "\n").encode()
        )
        campaign = self._json_of(files, "campaign.json")
        campaign.update(
            {
                "status": "COMPLETED",
                "verdict": "DEMONSTRATED",
                "rollback_status": ROLLBACK_VERIFIED_UNCHANGED,
            }
        )
        files["campaign.json"].write_bytes(
            (json.dumps(campaign, sort_keys=True, separators=(",", ":")) + "\n").encode()
        )
        self._reseal_manifest(directory)

        inspection = inspect_synthetic_campaign(result.campaign_id, self.evidence_base)
        self.assertEqual(inspection.inspection_status, "INCOMPLETE")
        joined = " ".join(inspection.issues)
        self.assertIn("but the states say FAILED_STATE_DIFF", joined)
        self.assertIn("Completed campaign carries unrestored host state", joined)


if __name__ == "__main__":
    unittest.main()
