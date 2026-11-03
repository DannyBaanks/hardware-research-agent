import unittest
from unittest.mock import patch

from core.executor import DryRunExecutor
from core.verifier import VerificationVerdict


class TestDryRunExecutor(unittest.TestCase):
    def setUp(self):
        self.executor = DryRunExecutor()

    def test_d3cold_fixture_replays_as_refuted_without_host_actions(self):
        with patch("subprocess.run") as run:
            receipt = self.executor.replay_fixture("ada_d3cold_false_positive")

        self.assertEqual(receipt.replay_status, "COMPLETED")
        self.assertEqual(receipt.verdict, VerificationVerdict.REFUTED.value)
        self.assertTrue(receipt.simulation_only)
        self.assertFalse(receipt.hardware_accessed)
        self.assertEqual(receipt.commands_executed, 0)
        self.assertFalse(receipt.system_state_changed)
        self.assertEqual(receipt.rollback_status, "NOT_REQUIRED_NO_STATE_CHANGE")
        self.assertIn("NO_HOST_ACTIONS_PERFORMED", receipt.trace)
        run.assert_not_called()

    def test_no_safe_gain_fixture_replays_without_host_actions(self):
        with patch("subprocess.run") as run:
            receipt = self.executor.replay_fixture("no_safe_gain_found")

        self.assertEqual(receipt.verdict, VerificationVerdict.NOT_DEMONSTRATED.value)
        self.assertFalse(receipt.hardware_accessed)
        self.assertFalse(receipt.system_state_changed)
        run.assert_not_called()

    def test_unknown_fixture_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown fixture"):
            self.executor.replay_fixture("../../core/cli")


if __name__ == "__main__":
    unittest.main()
