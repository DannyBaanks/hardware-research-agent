import unittest
from core.sentinel import SentinelGate, SentinelVerdict

class TestAntiFakeSentinel(unittest.TestCase):
    def setUp(self):
        self.sentinel = SentinelGate()

    def test_tier3_firmware_flashing_is_denied(self):
        plan = {
            "experiment_id": "EXP_FLASH",
            "risk_tier": 3,
            "workload": {"command": "flashrom -p internal -w bios.rom"},
            "rollback_plan": {"restoration_command": "flashrom -w backup.rom", "verification_query": "echo ok"}
        }
        grant = self.sentinel.evaluate_plan(plan, has_human_approval=True)
        self.assertEqual(grant.verdict, SentinelVerdict.DENIED)
        self.assertIn("Tier 3", grant.reason)

    def test_forbidden_smu_write_is_denied(self):
        plan = {
            "experiment_id": "EXP_SMU",
            "risk_tier": 2,
            "workload": {"command": "python3 poke_smn.py 0x03B10A20 0x14"},
            "rollback_plan": {"restoration_command": "echo reset", "verification_query": "echo ok"}
        }
        grant = self.sentinel.evaluate_plan(plan, has_human_approval=True)
        self.assertEqual(grant.verdict, SentinelVerdict.DENIED)
        self.assertIn("forbidden hardware safety pattern", grant.reason)

    def test_msr_write_is_denied(self):
        plan = {
            "experiment_id": "EXP_MSR",
            "risk_tier": 2,
            "workload": {"command": "wrmsr 0x199 0x2400"},
            "rollback_plan": {"restoration_command": "wrmsr 0x199 0x1800", "verification_query": "echo ok"}
        }
        grant = self.sentinel.evaluate_plan(plan, has_human_approval=True)
        self.assertEqual(grant.verdict, SentinelVerdict.DENIED)

    def test_missing_rollback_plan_is_denied(self):
        plan = {
            "experiment_id": "EXP_NO_ROLLBACK",
            "risk_tier": 1,
            "workload": {"command": "powerprofilesctl set performance"},
            "rollback_plan": {}
        }
        grant = self.sentinel.evaluate_plan(plan, has_human_approval=True)
        self.assertEqual(grant.verdict, SentinelVerdict.DENIED)
        self.assertIn("missing mandatory rollback_plan", grant.reason)

    def test_tier1_without_human_approval_is_denied(self):
        plan = {
            "experiment_id": "EXP_NO_HUMAN",
            "risk_tier": 1,
            "workload": {"command": "powerprofilesctl set performance"},
            "rollback_plan": {
                "restoration_command": "powerprofilesctl set power-saver",
                "verification_query": "powerprofilesctl get",
                "expected_restored_state": "power-saver"
            }
        }
        grant = self.sentinel.evaluate_plan(plan, has_human_approval=False)
        self.assertEqual(grant.verdict, SentinelVerdict.DENIED)
        self.assertIn("require explicit human authorization", grant.reason)

    def test_authorized_tier1_is_approved(self):
        plan = {
            "experiment_id": "EXP_SAFE_PROFILE",
            "risk_tier": 1,
            "workload": {"command": "powerprofilesctl set performance"},
            "rollback_plan": {
                "restoration_command": "powerprofilesctl set power-saver",
                "verification_query": "powerprofilesctl get",
                "expected_restored_state": "power-saver"
            }
        }
        grant = self.sentinel.evaluate_plan(plan, has_human_approval=True)
        self.assertEqual(grant.verdict, SentinelVerdict.AUTHORIZED)

if __name__ == "__main__":
    unittest.main()
