import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.authorization import AuthorizationDenied
from core.cli import (
    _load_synthetic_proposal,
    cmd_review_synthetic_proposal,
    cmd_run_synthetic_experiment,
    cmd_show_synthetic_proposal,
)
from core.experiment import SyntheticExperimentPlan
from core.planning import ProposalValidationError, SyntheticExperimentProposal


class TestSyntheticExperimentProposal(unittest.TestCase):
    def setUp(self):
        self.plan = SyntheticExperimentPlan.fixed_repeatability_plan()
        self.proposal = SyntheticExperimentProposal(
            schema_version=1,
            proposal_id="test-repeatability",
            rationale="Check deterministic output for the fixed workload.",
            plan=self.plan,
        )

    def test_round_trip_is_canonical_and_does_not_change_plan_authority(self):
        restored = SyntheticExperimentProposal.from_bytes(
            self.proposal.canonical_bytes()
        )
        self.assertEqual(restored.sha256, self.proposal.sha256)
        self.assertEqual(restored.plan.sha256, self.plan.sha256)
        self.assertEqual(restored.plan.samples, 5)

    def test_unknown_proposal_or_plan_fields_are_rejected(self):
        value = self.proposal.to_dict()
        value["command"] = "not accepted"
        with self.assertRaisesRegex(ProposalValidationError, "only"):
            SyntheticExperimentProposal.from_dict(value)

        value = self.proposal.to_dict()
        value["plan"]["shell"] = "not accepted"
        with self.assertRaises(ProposalValidationError):
            SyntheticExperimentProposal.from_dict(value)

    def test_arbitrary_workload_parameters_and_limits_are_rejected(self):
        value = self.proposal.to_dict()
        value["plan"]["workload_id"] = "caller-workload"
        with self.assertRaises(ProposalValidationError):
            SyntheticExperimentProposal.from_dict(value)

        value = self.proposal.to_dict()
        value["plan"]["samples"] = 100
        with self.assertRaises(ProposalValidationError):
            SyntheticExperimentProposal.from_dict(value)

    def test_invalid_identifier_rationale_and_size_are_rejected(self):
        value = self.proposal.to_dict()
        value["proposal_id"] = "../outside"
        with self.assertRaisesRegex(ProposalValidationError, "proposal_id"):
            SyntheticExperimentProposal.from_dict(value)

        value = self.proposal.to_dict()
        value["rationale"] = " "
        with self.assertRaisesRegex(ProposalValidationError, "rationale"):
            SyntheticExperimentProposal.from_dict(value)

        with self.assertRaisesRegex(ProposalValidationError, "size limit"):
            SyntheticExperimentProposal.from_bytes(
                b" " * (SyntheticExperimentProposal.MAX_PROPOSAL_BYTES + 1)
            )

    def test_review_loader_rejects_symlink_and_non_regular_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            proposal_path = root / "proposal.json"
            proposal_path.write_bytes(self.proposal.canonical_bytes())
            link_path = root / "proposal-link.json"
            link_path.symlink_to(proposal_path)
            with self.assertRaises(OSError):
                _load_synthetic_proposal(str(link_path))

            with self.assertRaises(ProposalValidationError):
                _load_synthetic_proposal(str(root))

    def test_review_loading_is_read_only(self):
        executor = Mock()
        with tempfile.TemporaryDirectory() as temp_dir:
            proposal_path = Path(temp_dir) / "proposal.json"
            proposal_path.write_bytes(self.proposal.canonical_bytes())
            reviewed = _load_synthetic_proposal(str(proposal_path))
        executor.run.assert_not_called()
        self.assertEqual(reviewed.sha256, self.proposal.sha256)

    def test_cli_proposal_export_and_review_do_not_authorize_or_execute(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cmd_show_synthetic_proposal(SimpleNamespace())
        exported = SyntheticExperimentProposal.from_bytes(output.getvalue().encode())
        self.assertEqual(exported.plan.sha256, self.plan.sha256)

        with tempfile.TemporaryDirectory() as temp_dir:
            proposal_path = Path(temp_dir) / "proposal.json"
            proposal_path.write_bytes(exported.canonical_bytes())
            output = io.StringIO()
            with contextlib.redirect_stdout(output), patch(
                "core.cli.SyntheticExperimentRunner"
            ) as runner:
                cmd_review_synthetic_proposal(
                    SimpleNamespace(proposal=str(proposal_path))
                )
            result = json.loads(output.getvalue())

        self.assertTrue(result["eligible"])
        self.assertEqual(result["execution_authority"], "none; review does not authorize or execute")
        runner.assert_not_called()

    def test_cli_denies_when_tty_authorization_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            proposal_path = Path(temp_dir) / "proposal.json"
            proposal_path.write_bytes(self.proposal.canonical_bytes())
            with (
                patch(
                    "core.cli.authorize_plan_from_tty",
                    side_effect=AuthorizationDenied("No controlling TTY is available"),
                ),
                patch("core.cli.SyntheticExperimentRunner") as runner,
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaises(SystemExit) as exit_info,
            ):
                cmd_run_synthetic_experiment(
                    SimpleNamespace(proposal=str(proposal_path))
                )

        self.assertEqual(exit_info.exception.code, 1)
        runner.assert_not_called()

    def test_cli_cancellation_returns_deny_without_starting_campaign(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            proposal_path = Path(temp_dir) / "proposal.json"
            proposal_path.write_bytes(self.proposal.canonical_bytes())
            output = io.StringIO()
            with (
                patch("core.cli.authorize_plan_from_tty", side_effect=KeyboardInterrupt),
                patch("core.cli.SyntheticExperimentRunner") as runner,
                contextlib.redirect_stdout(output),
                self.assertRaises(SystemExit) as exit_info,
            ):
                cmd_run_synthetic_experiment(
                    SimpleNamespace(proposal=str(proposal_path))
                )

        self.assertEqual(exit_info.exception.code, 130)
        self.assertEqual(json.loads(output.getvalue())["verdict"], "DENY")
        runner.assert_not_called()

    def test_duplicate_json_keys_are_rejected(self):
        value = self.proposal.to_dict()
        plan = json.dumps(value["plan"], separators=(",", ":"))
        data = (
            b'{"schema_version":1,"proposal_id":"x","rationale":"reason",'
            + b'"plan":' + plan.encode("ascii")
            + b',"proposal_id":"duplicate"}'
        )
        with self.assertRaises(ProposalValidationError):
            SyntheticExperimentProposal.from_bytes(data)


if __name__ == "__main__":
    unittest.main()
