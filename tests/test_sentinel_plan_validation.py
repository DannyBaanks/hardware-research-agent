import json
import unittest
from pathlib import Path
from unittest import mock

from core.sentinel import (
    CONTROLS_FIELDS,
    EXPERIMENT_PLAN_REQUIRED,
    ROLLBACK_PLAN_FIELDS,
    TARGET_HARDWARE_FIELDS,
    TARGET_HARDWARE_OPTIONAL,
    WORKLOAD_FIELDS,
    SentinelGate,
    SentinelVerdict,
    experiment_plan_issues,
    rollback_plan_issues,
)


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "experiment.schema.json"

VALID_ROLLBACK = {
    "restoration_command": "powerprofilesctl set performance",
    "verification_query": "powerprofilesctl get",
    "expected_restored_state": "performance",
}

TIER1_PLAN = {
    "experiment_id": "CPU-B1",
    "workload": {"command": "powerprofilesctl set balanced"},
    "rollback_plan": VALID_ROLLBACK,
}

COMPLETE_PLAN = {
    "experiment_id": "CPU-B1",
    "target_hardware": {"pci_id": "1022:1507", "device_family": "Zen 4"},
    "hypothesis_id": "H-boost-1",
    "risk_tier": 1,
    "primary_variable": "median tokens per second",
    "predicted_delta": "+5% or none",
    "workload": {"command": "powerprofilesctl set balanced", "repetitions": 5, "timeout_sec": 8},
    "controls": {
        "negative_control": "same workload with the profile restored",
        "sequence_pattern": "counterbalanced_alternating",
        "output_hash_check": True,
    },
    "rollback_plan": VALID_ROLLBACK,
}


class TestSentinelRollbackObligation(unittest.TestCase):
    def setUp(self):
        self.gate = SentinelGate()

    def test_missing_rollback_plan_is_denied(self):
        plan = {k: v for k, v in COMPLETE_PLAN.items() if k != "rollback_plan"}
        grant = self.gate.evaluate_plan(plan)
        self.assertEqual(grant.verdict, SentinelVerdict.DENIED)
        self.assertIn("rollback_plan", grant.reason)

    def test_unverifiable_rollback_plan_is_denied(self):
        for restoration in (
            "powerprofilesctl set turbo",
            "sudo powerprofilesctl set performance",
            "rm -rf /tmp/x",
            "powerprofilesctl set performance && lscpu",
        ):
            plan = dict(
                COMPLETE_PLAN, rollback_plan=dict(VALID_ROLLBACK, restoration_command=restoration)
            )
            grant = self.gate.evaluate_plan(plan)
            self.assertEqual(grant.verdict, SentinelVerdict.DENIED, restoration)
            self.assertIn("not verifiable", grant.reason)

    def test_non_string_rollback_fields_are_denied(self):
        for field, value in (
            ("restoration_command", None),
            ("expected_restored_state", 7),
            ("expected_restored_state", "  "),
        ):
            plan = dict(
                COMPLETE_PLAN, rollback_plan=dict(VALID_ROLLBACK, **{field: value})
            )
            grant = self.gate.evaluate_plan(plan)
            self.assertEqual(grant.verdict, SentinelVerdict.DENIED, field)
            self.assertIn("rollback_plan", grant.reason)

    def test_unserializable_plan_is_denied_before_any_binding(self):
        plan = dict(
            COMPLETE_PLAN, rollback_plan=dict(VALID_ROLLBACK, verification_query=b"x")
        )
        grant = self.gate.evaluate_plan(plan)
        self.assertEqual(grant.verdict, SentinelVerdict.DENIED)
        self.assertIn("canonically serializable", grant.reason)

    def test_rollback_plan_shape_is_exact(self):
        for broken in (
            {k: v for k, v in VALID_ROLLBACK.items() if k != "verification_query"},
            dict(VALID_ROLLBACK, extra_field="powerprofilesctl get"),
            "powerprofilesctl set performance",
        ):
            grant = self.gate.evaluate_plan(dict(COMPLETE_PLAN, rollback_plan=broken))
            self.assertEqual(grant.verdict, SentinelVerdict.DENIED)

    def test_incomplete_state_changing_plan_is_denied(self):
        # Bare command plus rollback is not an experiment plan: the required fields
        # carry the hypothesis and controls the state change is supposed to be tested.
        grant = self.gate.evaluate_plan(TIER1_PLAN)
        self.assertEqual(grant.verdict, SentinelVerdict.DENIED)
        self.assertIn("complete, verifiable experiment plan", grant.reason)
        self.assertIn("missing", grant.reason)

    def test_read_only_plan_needs_no_rollback(self):
        # Tier 0 restores nothing; SAFETY_CONTRACTS.md 2 and 4 attach the obligation
        # to state changes only, so a read-only command stays on the allowlist path.
        grant = self.gate.evaluate_plan(
            {"experiment_id": "G-0", "workload": {"command": "lscpu"}}
        )
        self.assertEqual(grant.verdict, SentinelVerdict.AUTHORIZED)

    def test_valid_rollback_plan_is_still_denied_without_a_human(self):
        grant = self.gate.evaluate_plan(COMPLETE_PLAN)
        self.assertEqual(grant.verdict, SentinelVerdict.DENIED)
        self.assertIn("unattended Tier-1 execution is disabled by decision", grant.reason)

    def test_verifiable_rollback_is_decided_without_executing_anything(self):
        def forbid(*args, **kwargs):
            raise AssertionError("Sentinel must not execute while judging a rollback plan")

        with mock.patch("core.rollback.subprocess.run", side_effect=forbid):
            grant = self.gate.evaluate_plan(COMPLETE_PLAN)
        self.assertEqual(grant.allowed_operations, [])
        self.assertEqual(rollback_plan_issues(VALID_ROLLBACK), [])


