import argparse
import contextlib
import hashlib
import io
import json
import os
import signal
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core.authorization import AuthorizationDenied
from core.cli import cmd_list_faults, cmd_run_tier1
from core.cpu_power import monitored_state
from core.experiment import SyntheticExperimentPlan, SyntheticExperimentRunner
from core.recovery import (
    FAULT_RESOLVED,
    FAULT_UNRESOLVED,
    FaultStateError,
    find_unresolved_faults,
    write_fault_record,
)
from core.rollback import ROLLBACK_FAILED_STATE_DIFF, ROLLBACK_RESTORED_VERIFIED
from core.sentinel import (
    SentinelGate,
    SentinelVerdict,
    TIER1_REVIEW_REASON,
    canonical_plan_digest,
    experiment_plan_issues,
    tier1_intervention,
    tier1_intervention_digest,
)
from core.tier1 import Tier1ExperimentRunner, build_tier1_plan

from tests.tty_test_utils import authorize_in_pty


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CPUS = "/sys/devices/system/cpu/cpu0/cpufreq"
BOOST = "/sys/devices/system/cpu/cpufreq/boost"
# The nodes a supported profile actually moves: the fake host mirrors them together,
# the way powerprofilesctl does on a real machine.
PROFILE_NODES = (CPUS + "/energy_performance_preference", "/sys/firmware/acpi/platform_profile")
GET = ("powerprofilesctl", "get")
SET_BALANCED = ("powerprofilesctl", "set", "balanced")
SET_PERFORMANCE = ("powerprofilesctl", "set", "performance")
EVIDENCE_NAMES = {
    "plan.json",
    "prestate.json",
    "campaign.json",
    "intervention.json",
    "measurement.json",
    "poststate.json",
    "rollback.json",
}


class _FakeProfileHost:
    def __init__(self, profile, nodes, fail_sets=frozenset(), interrupt_on=None):
        self.profile = profile
        self.nodes = nodes
        self.fail_sets = set(fail_sets)
        self.interrupt_on = interrupt_on
        self.commands = []

    def run(self, argv, **_kwargs):
        argv = tuple(str(part) for part in argv)
        self.commands.append(argv)
        if argv == GET:
            return Mock(stdout=self.profile + "\n")
        if argv[0] == "powerprofilesctl" and argv[1] == "set" and len(argv) == 3:
            if argv[2] not in self.fail_sets:
                self.profile = argv[2]
                self.mirror()
            if self.interrupt_on == argv[2]:
                self.interrupt_on = None
                os.kill(os.getpid(), signal.SIGTERM)
            return Mock(stdout="")
        raise AssertionError(f"Tier-1 executed an unsupported command: {argv}")

    def mirror(self):
        for node in self.nodes:
            node.parent.mkdir(parents=True, exist_ok=True)
            node.write_text(self.profile + "\n", encoding="ascii")


