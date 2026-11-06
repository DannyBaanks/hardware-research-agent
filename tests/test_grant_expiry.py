import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from core.authorization import (
    AuthorizationDenied,
    AuthorizationExpired,
    DEFAULT_TTL_SECONDS,
)
from core.experiment import SyntheticExperimentPlan, SyntheticExperimentRunner
from core.planning import SyntheticExperimentProposal
from core.executor import WorkloadReceipt
from core.sentinel import AuthorizationGrant, SentinelGate, SentinelVerdict

from tests.test_synthetic_experiment import FakeExecutor
from tests.tty_test_utils import authorize_in_pty


class FakeClock:
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds
        return self.now


def fixed_proposal():
    plan = SyntheticExperimentPlan.fixed_repeatability_plan()
    return plan, SyntheticExperimentProposal(
        schema_version=1,
        proposal_id="test-grant-expiry",
        rationale="Authorize the fixed plan to test authorization lifetime.",
        plan=plan,
    )


def receipt(plan, **overrides):
    fields = {
        "workload_id": plan.workload_id,
        "status": "COMPLETED",
        "output_sha256": plan.expected_result_digest,
        "elapsed_seconds": 0.025,
        "sandbox": "bubblewrap-rootless",
        "hardware_telemetry_reads": 0,
        "device_control_operations": 0,
        "persistent_host_state_changed": False,
        "sandboxed_processes_started": 1,
        "reason": "fixed workload completed",
        "runner_sha256": plan.runner_digest,
        "process_exit_code": 0,
    }
    fields.update(overrides)
    return WorkloadReceipt(**fields)