class TestExperimentPlanValidator(unittest.TestCase):
    def test_complete_plan_is_accepted(self):
        self.assertEqual(experiment_plan_issues(COMPLETE_PLAN), [])

    def test_minimal_plan_without_optional_fields_is_accepted(self):
        plan = {k: v for k, v in COMPLETE_PLAN.items() if k != "predicted_delta"}
        self.assertEqual(experiment_plan_issues(plan), [])

    def test_missing_and_unknown_fields_are_reported(self):
        issues = experiment_plan_issues({"experiment_id": "A"})
        self.assertTrue(any("missing" in issue for issue in issues))
        issues = experiment_plan_issues(dict(COMPLETE_PLAN, unscripted_field="x"))
        self.assertTrue(any("unknown fields" in issue for issue in issues))

    def test_field_rules_are_enforced(self):
        cases = {
            "experiment_id": "lower-case-id",
            "risk_tier": True,
            "hypothesis_id": "   ",
            "primary_variable": 5,
        }
        for field, value in cases.items():
            issues = experiment_plan_issues(dict(COMPLETE_PLAN, **{field: value}))
            self.assertTrue(issues, field)

    def test_numeric_and_enum_rules_are_enforced(self):
        for workload in (
            {"command": "powerprofilesctl set balanced", "repetitions": 2, "timeout_sec": 8},
            {"command": "powerprofilesctl set balanced", "repetitions": "5", "timeout_sec": 8},
            {"command": "powerprofilesctl set balanced", "repetitions": 5, "timeout_sec": 0.5},
            {"command": "", "repetitions": 5, "timeout_sec": 8},
        ):
            issues = experiment_plan_issues(dict(COMPLETE_PLAN, workload=workload))
            self.assertTrue(issues, workload)
        for controls in (
            dict(COMPLETE_PLAN["controls"], sequence_pattern="whenever_it_helps"),
            dict(COMPLETE_PLAN["controls"], output_hash_check="yes"),
            dict(COMPLETE_PLAN["controls"], negative_control=""),
        ):
            issues = experiment_plan_issues(dict(COMPLETE_PLAN, controls=controls))
            self.assertTrue(issues, controls)
        self.assertTrue(experiment_plan_issues(42))

    def test_target_hardware_rules_are_enforced(self):
        issues = experiment_plan_issues(
            dict(COMPLETE_PLAN, target_hardware={"pci_id": "1022:1507"})
        )
        self.assertTrue(any("device_family" in issue for issue in issues))

    def test_schema_required_keys_match_validator(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(set(EXPERIMENT_PLAN_REQUIRED), set(schema["required"]))
        properties = schema["properties"]
        for label, fields in (
            ("target_hardware", TARGET_HARDWARE_FIELDS),
            ("workload", WORKLOAD_FIELDS),
            ("controls", CONTROLS_FIELDS),
            ("rollback_plan", ROLLBACK_PLAN_FIELDS),
        ):
            self.assertEqual(set(fields), set(properties[label]["required"]), label)
            # Anything the schema declares but does not require must be a declared
            # optional of the validator, or the mirror has drifted.
            extra = set(properties[label]["properties"]) - set(fields)
            declared = {"target_hardware": set(TARGET_HARDWARE_OPTIONAL)}.get(label, set())
            self.assertEqual(extra, declared, f"{label} optional fields")
        self.assertEqual(properties["workload"]["properties"]["repetitions"]["minimum"], 3)
        self.assertEqual(properties["workload"]["properties"]["timeout_sec"]["minimum"], 1)
        self.assertEqual(set(properties["risk_tier"]["enum"]), {0, 1, 2, 3})
        self.assertEqual(
            set(properties["controls"]["properties"]["sequence_pattern"]["enum"]),
            {"counterbalanced_alternating", "single_group"},
        )
        self.assertEqual(
            set(properties.keys()) - set(EXPERIMENT_PLAN_REQUIRED), {"predicted_delta"}
        )


if __name__ == "__main__":
    unittest.main()
