import unittest
from core.rollback import RollbackController

class TestRollbackController(unittest.TestCase):
    def setUp(self):
        self.controller = RollbackController()

    def test_successful_mock_rollback(self):
        # Restoration command echo, verification query echo matching expected state
        receipt = self.controller.execute_and_verify_rollback(
            experiment_id="TEST_EXP_01",
            restore_cmd="true",
            verify_query="echo 'power-saver'",
            expected_state="power-saver"
        )
        self.assertTrue(receipt.success)
        self.assertEqual(receipt.verified_state, "power-saver")
        self.assertIsNone(receipt.error_message)

    def test_failed_state_verification_is_flagged(self):
        # Verification query returns 'performance' when 'power-saver' was expected
        receipt = self.controller.execute_and_verify_rollback(
            experiment_id="TEST_EXP_FAIL",
            restore_cmd="true",
            verify_query="echo 'performance'",
            expected_state="power-saver"
        )
        self.assertFalse(receipt.success)
        self.assertIn("State mismatch", receipt.error_message)

if __name__ == "__main__":
    unittest.main()
