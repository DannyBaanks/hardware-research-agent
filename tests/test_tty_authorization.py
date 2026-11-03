import os
import tempfile
import unittest
from unittest.mock import patch

from core.authorization import (
    AuthorizationDenied,
    PlanAuthorization,
    authorize_plan_from_tty,
)
from core.experiment import SyntheticExperimentPlan

from tests.tty_test_utils import authorize_in_pty


class TestTtyAuthorization(unittest.TestCase):
    def setUp(self):
        plan = SyntheticExperimentPlan.fixed_repeatability_plan()
        self.plan_sha256 = plan.sha256
        self.proposal_sha256 = "a" * 64

    def test_matching_local_tty_confirmation_issues_single_use_grant(self):
        grant = authorize_in_pty(self.plan_sha256, self.proposal_sha256)
        self.assertEqual(
            grant.consume(self.plan_sha256, self.proposal_sha256),
            self.proposal_sha256,
        )
        with self.assertRaisesRegex(AuthorizationDenied, "already been used"):
            grant.consume(self.plan_sha256, self.proposal_sha256)

    def test_wrong_digest_cancel_and_eof_are_denied(self):
        with self.assertRaisesRegex(AuthorizationDenied, "did not match"):
            authorize_in_pty(
                self.plan_sha256, self.proposal_sha256, answer="no"
            )

        with self.assertRaisesRegex(AuthorizationDenied, "did not match"):
            authorize_in_pty(
                self.plan_sha256,
                self.proposal_sha256,
                answer="AUTHORIZE " + "0" * 64,
            )

        with self.assertRaises(AuthorizationDenied):
            authorize_in_pty(
                self.plan_sha256, self.proposal_sha256, answer=""
            )

    def test_no_tty_and_invalid_digest_are_denied(self):
        with tempfile.TemporaryFile() as non_tty:
            with patch(
                "core.authorization.os.open",
                return_value=os.dup(non_tty.fileno()),
            ):
                with self.assertRaisesRegex(AuthorizationDenied, "not a TTY"):
                    authorize_plan_from_tty(
                        self.plan_sha256, self.proposal_sha256, "{}"
                    )

        with self.assertRaisesRegex(AuthorizationDenied, "plan digest"):
            authorize_plan_from_tty(
                "z" * 64, self.proposal_sha256, "{}"
            )

    def test_constructor_cannot_issue_authorization_without_tty_gate(self):
        with self.assertRaisesRegex(AuthorizationDenied, "local TTY"):
            PlanAuthorization(self.plan_sha256, self.proposal_sha256)

    def test_grant_cannot_be_consumed_for_another_plan(self):
        grant = authorize_in_pty(self.plan_sha256, self.proposal_sha256)
        with self.assertRaisesRegex(AuthorizationDenied, "does not match"):
            grant.consume("0" * 64, self.proposal_sha256)
        with self.assertRaisesRegex(AuthorizationDenied, "already been used"):
            grant.consume(self.plan_sha256, self.proposal_sha256)

    def test_grant_cannot_be_consumed_for_another_proposal(self):
        grant = authorize_in_pty(self.plan_sha256, self.proposal_sha256)
        with self.assertRaisesRegex(AuthorizationDenied, "proposal digest"):
            grant.consume(self.plan_sha256, "b" * 64)
        with self.assertRaisesRegex(AuthorizationDenied, "already been used"):
            grant.consume(self.plan_sha256, self.proposal_sha256)

    def test_tty_authorization_displays_the_proposal_being_approved(self):
        summary = '{"proposal_id":"approved-proposal"}'
        _, output = authorize_in_pty(
            self.plan_sha256, self.proposal_sha256, summary, capture=True
        )
        self.assertIn(summary.encode("ascii"), output)
        self.assertIn(self.plan_sha256.encode("ascii"), output)


if __name__ == "__main__":
    unittest.main()
