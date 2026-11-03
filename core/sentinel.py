import re
from typing import Dict, Any, List, Optional
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
    """
    Decoupled security and safety gatekeeper.
    Evaluates experiment plans, enforces invariant boundaries, and blocks dangerous actions.
    """
    
    # Strictly forbidden patterns (Tier 3 / Dangerous)
    FORBIDDEN_PATTERNS = [
        re.compile(r"\b(flashrom|nvramtool|fwupdtool)\b", re.IGNORECASE),
        re.compile(r"\b(wrmsr|rdmsr)\b", re.IGNORECASE),
        re.compile(r"\b(0x03b10[a-f0-9]+|smn|smu_message|smn_write)\b", re.IGNORECASE),
        re.compile(r"/dev/cpu/\d+/msr", re.IGNORECASE),
        re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
        re.compile(r">/sys/firmware/acpi/tables", re.IGNORECASE),
        re.compile(r">/sys/devices/system/cpu/cpufreq/boost\s*&&\s*reboot", re.IGNORECASE),
    ]

    ALLOWED_REVERSIBLE_COMMANDS = [
        re.compile(r"^powerprofilesctl\s+set\s+(power-saver|balanced|performance)$"),
        re.compile(r"^python3\s+[\w\./-]+\.py(\s+[\w\./-]+)*$"),
    ]

    def evaluate_plan(self, plan: Dict[str, Any], has_human_approval: bool = False) -> AuthorizationGrant:
        exp_id = plan.get("experiment_id", "UNKNOWN_EXP")
        risk_tier = plan.get("risk_tier", 3)
        workload = plan.get("workload", {})
        cmd = workload.get("command", "")
        rollback_plan = plan.get("rollback_plan", {})

        # 1. Tier 3 is strictly forbidden under all circumstances
        if risk_tier >= 3:
            return AuthorizationGrant(
                verdict=SentinelVerdict.DENIED,
                experiment_id=exp_id,
                risk_tier=risk_tier,
                reason="Tier 3 operations (firmware/SMU/MSR flashing) are strictly forbidden in V1",
                allowed_operations=[]
            )

        # 2. Check for forbidden instruction patterns
        for pattern in self.FORBIDDEN_PATTERNS:
            if pattern.search(cmd):
                return AuthorizationGrant(
                    verdict=SentinelVerdict.DENIED,
                    experiment_id=exp_id,
                    risk_tier=risk_tier,
                    reason=f"Command matches forbidden hardware safety pattern: '{pattern.pattern}'",
                    allowed_operations=[]
                )

        # 3. Verify Rollback Contract for state-modifying tiers
        if risk_tier >= 1:
            if not rollback_plan:
                return AuthorizationGrant(
                    verdict=SentinelVerdict.DENIED,
                    experiment_id=exp_id,
                    risk_tier=risk_tier,
                    reason="State-modifying experiment (Tier >= 1) missing mandatory rollback_plan",
                    allowed_operations=[]
                )
            if not rollback_plan.get("restoration_command") or not rollback_plan.get("verification_query"):
                return AuthorizationGrant(
                    verdict=SentinelVerdict.DENIED,
                    experiment_id=exp_id,
                    risk_tier=risk_tier,
                    reason="Rollback plan must define both restoration_command and verification_query",
                    allowed_operations=[]
                )
            if not has_human_approval:
                return AuthorizationGrant(
                    verdict=SentinelVerdict.DENIED,
                    experiment_id=exp_id,
                    risk_tier=risk_tier,
                    reason="Tier >= 1 operations require explicit human authorization grant",
                    allowed_operations=[]
                )

        # Tier 0 (Read-Only) or Authorized Tier 1
        return AuthorizationGrant(
            verdict=SentinelVerdict.AUTHORIZED,
            experiment_id=exp_id,
            risk_tier=risk_tier,
            reason="Experiment plan complies with IHRA safety boundaries",
            allowed_operations=[cmd]
        )
