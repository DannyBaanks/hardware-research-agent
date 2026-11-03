import re
import shlex
from typing import Dict, Any, List
from dataclasses import dataclass
from enum import Enum


class SentinelVerdict(Enum):
    AUTHORIZED = "AUTHORIZED"
    DENIED = "DENIED"


@dataclass
class AuthorizationGrant:
    verdict: SentinelVerdict
    experiment_id: str
    risk_tier: int
    reason: str
    allowed_operations: List[str]


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
            return AuthorizationGrant(
                verdict=SentinelVerdict.AUTHORIZED,
                experiment_id=exp_id,
                risk_tier=0,
                reason="Exact read-only command is on the Sentinel allowlist",
                allowed_operations=[command],
            )

        if derived_tier == 1:
            return self._deny(
                exp_id,
                1,
                "State changes are disabled: authenticated human authorization and an execution-coupled verified rollback are not implemented",
            )

        return self._deny(exp_id, 3, "Command is not on the exact read-only allowlist")
