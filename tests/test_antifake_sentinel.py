import unittest

from core.sentinel import SentinelGate, SentinelVerdict


class TestAntiFakeSentinel(unittest.TestCase):
    def setUp(self):
        self.sentinel = SentinelGate()

    def evaluate(self, command, **extra):
        return self.sentinel.evaluate_plan({
            "experiment_id": "SENTINEL_TEST",
            "workload": {"command": command},
            **extra,
        })

    def test_tier3_firmware_flashing_is_denied_even_if_caller_claims_tier0(self):
        grant = self.evaluate("flashrom -p internal -w bios.rom", risk_tier=0)
        self.assertEqual(grant.verdict, SentinelVerdict.DENIED)
        self.assertEqual(grant.risk_tier, 3)

    def test_forbidden_smu_write_is_denied(self):
        grant = self.evaluate("python3 poke_smn.py 0x03B10A20 0x14")
        self.assertEqual(grant.verdict, SentinelVerdict.DENIED)
        self.assertEqual(grant.risk_tier, 3)

    def test_msr_write_is_denied(self):
        grant = self.evaluate("wrmsr 0x199 0x2400")
        self.assertEqual(grant.verdict, SentinelVerdict.DENIED)

    def test_shell_metacharacters_and_unlisted_commands_are_denied(self):
        for command in (
            "lscpu; rm -rf /",
            "python3 benchmark.py",
            "lscpu && nvidia-smi",
            "nvidia-smi --query-gpu=uuid",
        ):
            with self.subTest(command=command):
                grant = self.evaluate(command)
                self.assertEqual(grant.verdict, SentinelVerdict.DENIED)

    def test_exact_read_only_allowlist_is_authorized(self):
        grant = self.evaluate("powerprofilesctl get", risk_tier=0)
        self.assertEqual(grant.verdict, SentinelVerdict.AUTHORIZED)
        self.assertEqual(grant.allowed_operations, ["powerprofilesctl get"])

    def test_caller_boolean_cannot_authorize_state_change(self):
        plan = {
            "experiment_id": "EXP_PROFILE",
            "risk_tier": 1,
            "workload": {"command": "powerprofilesctl set performance"},
            "rollback_plan": {
                "restoration_command": "powerprofilesctl set power-saver",
                "verification_query": "powerprofilesctl get",
                "expected_restored_state": "power-saver",
            },
        }
        with self.assertRaises(TypeError):
            self.sentinel.evaluate_plan(plan, has_human_approval=True)
        grant = self.sentinel.evaluate_plan(plan)
        self.assertEqual(grant.verdict, SentinelVerdict.DENIED)
        self.assertIn("authenticated human authorization", grant.reason)

    def test_mismatched_caller_risk_tier_is_denied(self):
        grant = self.evaluate("lscpu", risk_tier=3)
        self.assertEqual(grant.verdict, SentinelVerdict.DENIED)
        self.assertEqual(grant.risk_tier, 0)


if __name__ == "__main__":
    unittest.main()
