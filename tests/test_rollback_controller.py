import unittest
from unittest.mock import patch, Mock
from core.rollback import RollbackController

class TestRollbackController(unittest.TestCase):
    def setUp(self):
        self.controller = RollbackController()

    def test_successful_mock_rollback(self):
        with patch("core.rollback.subprocess.run", return_value=Mock(stdout="power-saver\n")) as run:
            receipt = self.controller.execute_and_verify_rollback(
                experiment_id="TEST_EXP_01",
                restore_cmd="powerprofilesctl set power-saver",
                verify_query="powerprofilesctl get",
                expected_state="power-saver",
            )
        self.assertTrue(receipt.success)
        self.assertEqual(receipt.verified_state, "power-saver")
        self.assertIsNone(receipt.error_message)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args[0], ("powerprofilesctl", "set", "power-saver"))

    def test_failed_state_verification_is_flagged(self):
        # Verification query returns 'performance' when 'power-saver' was expected
        with patch("core.rollback.subprocess.run", return_value=Mock(stdout="performance\n")):
            receipt = self.controller.execute_and_verify_rollback(
                experiment_id="TEST_EXP_FAIL",
                restore_cmd="powerprofilesctl set power-saver",
                verify_query="powerprofilesctl get",
                expected_state="power-saver",
            )
        self.assertFalse(receipt.success)
        self.assertIn("State mismatch", receipt.error_message)

    def test_shell_metacharacters_are_rejected_without_execution(self):
        with patch("core.rollback.subprocess.run") as run:
            receipt = self.controller.execute_and_verify_rollback(
                experiment_id="TEST_EXP_INJECTION",
                restore_cmd="powerprofilesctl set power-saver; touch /tmp/ihra",
                verify_query="powerprofilesctl get",
                expected_state="power-saver",
            )
        self.assertFalse(receipt.success)
        self.assertIn("Restore command must set", receipt.error_message)
        run.assert_not_called()

if __name__ == "__main__":
    unittest.main()