class TestGrantExpiry(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        temp_path = Path(self.temp.name)
        os.chmod(temp_path, 0o700)
        self.evidence_base = temp_path / "xdg" / "ihra" / "synthetic-experiments"
        self.repository_root = Path(__file__).resolve().parents[1]

    def _runner(self, executor):
        runner = SyntheticExperimentRunner(
            executor=executor,
            evidence_base=self.evidence_base,
            repository_root=self.repository_root,
        )
        runner._verify_sandbox_binaries = Mock(
            return_value={
                "bubblewrap_path": "/usr/bin/bwrap",
                "bubblewrap_sha256": "a" * 64,
                "python_path": "/usr/bin/python3",
                "python_sha256": "b" * 64,
            }
        )
        return runner

    def _authorized(self, plan, proposal, clock, ttl_seconds):
        return authorize_in_pty(
            plan.sha256,
            proposal.sha256,
            proposal.canonical_bytes().decode("ascii"),
            ttl_seconds=ttl_seconds,
            clock=clock,
        )

    def test_expired_grant_denies_before_dispatch(self):
        plan, proposal = fixed_proposal()
        clock = FakeClock(1_000.0)
        authorization = self._authorized(plan, proposal, clock, ttl_seconds=10)
        clock.advance(11)
        executor = FakeExecutor([receipt(plan)])

        result = self._runner(executor).run(
            plan, authorization=authorization, proposal_bytes=proposal.canonical_bytes()
        )

        self.assertEqual(result.status, "DENIED")
        self.assertEqual(result.verdict, "DENY")
        self.assertIn("expired", result.reason)
        self.assertEqual(executor.calls, 0)
        self.assertFalse(self.evidence_base.exists())

    def test_expired_grant_is_not_consumed(self):
        plan, proposal = fixed_proposal()
        clock = FakeClock(1_000.0)
        authorization = self._authorized(plan, proposal, clock, ttl_seconds=10)
        clock.advance(11)

        with self.assertRaises(AuthorizationExpired):
            authorization.consume(plan.sha256, proposal.sha256)

        # A fake clock may move backwards; a real monotonic clock cannot. Doing it
        # here proves the refused consume() above did not burn the single use.
        clock.now = 1_005.0
        self.assertEqual(authorization.consume(plan.sha256, proposal.sha256), proposal.sha256)
        with self.assertRaises(AuthorizationDenied):
            authorization.consume(plan.sha256, proposal.sha256)

    def test_backwards_clock_reads_as_expired(self):
        plan, proposal = fixed_proposal()
        clock = FakeClock(1_000.0)
        authorization = self._authorized(plan, proposal, clock, ttl_seconds=10)
        clock.now = 900.0
        self.assertTrue(authorization.expired())

    def test_ttl_revalidated_before_each_sample(self):
        plan, proposal = fixed_proposal()
        clock = FakeClock(1_000.0)
        authorization = self._authorized(plan, proposal, clock, ttl_seconds=100)

        def expire_after_two(workload_id):
            if executor.calls == 2:
                clock.now = 1_000.0 + 101
            return receipt(plan)

        executor = FakeExecutor([expire_after_two] * plan.samples)
        result = self._runner(executor).run(
            plan, authorization=authorization, proposal_bytes=proposal.canonical_bytes()
        )

        self.assertEqual(executor.calls, 2)
        self.assertEqual(result.samples_completed, 2)
        self.assertEqual(result.status, "ABORTED")
        self.assertIn("Authorization expired before sample 3", result.reason)
        completed = sorted(
            path.name for path in Path(result.evidence_dir).iterdir() if ".receipt." in path.name
        )
        self.assertEqual(len(completed), 2)

    def test_ttl_default_covers_campaign_ceiling(self):
        plan = SyntheticExperimentPlan.fixed_repeatability_plan()
        self.assertGreaterEqual(DEFAULT_TTL_SECONDS, plan.max_total_duration)

    def test_shorter_than_campaign_ttl_is_denied(self):
        plan, proposal = fixed_proposal()
        clock = FakeClock(1_000.0)
        authorization = self._authorized(plan, proposal, clock, ttl_seconds=5)
        executor = FakeExecutor([receipt(plan)])

        result = self._runner(executor).run(
            plan, authorization=authorization, proposal_bytes=proposal.canonical_bytes()
        )

        self.assertEqual(result.status, "DENIED")
        self.assertIn("lifetime", result.reason)
        self.assertEqual(executor.calls, 0)

    def test_ttl_cannot_exceed_the_fixed_ceiling(self):
        plan, proposal = fixed_proposal()
        with self.assertRaises(AuthorizationDenied):
            authorize_in_pty(
                plan.sha256,
                proposal.sha256,
                proposal.canonical_bytes().decode("ascii"),
                ttl_seconds=DEFAULT_TTL_SECONDS + 1,
            )


class TestSentinelGrantBinding(unittest.TestCase):
    def setUp(self):
        self.gate = SentinelGate()
        self.plan = {"experiment_id": "G-1", "workload": {"command": "lscpu"}}

    def test_grant_binds_the_plan_digest(self):
        grant = self.gate.evaluate_plan(self.plan)
        self.assertEqual(grant.verdict, SentinelVerdict.AUTHORIZED)
        self.assertTrue(self.gate.verify_grant(grant, self.plan))

    def test_grant_rejects_a_mutated_plan(self):
        grant = self.gate.evaluate_plan(self.plan)
        tampered = dict(self.plan, risk_tier=0)
        self.assertFalse(self.gate.verify_grant(grant, tampered))
        swapped = {"experiment_id": "G-1", "workload": {"command": "lspci"}}
        self.assertFalse(self.gate.verify_grant(grant, swapped))

    def test_forged_grant_object_is_rejected(self):
        forged = AuthorizationGrant(
            verdict=SentinelVerdict.AUTHORIZED,
            experiment_id="G-1",
            risk_tier=0,
            reason="forged",
            allowed_operations=["lscpu"],
            plan_sha256="0" * 64,
            issued_at=0.0,
            expires_at=10**9,
            grant_token="f" * 64,
        )
        self.assertFalse(self.gate.verify_grant(forged, self.plan))

    def test_grant_without_binding_fields_is_rejected(self):
        unbound = AuthorizationGrant(
            verdict=SentinelVerdict.AUTHORIZED,
            experiment_id="G-1",
            risk_tier=0,
            reason="legacy shape",
            allowed_operations=["lscpu"],
        )
        self.assertFalse(self.gate.verify_grant(unbound, self.plan))

    def test_expired_grant_is_rejected(self):
        grant = self.gate.evaluate_plan(self.plan)
        self.assertFalse(self.gate.verify_grant(grant, self.plan, clock=lambda: grant.expires_at))
        self.assertFalse(self.gate.verify_grant(grant, self.plan, clock=lambda: -1.0))

    def test_denied_grant_is_never_verifiable(self):
        grant = self.gate.evaluate_plan(
            {"experiment_id": "G-2", "workload": {"command": "powerprofilesctl set balanced"}}
        )
        self.assertEqual(grant.verdict, SentinelVerdict.DENIED)
        self.assertFalse(self.gate.verify_grant(grant, {"experiment_id": "G-2"}))

    def test_non_serializable_plan_is_denied(self):
        grant = self.gate.evaluate_plan({"experiment_id": "G-3", "workload": {"command": b"lscpu"}})
        self.assertEqual(grant.verdict, SentinelVerdict.DENIED)


if __name__ == "__main__":
    unittest.main()
