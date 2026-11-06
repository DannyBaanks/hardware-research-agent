import hashlib
import hmac
import json
import re
import secrets
import shlex
import time
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from .authorization import PlanAuthorization
from .rollback import RollbackController


class SentinelVerdict(Enum):
    AUTHORIZED = "AUTHORIZED"
    DENIED = "DENIED"


# Contract: docs/SAFETY_CONTRACTS.md 6.2. The key lives only in this process, so a
# token proves a grant was not rebuilt or reordered here; it proves nothing about
# who holds it, and a same-UID process can read it out of memory.
_GRANT_KEY = secrets.token_bytes(32)
GRANT_TTL_SECONDS = 120

# The single state change IHRA can ever be granted, and only with a human at the TTY.
_TIER1_PROFILE_COMMAND = re.compile(
    r"^powerprofilesctl\s+set\s+(?:power-saver|balanced|performance)$"
)
TIER1_REVIEW_REASON = (
    "State changes require a live local TTY authorization bound to this exact plan "
    "and rollback; unattended Tier-1 execution is disabled by decision, not by omission"
)


def canonical_plan_digest(plan: Dict[str, Any]) -> Optional[str]:
    try:
        canonical = json.dumps(
            plan, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode("ascii")
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(canonical).hexdigest()


def _grant_token(plan_sha256: str, experiment_id: str, deadline: float) -> str:
    message = f"{plan_sha256}\0{experiment_id}\0{deadline!r}".encode("ascii")
    return hmac.new(_GRANT_KEY, message, hashlib.sha256).hexdigest()


def tier1_intervention(plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The exact host change a tier-1 plan performs, plus what is owed to undo it.

    Derived only from bytes the plan already declared: the caller cannot hand the
    gate a friendlier intervention than the one a human was shown.
    """
    workload = plan.get("workload")
    command = workload.get("command") if isinstance(workload, dict) else None
    if not isinstance(command, str) or not _TIER1_PROFILE_COMMAND.fullmatch(command):
        return None
    rollback = plan.get("rollback_plan")
    if not isinstance(rollback, dict) or rollback_plan_issues(rollback):
        return None
    profile = command.split()[-1]
    if rollback["expected_restored_state"] not in RollbackController.PROFILES:
        return None
    return {
        "applied_command": command,
        "target_profile": profile,
        "restoration_command": rollback["restoration_command"],
        "verification_query": rollback["verification_query"],
        "expected_restored_state": rollback["expected_restored_state"],
    }


def tier1_intervention_digest(plan: Dict[str, Any]) -> Optional[str]:
    intervention = tier1_intervention(plan)
    if intervention is None:
        return None
    return hashlib.sha256(_canonical_intervention_bytes(intervention)).hexdigest()


def _canonical_intervention_bytes(intervention: Dict[str, Any]) -> bytes:
    return json.dumps(
        intervention, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


@dataclass
class AuthorizationGrant:
    verdict: SentinelVerdict
    experiment_id: str
    risk_tier: int
    reason: str
    allowed_operations: List[str]
    plan_sha256: Optional[str] = None
    issued_at: Optional[float] = None
    expires_at: Optional[float] = None
    grant_token: Optional[str] = None


# Mirrors schemas/experiment.schema.json. A test asserts the two stay equal, so the
# schema stays declarative documentation rather than a runtime dependency.
EXPERIMENT_PLAN_REQUIRED = (
    "experiment_id",
    "target_hardware",
    "hypothesis_id",
    "risk_tier",
    "primary_variable",
    "workload",
    "controls",
    "rollback_plan",
)
EXPERIMENT_PLAN_OPTIONAL = ("predicted_delta",)
TARGET_HARDWARE_FIELDS = ("pci_id", "device_family")
TARGET_HARDWARE_OPTIONAL = ("subsystem_id",)
WORKLOAD_FIELDS = ("command", "repetitions", "timeout_sec")
CONTROLS_FIELDS = ("negative_control", "sequence_pattern", "output_hash_check")
ROLLBACK_PLAN_FIELDS = (
    "restoration_command",
    "verification_query",
    "expected_restored_state",
)
SEQUENCE_PATTERNS = ("counterbalanced_alternating", "single_group")
WORKLOAD_MIN_REPETITIONS = 3
WORKLOAD_MIN_TIMEOUT_SECONDS = 1
EXPERIMENT_ID_PATTERN = re.compile(r"^[A-Z0-9_-]+$")


def _non_empty_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _exact_keys(value, fields: Tuple[str, ...], label: str, optional=()) -> List[str]:
    if not isinstance(value, dict):
        return [f"{label} must be an object"]
    issues = []
    missing = sorted(set(fields) - set(value))
    unknown = sorted(set(value) - set(fields) - set(optional))
    if missing:
        issues.append(f"{label} is missing {', '.join(missing)}")
    if unknown:
        issues.append(f"{label} has unknown fields {', '.join(unknown)}")
    return issues


def rollback_plan_issues(rollback_plan) -> List[str]:
    """Reports why a rollback plan cannot be verified, without executing anything."""
    issues = _exact_keys(rollback_plan, ROLLBACK_PLAN_FIELDS, "rollback_plan")
    if issues:
        return issues
    for field in ROLLBACK_PLAN_FIELDS:
        if not _non_empty_text(rollback_plan[field]):
            issues.append(f"rollback_plan.{field} must be a non-empty string")
    if issues:
        return issues
    try:
        RollbackController._validate_restore(
            rollback_plan["restoration_command"],
            rollback_plan["expected_restored_state"],
        )
    except ValueError as exc:
        issues.append(f"rollback_plan.restoration_command is not verifiable: {exc}")
    try:
        RollbackController._validate_query(rollback_plan["verification_query"])
    except ValueError as exc:
        issues.append(f"rollback_plan.verification_query is not verifiable: {exc}")
    return issues


def experiment_plan_issues(plan) -> List[str]:
    """Validates a complete experiment plan against the schema's rules."""
    if not isinstance(plan, dict):
        return ["plan must be an object"]
    issues = []
    missing = sorted(set(EXPERIMENT_PLAN_REQUIRED) - set(plan))
    unknown = sorted(set(plan) - set(EXPERIMENT_PLAN_REQUIRED) - set(EXPERIMENT_PLAN_OPTIONAL))
    if missing:
        issues.append(f"plan is missing {', '.join(missing)}")
    if unknown:
        issues.append(f"plan has unknown fields {', '.join(unknown)}")

    experiment_id = plan.get("experiment_id")
    if not isinstance(experiment_id, str) or not EXPERIMENT_ID_PATTERN.fullmatch(experiment_id):
        issues.append("experiment_id must match ^[A-Z0-9_-]+$")
    risk_tier = plan.get("risk_tier")
    if isinstance(risk_tier, bool) or not isinstance(risk_tier, int) or risk_tier not in range(4):
        issues.append("risk_tier must be an integer from 0 to 3")
    for field in ("hypothesis_id", "primary_variable"):
        if not _non_empty_text(plan.get(field)):
            issues.append(f"{field} must be a non-empty string")
    predicted = plan.get("predicted_delta")
    if predicted is not None and not _non_empty_text(predicted):
        issues.append("predicted_delta must be a non-empty string")

    target = plan.get("target_hardware")
    issues += _exact_keys(
        target, TARGET_HARDWARE_FIELDS, "target_hardware", TARGET_HARDWARE_OPTIONAL
    )
    if isinstance(target, dict):
        for field in TARGET_HARDWARE_FIELDS + TARGET_HARDWARE_OPTIONAL:
            if field in target and not _non_empty_text(target[field]):
                issues.append(f"target_hardware.{field} must be a non-empty string")

    workload = plan.get("workload")
    issues += _exact_keys(workload, WORKLOAD_FIELDS, "workload")
    if isinstance(workload, dict):
        command = workload.get("command")
        if not _non_empty_text(command) or "\x00" in str(command):
            issues.append("workload.command must be a non-empty string without NUL bytes")
        repetitions = workload.get("repetitions")
        if (
            isinstance(repetitions, bool)
            or not isinstance(repetitions, int)
            or repetitions < WORKLOAD_MIN_REPETITIONS
        ):
            issues.append(f"workload.repetitions must be at least {WORKLOAD_MIN_REPETITIONS}")
        timeout = workload.get("timeout_sec")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or timeout < WORKLOAD_MIN_TIMEOUT_SECONDS
        ):
            issues.append(f"workload.timeout_sec must be at least {WORKLOAD_MIN_TIMEOUT_SECONDS}")

    controls = plan.get("controls")
    issues += _exact_keys(controls, CONTROLS_FIELDS, "controls")
    if isinstance(controls, dict):
        if not _non_empty_text(controls.get("negative_control")):
            issues.append("controls.negative_control must be a non-empty string")
        if controls.get("sequence_pattern") not in SEQUENCE_PATTERNS:
            issues.append(f"controls.sequence_pattern must be one of {', '.join(SEQUENCE_PATTERNS)}")
        if not isinstance(controls.get("output_hash_check"), bool):
            issues.append("controls.output_hash_check must be a boolean")

    if "rollback_plan" in plan:
        issues += rollback_plan_issues(plan["rollback_plan"])
    return issues


class SentinelGate:
    """Fail-closed classifier for a deliberately small set of read-only commands."""

    READ_ONLY_COMMANDS = {
        ("lscpu",),
        ("lspci",),
        ("sensors",),
        ("powerprofilesctl", "get"),
        ("nvidia-smi",),
    }
    TIER1_PROFILE_COMMAND = _TIER1_PROFILE_COMMAND
    FORBIDDEN_PATTERNS = (
        re.compile(r"\b(flashrom|nvramtool|fwupdtool|wrmsr|rdmsr)\b", re.IGNORECASE),
        re.compile(r"\b(0x03b10[a-f0-9]+|smn|smu)(?:_write|_message)?\b", re.IGNORECASE),
        re.compile(r"/dev/cpu/\d+/msr", re.IGNORECASE),
        re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
        re.compile(r">\s*/sys/(?:firmware|devices)/", re.IGNORECASE),
    )

    @staticmethod
    def _deny(experiment_id: str, risk_tier: int, reason: str) -> AuthorizationGrant:
        return AuthorizationGrant(
            verdict=SentinelVerdict.DENIED,
            experiment_id=experiment_id,
            risk_tier=risk_tier,
            reason=reason,
            allowed_operations=[],
        )

    def _review_static(
        self, plan: Dict[str, Any]
    ) -> Tuple[Optional[AuthorizationGrant], Optional[str], Optional[str], Optional[int], Optional[str]]:
        """Applies every rule that does not depend on who is asking.

        The returned denial is what an unattended caller is given. A Tier-1 plan
        still has to pass the human leg in evaluate_tier1() before anything runs,
        so nothing here may execute or grant.
        """
        if not isinstance(plan, dict):
            return (
                self._deny("UNKNOWN_EXP", 3, "Experiment plan must be an object"),
                None,
                None,
                None,
                None,
            )

        exp_id = plan.get("experiment_id", "UNKNOWN_EXP")
        if not isinstance(exp_id, str) or not exp_id:
            exp_id = "UNKNOWN_EXP"

        plan_digest = canonical_plan_digest(plan)
        if plan_digest is None:
            return (
                self._deny(exp_id, 3, "Plan is not canonically serializable"),
                None,
                None,
                None,
                None,
            )

        workload = plan.get("workload")
        command = workload.get("command") if isinstance(workload, dict) else None
        if not isinstance(command, str) or not command or "\x00" in command:
            return (
                self._deny(exp_id, 3, "Plan must contain a non-empty command string"),
                None,
                None,
                None,
                None,
            )

        try:
            argv = tuple(shlex.split(command, posix=True))
        except ValueError:
            return (
                self._deny(exp_id, 3, "Malformed command string"),
                None,
                None,
                None,
                None,
            )

        if any(pattern.search(command) for pattern in self.FORBIDDEN_PATTERNS):
            return (
                self._deny(
                    exp_id, 3, "Command matches a forbidden hardware or destructive-operation rule"
                ),
                None,
                None,
                None,
                None,
            )

        requested_tier = plan.get("risk_tier")
        if requested_tier is not None and (
            isinstance(requested_tier, bool)
            or not isinstance(requested_tier, int)
            or requested_tier not in range(4)
        ):
            return (
                self._deny(exp_id, 3, "risk_tier must be an integer from 0 to 3"),
                None,
                None,
                None,
                None,
            )

        if argv in self.READ_ONLY_COMMANDS and command == " ".join(argv):
            derived_tier = 0
        elif self.TIER1_PROFILE_COMMAND.fullmatch(command):
            derived_tier = 1
        else:
            derived_tier = 3

        if requested_tier is not None and requested_tier != derived_tier:
            return (
                self._deny(
                    exp_id,
                    derived_tier,
                    f"Caller-supplied risk_tier does not match the command's derived tier ({derived_tier})",
                ),
                None,
                None,
                None,
                None,
            )

        if derived_tier == 1:
            # A state change is only evaluable as a complete experiment plan: the
            # schema's required fields and a rollback the controller can verify are
            # both load-bearing before a human is even asked (SAFETY_CONTRACTS.md 2, 4).
            issues = experiment_plan_issues(plan)
            if issues:
                return (
                    self._deny(
                        exp_id,
                        1,
                        "State-changing plan must be a complete, verifiable experiment plan: "
                        + "; ".join(issues),
                    ),
                    None,
                    None,
                    None,
                    None,
                )

        return None, exp_id, plan_digest, derived_tier, command

    def evaluate_plan(self, plan: Dict[str, Any]) -> AuthorizationGrant:
        denial, exp_id, plan_digest, derived_tier, command = self._review_static(plan)
        if denial is not None:
            return denial

        if derived_tier == 0:
            issued_at = time.monotonic()
            deadline = issued_at + GRANT_TTL_SECONDS
            return AuthorizationGrant(
                verdict=SentinelVerdict.AUTHORIZED,
                experiment_id=exp_id,
                risk_tier=0,
                reason="Exact read-only command is on the Sentinel allowlist",
                allowed_operations=[command],
                plan_sha256=plan_digest,
                issued_at=issued_at,
                expires_at=deadline,
                grant_token=_grant_token(plan_digest, exp_id, deadline),
            )

        if derived_tier == 1:
            return self._deny(exp_id, 1, TIER1_REVIEW_REASON)

        return self._deny(exp_id, 3, "Command is not on the exact read-only allowlist")

    def evaluate_tier1(
        self, plan: Dict[str, Any], authorization: Optional[PlanAuthorization]
    ) -> AuthorizationGrant:
        """Opens the Tier-1 leg, and only for a live authorization minted at a TTY.

        evaluate_plan() denies every Tier-1 plan; this method is the second half of
        the contract and refuses unless a person already confirmed these exact
        bytes. Tier 1 has no separate proposal artifact, so the ceremony binds the
        plan digest in the proposal slot as well as in the plan slot.
        """
        denial, exp_id, plan_digest, derived_tier, _ = self._review_static(plan)
        if denial is not None:
            return denial
        if derived_tier != 1:
            return self._deny(
                exp_id, derived_tier, "Tier-1 review requires a tier-1 state-changing plan"
            )

        intervention_digest = tier1_intervention_digest(plan)
        if intervention_digest is None:
            return self._deny(exp_id, 1, "Tier-1 plan declares no verifiable intervention")

        if type(authorization) is not PlanAuthorization:
            return self._deny(exp_id, 1, TIER1_REVIEW_REASON)
        if authorization.expired():
            return self._deny(exp_id, 1, "Local TTY authorization has expired")
        if authorization.used:
            return self._deny(exp_id, 1, "Local TTY authorization has already been used")
        if not authorization.binds(plan_digest, plan_digest, intervention_digest):
            return self._deny(
                exp_id,
                1,
                "Local TTY authorization is not bound to this exact plan and intervention",
            )

        intervention = tier1_intervention(plan)
        issued_at = time.monotonic()
        deadline = issued_at + authorization.remaining_seconds()
        return AuthorizationGrant(
            verdict=SentinelVerdict.AUTHORIZED,
            experiment_id=exp_id,
            risk_tier=1,
            reason="Local TTY authorization binds this exact plan digest and intervention",
            allowed_operations=[
                intervention["applied_command"],
                intervention["restoration_command"],
            ],
            plan_sha256=plan_digest,
            issued_at=issued_at,
            expires_at=deadline,
            grant_token=_grant_token(plan_digest, exp_id, deadline),
        )

    def verify_grant(
        self, grant: AuthorizationGrant, plan: Dict[str, Any], clock=time.monotonic
    ) -> bool:
        """Re-checks an issued grant against the plan about to execute.

        A clock that reads before issuance is treated as expired, so an injected
        clock can only shorten a grant's life, never extend it.
        """
        if not isinstance(grant, AuthorizationGrant) or not isinstance(plan, dict):
            return False
        if grant.verdict is not SentinelVerdict.AUTHORIZED:
            return False
        plan_digest = canonical_plan_digest(plan)
        if plan_digest is None or not isinstance(grant.plan_sha256, str):
            return False
        if not hmac.compare_digest(grant.plan_sha256, plan_digest):
            return False
        issued_at = grant.issued_at
        expires_at = grant.expires_at
        if isinstance(issued_at, bool) or isinstance(expires_at, bool):
            return False
        if not isinstance(issued_at, (int, float)) or not isinstance(expires_at, (int, float)):
            return False
        now = clock()
        if now < issued_at or now >= expires_at:
            return False
        if not isinstance(grant.grant_token, str) or not hmac.compare_digest(
            grant.grant_token, _grant_token(plan_digest, grant.experiment_id, expires_at)
        ):
            return False
        recheck = self.evaluate_plan(plan)
        if recheck.verdict is not SentinelVerdict.AUTHORIZED:
            return False
        if recheck.allowed_operations != grant.allowed_operations:
            return False
        return True