class TestTier1Safety(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        temp_path = Path(self.temp.name)
        os.chmod(temp_path, 0o700)
        self.host_root = temp_path / "host"
        self.host_root.mkdir()
        self.xdg = temp_path / "xdg"
        self.evidence_base = self.xdg / "ihra" / "tier1-experiments"
        self.fault_base = self.xdg / "ihra" / "tier1-faults"
        self.plan = build_tier1_plan("balanced", "performance")
        self.digest = canonical_plan_digest(self.plan)

    def _surface(self, profile):
        for relative in PROFILE_NODES:
            node = self.host_root / relative.lstrip("/")
            node.parent.mkdir(parents=True, exist_ok=True)
            node.write_text(profile + "\n", encoding="ascii")
        driver = self.host_root / CPUS.lstrip("/") / "scaling_driver"
        driver.parent.mkdir(parents=True, exist_ok=True)
        driver.write_text("amd-pstate-epp\n", encoding="ascii")
        driver.parent.joinpath("scaling_governor").write_text("performance\n", encoding="ascii")
        boost = self.host_root / BOOST.lstrip("/")
        boost.parent.mkdir(parents=True, exist_ok=True)
        boost.write_text("1\n", encoding="ascii")
        return monitored_state(str(self.host_root))

    def _host(self, profile="performance", mirror=True, **kwargs):
        """A host at `profile`; mirroring models the remediation a person ran by hand."""
        host = _FakeProfileHost(
            profile, [self.host_root / node.lstrip("/") for node in PROFILE_NODES], **kwargs
        )
        if mirror:
            host.mirror()
        patcher = patch("core.rollback.subprocess.run", side_effect=host.run)
        patcher.start()
        self.addCleanup(patcher.stop)
        return host

    def _runner(self):
        return Tier1ExperimentRunner(
            sysfs_root=str(self.host_root),
            evidence_base=self.evidence_base,
            repository_root=REPOSITORY_ROOT,
            fault_base=self.fault_base,
        )

    def _grant(self, plan=None, intervention="auto"):
        plan = self.plan if plan is None else plan
        digest = canonical_plan_digest(plan)
        summary = json.dumps(plan, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if intervention == "auto":
            intervention = tier1_intervention_digest(plan)
        return authorize_in_pty(digest, digest, summary, intervention_sha256=intervention)

    def _run(self, plan=None, authorization=None):
        plan = self.plan if plan is None else plan
        authorization = self._grant(plan) if authorization is None else authorization
        return self._runner().run(plan, authorization)

    def _files(self, result):
        directory = Path(result.evidence_dir)
        return directory, {path.name: path for path in directory.iterdir()}

    def _json_of(self, result, name):
        _directory, files = self._files(result)
        return json.loads(files[name].read_text(encoding="utf-8"))

    def _list_faults(self):
        """The fault CLI reads the default fault state and takes no caller path."""
        output = io.StringIO()
        with patch.dict(os.environ, {"XDG_STATE_HOME": str(self.xdg)}), \
                contextlib.redirect_stdout(output):
            try:
                cmd_list_faults(argparse.Namespace())
            except SystemExit as raised:
                return json.loads(output.getvalue()), raised.code
        return json.loads(output.getvalue()), None

    def test_generated_plan_is_a_complete_verifiable_tier1_plan(self):
        self.assertEqual(experiment_plan_issues(self.plan), [])
        self.assertEqual(self.plan["risk_tier"], 1)
        self.assertEqual(
            tier1_intervention(self.plan),
            {
                "applied_command": "powerprofilesctl set balanced",
                "target_profile": "balanced",
                "restoration_command": "powerprofilesctl set performance",
                "verification_query": "powerprofilesctl get",
                "expected_restored_state": "performance",
            },
        )
        self.assertIsInstance(tier1_intervention_digest(self.plan), str)
        self.assertEqual(
            build_tier1_plan("balanced", "balanced")["rollback_plan"]["restoration_command"],
            "powerprofilesctl set balanced",
        )
        for bad in (("nope", "performance"), ("performance", "nope")):
            with self.assertRaises(ValueError):
                build_tier1_plan(*bad)

    def test_tier1_requires_human_grant(self):
        forbidden = Mock(side_effect=AssertionError("Tier-1 review executed a command"))
        with patch("core.rollback.subprocess.run", forbidden):
            gate = SentinelGate()
            unattended = gate.evaluate_plan(self.plan)
            self.assertEqual(unattended.verdict, SentinelVerdict.DENIED)
            self.assertEqual(unattended.reason, TIER1_REVIEW_REASON)

            # The Tier-1 leg refuses anything that is not a live TTY grant.
            for impersonation in (None, object(), "authorized"):
                denied = gate.evaluate_tier1(self.plan, impersonation)
                self.assertEqual(denied.verdict, SentinelVerdict.DENIED, impersonation)
                self.assertEqual(denied.reason, TIER1_REVIEW_REASON)
            forbidden.assert_not_called()

    def test_tier1_grant_binds_exact_profile(self):
        authorization, prompt = authorize_in_pty(
            self.digest,
            self.digest,
            json.dumps(self.plan, sort_keys=True, separators=(",", ":"), allow_nan=False),
            intervention_sha256=tier1_intervention_digest(self.plan),
            capture=True,
        )
        self.assertIn(
            f"Intervention SHA-256: {tier1_intervention_digest(self.plan)}".encode(), prompt
        )

        grant = SentinelGate().evaluate_tier1(self.plan, authorization)
        self.assertEqual(grant.verdict, SentinelVerdict.AUTHORIZED)
        self.assertEqual(grant.risk_tier, 1)
        self.assertEqual(
            grant.allowed_operations,
            ["powerprofilesctl set balanced", "powerprofilesctl set performance"],
        )
        # Reviewing a plan must not spend the ceremony.
        self.assertFalse(authorization.used)

        host = self._host()
        other = build_tier1_plan("power-saver", "performance")
        mismatched = self._runner().run(other, authorization)
        self.assertEqual(mismatched.status, "DENIED")
        self.assertIn("not bound to this exact plan", mismatched.reason)
        self.assertIsNone(mismatched.evidence_dir)
        self.assertEqual(host.commands, [])
        self.assertFalse(authorization.used)

        # A grant minted without an intervention slot cannot open Tier-1 either.
        generic = authorize_in_pty(self.digest, self.digest, "{}")
        unbound = SentinelGate().evaluate_tier1(self.plan, generic)
        self.assertEqual(unbound.verdict, SentinelVerdict.DENIED)
        self.assertIn("not bound to this exact plan", unbound.reason)

        # Nor can a grant bound to a different intervention.
        diverted = self._grant(intervention="c" * 64)
        refused = SentinelGate().evaluate_tier1(self.plan, diverted)
        self.assertEqual(refused.verdict, SentinelVerdict.DENIED)
        self.assertIn("not bound to this exact plan", refused.reason)
        self.assertEqual(host.commands, [])

    def test_tier1_still_denied_without_authorization(self):
        host = self._host()
        output = io.StringIO()
        with patch(
            "core.cli.authorize_plan_from_tty",
            side_effect=AuthorizationDenied("No controlling TTY is available"),
        ), contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as exit_context:
                cmd_run_tier1(argparse.Namespace(plan=None, set_profile="balanced"))
        self.assertEqual(exit_context.exception.code, 1)
        reported = json.loads(output.getvalue())
        self.assertEqual(reported["status"], "DENIED")
        self.assertEqual(reported["reason"], "No controlling TTY is available")
        # Building a plan from --set reads the host, and the refusal changes nothing.
        self.assertEqual(host.commands, [GET])

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as exit_context:
                cmd_run_tier1(argparse.Namespace(plan=None, set_profile=None))
        self.assertEqual(exit_context.exception.code, 2)
        self.assertIn("exactly one of", json.loads(output.getvalue())["reason"])

    def test_tier1_round_trip_seals_authorized_rollback(self):
        prestate = self._surface("performance")
        host = self._host()
        authorization = self._grant()
        result = self._runner().run(self.plan, authorization)
        self.assertEqual(result.status, "COMPLETED", result.reason)
        self.assertEqual(result.verdict, "DEMONSTRATED")
        self.assertEqual(result.plan_sha256, self.digest)
        self.assertEqual(result.samples_completed, 1)
        self.assertTrue(authorization.used)
        self.assertEqual(host.profile, "performance")

        # Exactly two writes: the authorized change and the authorized way back.
        self.assertEqual(
            host.commands, [GET, SET_BALANCED, GET, GET, SET_PERFORMANCE, GET]
        )

        directory, files = self._files(result)
        self.assertEqual(set(files) - {"SHA256SUMS.txt"}, EVIDENCE_NAMES)
        for path in files.values():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

        # The sealed plan bytes are the bytes the human approved.
        self.assertEqual(hashlib.sha256(files["plan.json"].read_bytes()).hexdigest(), self.digest)

        manifest = {
            line.split("  ", 1)[1]: line.split("  ", 1)[0]
            for line in (directory / "SHA256SUMS.txt").read_text(encoding="ascii").splitlines()
        }
        self.assertEqual(set(manifest), EVIDENCE_NAMES)
        for name, digest in manifest.items():
            self.assertEqual(digest, hashlib.sha256(files[name].read_bytes()).hexdigest())

        prestate_record = self._json_of(result, "prestate.json")
        self.assertEqual(prestate_record["state"], prestate)
        self.assertEqual(prestate_record["profile"], "performance")

        measurement = self._json_of(result, "measurement.json")
        self.assertEqual(measurement["profile"], "balanced")
        self.assertFalse(measurement["performance_claim"])
        self.assertEqual(measurement["state"]["energy_performance_preference"], "balanced")

        campaign = self._json_of(result, "campaign.json")
        self.assertEqual(campaign["status"], "COMPLETED")
        self.assertEqual(campaign["verdict"], "DEMONSTRATED")
        self.assertEqual(campaign["rollback_status"], ROLLBACK_RESTORED_VERIFIED)
        self.assertFalse(campaign["performance_claim"])
        self.assertEqual(campaign["plan_sha256"], self.digest)
        self.assertEqual(campaign["intervention"], tier1_intervention(self.plan))
        self.assertEqual(
            set(campaign["authorization"]),
            {
                "method",
                "plan_sha256",
                "proposal_sha256",
                "ttl_seconds",
                "issued_at",
                "expires_at",
                "intervention_sha256",
            },
        )
        self.assertEqual(campaign["authorization"]["method"], "local_tty_confirmation")
        self.assertEqual(campaign["authorization"]["plan_sha256"], self.digest)
        self.assertEqual(campaign["authorization"]["proposal_sha256"], self.digest)
        self.assertEqual(
            campaign["authorization"]["intervention_sha256"],
            tier1_intervention_digest(self.plan),
        )

        record = self._json_of(result, "rollback.json")
        self.assertEqual(record["status"], ROLLBACK_RESTORED_VERIFIED)
        self.assertTrue(record["restoration_executed"])
        self.assertEqual(record["restoration_command"], "powerprofilesctl set performance")
        self.assertEqual(record["campaign_id"], result.campaign_id)
        self.assertEqual(record["plan_sha256"], self.digest)
        self.assertEqual(record["verified_state"], prestate)
        self.assertEqual(
            record["differences"],
            [
                {
                    "field": "energy_performance_preference",
                    "prestate": "performance",
                    "poststate": "balanced",
                },
                {
                    "field": "platform_profile",
                    "prestate": "performance",
                    "poststate": "balanced",
                },
            ],
        )
        self.assertEqual(
            record["prestate_sha256"],
            hashlib.sha256(files["prestate.json"].read_bytes()).hexdigest(),
        )
        self.assertEqual(
            record["poststate_sha256"],
            hashlib.sha256(files["poststate.json"].read_bytes()).hexdigest(),
        )
        self.assertTrue(record["restoration_receipt"]["success"])

    def test_signal_during_tier1_restores_profile(self):
        self._surface("performance")
        host = self._host(interrupt_on="balanced")
        result = self._run()
        self.assertEqual(result.status, "ABORTED")
        self.assertEqual(result.verdict, "REFUTED")
        self.assertEqual(result.samples_completed, 0)
        self.assertIn("interrupted by signal", result.reason)
        self.assertEqual(host.profile, "performance")

        # The handler restored first; the close-out then proved it and sealed the
        # bundle without claiming a completed campaign.
        self.assertEqual(
            host.commands, [GET, SET_BALANCED, SET_PERFORMANCE, GET, SET_PERFORMANCE, GET]
        )
        record = self._json_of(result, "rollback.json")
        self.assertEqual(record["status"], ROLLBACK_RESTORED_VERIFIED)
        self.assertTrue(record["restoration_executed"])
        self.assertEqual(self._json_of(result, "campaign.json")["status"], "ABORTED")
        self.assertNotIn("measurement.json", self._files(result)[1])
        self.assertEqual(find_unresolved_faults(self.fault_base), [])

    def test_unresolved_fault_blocks_next_run(self):
        self._surface("performance")
        self._host(fail_sets={"performance"})
        broken = self._run()
        self.assertEqual(broken.status, "ABORTED")
        self.assertEqual(broken.verdict, "REFUTED")
        record = self._json_of(broken, "rollback.json")
        self.assertEqual(record["status"], ROLLBACK_FAILED_STATE_DIFF)
        self.assertEqual(record["verified_state"], record["close_state"])

        faults = find_unresolved_faults(self.fault_base)
        self.assertEqual(len(faults), 1)
        self.assertEqual(faults[0]["status"], FAULT_UNRESOLVED)
        self.assertEqual(faults[0]["fault_id"], broken.campaign_id)
        self.assertEqual(faults[0]["expected_restored_state"], "performance")
        self.assertEqual(faults[0]["restoration_command"], "powerprofilesctl set performance")
        self.assertIn(f"resolve-fault {broken.campaign_id}", " ".join(faults[0]["remediation"]))

        reported, code = self._list_faults()
        self.assertEqual(code, 1)
        self.assertEqual(reported["count"], 1)

        healthy = self._host()
        blocked = self._run()
        self.assertEqual(blocked.status, "DENIED")
        self.assertIn("Emergency fault state is unresolved", blocked.reason)
        self.assertIsNone(blocked.evidence_dir)
        self.assertEqual(healthy.commands, [])

        # Synthetic campaigns consult the same state before anything else.
        synthetic = SyntheticExperimentRunner(
            evidence_base=self.evidence_base.parent.parent / "synthetic-experiments",
            repository_root=REPOSITORY_ROOT,
            fault_base=self.fault_base,
        ).run(SyntheticExperimentPlan.fixed_repeatability_plan())
        self.assertEqual(synthetic.status, "DENIED")
        self.assertIn("Emergency fault state is unresolved", synthetic.reason)

        # Resolving verifies the host and never writes it.
        runner = self._runner()
        resolved = runner.resolve(faults[0]["fault_id"])
        self.assertEqual(resolved["status"], FAULT_RESOLVED)
        self.assertEqual(resolved["resolved_profile"], "performance")
        self.assertEqual(healthy.commands, [GET])
        self.assertEqual(find_unresolved_faults(self.fault_base), [])

        reported, code = self._list_faults()
        self.assertIsNone(code)
        self.assertEqual(reported["count"], 0)

        resumed = self._run()
        self.assertEqual(resumed.status, "COMPLETED", resumed.reason)

    def test_resolve_fault_requires_the_owed_profile(self):
        expected_state = self._surface("performance")
        # The profile is wrong while the monitored surface already matches what the
        # fault owes, so only the profile rule can refuse this resolution.
        host = self._host("balanced", mirror=False)
        write_fault_record(
            {
                "schema_version": 1,
                "fault_id": "a" * 32,
                "recorded_at": "2026-11-06T00:00:00+00:00",
                "status": FAULT_UNRESOLVED,
                "reason": "injected close-out failure",
                "rollback_status": ROLLBACK_FAILED_STATE_DIFF,
                "experiment_id": "TIER1_POWER_PROFILE_BALANCED",
                "campaign_id": "a" * 32,
                "plan_sha256": "b" * 64,
                "expected_state": expected_state,
                "observed_state": None,
                "restoration_command": "powerprofilesctl set performance",
                "expected_restored_state": "performance",
                "remediation": ["powerprofilesctl get"],
                "evidence_dir": None,
                "resolved_at": None,
                "resolved_state": None,
                "resolved_profile": None,
            },
            base=self.fault_base,
        )
        runner = self._runner()
        with self.assertRaises(FaultStateError) as refused:
            runner.resolve("a" * 32)
        self.assertIn("not the restored state", str(refused.exception))
        self.assertEqual(host.commands, [GET])
        self.assertEqual(find_unresolved_faults(self.fault_base)[0]["status"], FAULT_UNRESOLVED)

        host.profile = "performance"
        self.assertEqual(runner.resolve("a" * 32)["status"], FAULT_RESOLVED)
        self.assertEqual(host.commands, [GET, GET])

    def test_run_tier1_refuses_a_host_the_plan_no_longer_describes(self):
        self._surface("performance")
        host = self._host("balanced")
        result = self._run()
        self.assertEqual(result.status, "DENIED")
        self.assertIn("no longer describes this host", result.reason)
        self.assertIsNone(result.evidence_dir)
        self.assertEqual(
            [command for command in host.commands if command[1] == "set"], []
        )
        self.assertEqual(find_unresolved_faults(self.fault_base), [])


if __name__ == "__main__":
    unittest.main()
