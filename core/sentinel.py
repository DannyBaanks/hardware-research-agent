import hashlib
import hmac
import json
import re
import secrets
import shlex
import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from enum import Enum


class SentinelVerdict(Enum):
    AUTHORIZED = "AUTHORIZED"
    DENIED = "DENIED"


# Contract: docs/SAFETY_CONTRACTS.md 6.2. The key lives only in this process, so a
# token proves a grant was not rebuilt or reordered here; it proves nothing about
# who holds it, and a same-UID process can read it out of memory.
_GRANT_KEY = secrets.token_bytes(32)
GRANT_TTL_SECONDS = 120


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


class SentinelGate:
    """Fail-closed classifier for a deliberately small set of read-only commands."""

    READ_ONLY_COMMANDS = {
        ("lscpu",),
        ("lspci",),
        ("sensors",),
        ("powerprofilesctl", "get"),
        ("nvidia-smi",),
    }
    TIER1_PROFILE_COMMAND = re.compile(
        r"^powerprofilesctl\s+set\s+(?:power-saver|balanced|performance)$"
    )
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

    def evaluate_plan(self, plan: Dict[str, Any]) -> AuthorizationGrant:
        if not isinstance(plan, dict):
            return self._deny("UNKNOWN_EXP", 3, "Experiment plan must be an object")

        exp_id = plan.get("experiment_id", "UNKNOWN_EXP")
        if not isinstance(exp_id, str) or not exp_id:
            exp_id = "UNKNOWN_EXP"

        plan_digest = canonical_plan_digest(plan)
        if plan_digest is None:
            return self._deny(exp_id, 3, "Plan is not canonically serializable")

        workload = plan.get("workload")
        command = workload.get("command") if isinstance(workload, dict) else None
        if not isinstance(command, str) or not command or "\x00" in command:
            return self._deny(exp_id, 3, "Plan must contain a non-empty command string")

        try:
            argv = tuple(shlex.split(command, posix=True))
        except ValueError:
            return self._deny(exp_id, 3, "Malformed command string")

        if any(pattern.search(command) for pattern in self.FORBIDDEN_PATTERNS):
            return self._deny(exp_id, 3, "Command matches a forbidden hardware or destructive-operation rule")

        requested_tier = plan.get("risk_tier")
        if requested_tier is not None and (
            isinstance(requested_tier, bool)
            or not isinstance(requested_tier, int)
            or requested_tier not in range(4)
        ):
            return self._deny(exp_id, 3, "risk_tier must be an integer from 0 to 3")

        if argv in self.READ_ONLY_COMMANDS and command == " ".join(argv):
            derived_tier = 0
        elif self.TIER1_PROFILE_COMMAND.fullmatch(command):
            derived_tier = 1
        else:
            derived_tier = 3

        if requested_tier is not None and requested_tier != derived_tier:
            return self._deny(
                exp_id,
                derived_tier,
                f"Caller-supplied risk_tier does not match the command's derived tier ({derived_tier})",
            )

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
            return self._deny(
                exp_id,
                1,
                "State changes are disabled: authenticated human authorization and an execution-coupled verified rollback are not implemented",
            )

        return self._deny(exp_id, 3, "Command is not on the exact read-only allowlist")

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
