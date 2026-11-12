# SPDX-License-Identifier: MIT
"""Tier-1 execution: exactly one reversible power-profile change per plan.

Nothing here is reachable without a live local TTY authorization bound to the
exact plan and intervention bytes (core.sentinel.evaluate_tier1). The campaign
applies one allowlisted profile, records the monitored surface, and always runs
and verifies the authorized restoration — including from a signal handler. A
restoration that cannot be verified writes an emergency fault record, and that
record denies every later campaign until a person has run the remediation and
resolved it.

A pending-intent record is written to the same ledger before the profile is
changed and removed only after the restoration is verified. A campaign killed
where no handler can run (SIGKILL, power loss) therefore leaves that record
behind, and the gate denies exactly as it does for a sealed fault. Nothing
restores the host automatically: the record makes the orphan visible.

A completed campaign's verdict is ROLLBACK_VERIFIED, not DEMONSTRATED. Tier-1
runs no workload, so it reports performance_verdict NOT_DEMONSTRATED beside it.

Tier-1 evidence is a sealed, manifest-hashed bundle beside the synthetic campaign
tree, so synthetic artifact allowlists and historical bundles stay untouched. The
read-only inspector covers synthetic campaigns only: whether a Tier-1 bundle proves
its own claims is NOT_DEMONSTRATED (docs/ANTIFAKE_TEST_MATRIX.md AF-09), so a
Tier-1 bundle is written for a human to verify with sha256sum -c.
"""

import hashlib
import json
import re
import signal
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from .authorization import AuthorizationDenied, PlanAuthorization
from .cpu_power import monitored_state
from .experiment import ExperimentCampaignResult, _CampaignEvidence
from .recovery import (
    FAULT_PENDING_CLOSE_OUT,
    FAULT_UNRESOLVED,
    FaultStateError,
    _FAULT_FIELDS,
    close_intent_record,
    find_unresolved_faults,
    resolve_fault,
    tier1_fault_base,
    write_fault_record,
)
from .rollback import (
    ROLLBACK_FAILED_STATE_DIFF,
    ROLLBACK_RESTORED_VERIFIED,
    ROLLBACK_VERIFIED_UNCHANGED,
    RollbackController,
    RollbackContract,
)
from .sentinel import (
    SentinelGate,
    SentinelVerdict,
    canonical_plan_digest,
    tier1_intervention,
    tier1_intervention_digest,
)

RESTORE_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT)
_FAULT_ID = re.compile(r"^[0-9a-f]{32}$")
# The validator demands repetitions >= 3; without a ceiling a hand-written plan
# could turn one authorized campaign into an unbounded read loop against the host.
MAX_TIER1_OBSERVATIONS = 16
# What a completed Tier-1 campaign proves: the change was applied and provably
# undone. It measures no workload, so it must not borrow the word that means
# "the effect is real" (DEMONSTRATED) from the performance vocabulary.
TIER1_VERDICT_ROLLBACK_VERIFIED = "ROLLBACK_VERIFIED"
TIER1_PERFORMANCE_VERDICT = "NOT_DEMONSTRATED"
INTENT_REASON = (
    "PENDING_CLOSE_OUT: a Tier-1 change was authorized and this record was written "
    "before applying it. It is removed only after the restoration is verified, so "
    "if you are reading it the campaign did not close out (killed, crashed or power "
    "lost) and the host may still carry the change"
)


class CampaignInterrupted(Exception):
    pass


def _canonical_bytes(value) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _state_differences(prestate, close_state) -> list:
    if not isinstance(prestate, dict) or not isinstance(close_state, dict):
        return []
    return [
        {
            "field": field,
            "prestate": prestate.get(field),
            "poststate": close_state.get(field),
        }
        for field in sorted(set(prestate) | set(close_state))
        if prestate.get(field) != close_state.get(field)
    ]


def build_tier1_plan(
    target_profile: str,
    current_profile: str,
    pci_id: str = "not-a-pci-device",
    device_family: str = "platform power profile provider",
) -> Dict[str, object]:
    """Writes the experiment plan for one profile change and the way back.

    The plan is built before the ceremony so the human reads the exact bytes that
    the gate will bind: the target is what changes, the rollback restores whatever
    this host was found running.
    """
    if target_profile not in RollbackController.PROFILES:
        raise ValueError("Target profile must be a supported power profile")
    if current_profile not in RollbackController.PROFILES:
        raise ValueError("Current profile must be a supported power profile")
    return {
        "experiment_id": "TIER1_POWER_PROFILE_" + target_profile.upper().replace("-", "_"),
        "target_hardware": {"pci_id": pci_id, "device_family": device_family},
        "hypothesis_id": "H-tier1-power-profile-1",
        "risk_tier": 1,
        "primary_variable": "monitored CPU power state after a platform profile change",
        "workload": {
            "command": f"powerprofilesctl set {target_profile}",
            "repetitions": 3,
            "timeout_sec": 5,
        },
        "controls": {
            "negative_control": "the same monitored state read after the authorized restoration",
            "sequence_pattern": "single_group",
            "output_hash_check": False,
        },
        "predicted_delta": (
            f"platform profile moves from {current_profile} to {target_profile} and back"
        ),
        "rollback_plan": {
            "restoration_command": f"powerprofilesctl set {current_profile}",
            "verification_query": "powerprofilesctl get",
            "expected_restored_state": current_profile,
        },
    }


class Tier1ExperimentRunner:
    """Runs the one authorized profile change, then puts the host back."""

    def __init__(
        self,
        sysfs_root: str = "",
        evidence_base: Optional[Path] = None,
        repository_root: Optional[Path] = None,
        fault_base: Optional[Path] = None,
    ):
        self.sysfs_root = sysfs_root
        self.evidence_base = evidence_base
        self.fault_base = fault_base
        self.repository_root = repository_root or Path(__file__).resolve().parent.parent

    @staticmethod
    def default_base() -> Path:
        return tier1_fault_base().parent / "tier1-experiments"

    def _denied(self, reason: str) -> ExperimentCampaignResult:
        return ExperimentCampaignResult(None, None, "DENIED", "DENY", 0, None, reason)

    def run(
        self, plan: Dict[str, object], authorization: PlanAuthorization
    ) -> ExperimentCampaignResult:
        grant = SentinelGate().evaluate_tier1(plan, authorization)
        if grant.verdict is not SentinelVerdict.AUTHORIZED:
            return self._denied(grant.reason)

        intervention = tier1_intervention(plan)
        plan_digest = canonical_plan_digest(plan)
        intervention_digest = tier1_intervention_digest(plan)

        repetitions = plan["workload"]["repetitions"]
        if repetitions > MAX_TIER1_OBSERVATIONS:
            return self._denied(
                f"workload.repetitions is {repetitions}; Tier-1 observes the host "
                f"surface at most {MAX_TIER1_OBSERVATIONS} times per campaign"
            )

        try:
            faults = find_unresolved_faults(self.fault_base)
        except FaultStateError as exc:
            return self._denied(str(exc))
        if faults:
            return self._denied(
                f"Emergency fault state is unresolved ({faults[0]['fault_id']}): "
                "restore the host and run 'resolve-fault' before any campaign"
            )

        campaign_id = uuid.uuid4().hex
        experiment_id = plan.get("experiment_id")
        contract = RollbackContract(sysfs_root=self.sysfs_root, experiment_id=campaign_id)
        guard = {"prestate": None, "restored": False, "signal": None, "emergency_error": None}
        started_monotonic = time.monotonic()
        started_at = _utc_now()
        status, verdict, reason = "ABORTED", "REFUTED", "EXPERIMENT_ABORTED"
        applied = None
        samples_recorded = 0
        prestate = None
        prestate_bytes = None
        campaign_record = None
        store = None
        intent_written = False

        handlers = self._install_guard(contract, guard)
        try:
            prestate = contract.capture_prestate()
            guard["prestate"] = prestate
            prestate_profile = contract.read_profile()
            owed = intervention["expected_restored_state"]
            if prestate_profile != owed:
                # The human approved restoring to one specific profile. If the host
                # is not there now, the authorized rollback no longer describes it.
                return self._denied(
                    f"Host profile is {prestate_profile!r} but the authorized rollback "
                    f"restores {owed!r}: the plan no longer describes this host"
                )
            contract.declare_restoration(prestate_profile)
            authorization.consume(plan_digest, plan_digest, intervention_digest)

            store = self._open_store(campaign_id)
            store.write_bytes("plan.json", _canonical_bytes(plan))
            prestate_bytes = _canonical_bytes(
                {
                    "campaign_id": campaign_id,
                    "plan_sha256": plan_digest,
                    "recorded_at": _utc_now(),
                    "state": prestate,
                    "profile": prestate_profile,
                }
            ) + b"\n"
            store.write_bytes("prestate.json", prestate_bytes)
            campaign_record = {
                "campaign_id": campaign_id,
                "plan_sha256": plan_digest,
                "intervention_sha256": intervention_digest,
                "authorization": authorization.to_evidence_record(),
                "intervention": intervention,
                "status": "RUNNING",
                "verdict": "NOT_DEMONSTRATED",
                "started_at": started_at,
                "ended_at": None,
                "duration_seconds": None,
                "rollback_status": None,
                "reason": None,
                "performance_claim": False,
                "performance_verdict": TIER1_PERFORMANCE_VERDICT,
            }
            store.write_json("campaign.json", campaign_record)

            # Write-ahead: from here until the verified close-out the ledger says
            # a change is outstanding. A failure to write it stops the campaign
            # with the host untouched.
            self._write_intent(
                campaign_id, experiment_id, plan_digest, contract, prestate, str(store.path)
            )
            intent_written = True

            receipt = contract.apply_intervention(intervention["target_profile"])
            applied = intervention["target_profile"]
            store.write_json("intervention.json", asdict(receipt))
            if not receipt.success:
                status, verdict = "DENIED", "DENY"
                reason = (
                    f"Intervention did not verify: "
                    f"{receipt.error_message or receipt.verified_state}"
                )
            else:
                samples = [
                    {
                        "sample": index,
                        "recorded_at": _utc_now(),
                        "state": monitored_state(self.sysfs_root),
                    }
                    for index in range(1, repetitions + 1)
                ]
                samples_recorded = len(samples)
                store.write_json(
                    "measurement.json",
                    {
                        "repetitions_declared": repetitions,
                        "samples_recorded": samples_recorded,
                        "samples": samples,
                        "recorded_at": _utc_now(),
                        "profile": contract.read_profile(),
                        "state": samples[-1]["state"],
                        "performance_claim": False,
                        "note": (
                            "Tier-1 observes the monitored surface once per declared "
                            "repetition after the authorized change; 'state' is the "
                            "final observation and 'profile' is the provider read-back. "
                            "It makes no throughput or latency claim."
                        ),
                    },
                )
                status, verdict = "COMPLETED", TIER1_VERDICT_ROLLBACK_VERIFIED
                reason = (
                    "Authorized profile change applied and recorded; rollback status "
                    "is decided by the sealed close-out"
                )
        except CampaignInterrupted as exc:
            reason = f"{exc}; restoration was attempted from the signal handler"
            if guard["emergency_error"]:
                reason = reason + f", which failed: {guard['emergency_error']}"
        except AuthorizationDenied as exc:
            return self._denied(str(exc))
        except (OSError, ValueError) as exc:
            if store is None:
                return self._denied(f"Tier-1 host surface unavailable: {exc}")
            status, verdict = "ABORTED", "REFUTED"
            reason = f"Tier-1 campaign failed: {exc}"
        except FaultStateError as exc:
            status, verdict = "ABORTED", "REFUTED"
            reason = f"Pending-intent record could not be written; nothing was applied: {exc}"
        finally:
            self._remove_guard(handlers)

        record = self._finalize(contract, prestate, applied, guard)
        if record["status"] == ROLLBACK_FAILED_STATE_DIFF:
            status, verdict = "ABORTED", "REFUTED"
            reason = "Rollback verification failed: " + record["reason"]
        elif status == "COMPLETED":
            reason = reason + "; " + record["reason"]

        evidence_dir = None
        if store is not None:
            self._seal(
                store,
                campaign_record,
                campaign_id,
                plan_digest,
                record,
                prestate_bytes,
                status,
                verdict,
                reason,
                started_monotonic,
            )
            evidence_dir = str(store.path)
            store.close()

        if intent_written and record["status"] != ROLLBACK_FAILED_STATE_DIFF:
            # The host is proven back, so nothing is outstanding. If the intent
            # cannot be removed it stays and denies, and resolve-fault closes it.
            try:
                close_intent_record(campaign_id, base=self.fault_base)
            except FaultStateError as exc:
                reason = reason + f"; pending intent was not removed: {exc}"

        # A fault record is only owed for a host this campaign actually touched:
        # an interruption before any write left the machine where the human found it.
        # Once the intent exists the sealed fault replaces it under the same id.
        if record["status"] == ROLLBACK_FAILED_STATE_DIFF and (
            applied is not None or guard["restored"] or intent_written
        ):
            try:
                self._record_fault(
                    campaign_id,
                    experiment_id,
                    plan_digest,
                    contract,
                    prestate,
                    record,
                    evidence_dir,
                )
            except FaultStateError as exc:
                reason = reason + f"; emergency fault record failed: {exc}"

        return ExperimentCampaignResult(
            campaign_id,
            plan_digest,
            status,
            verdict,
            samples_recorded,
            evidence_dir,
            reason,
        )

    def _finalize(
        self, contract: RollbackContract, prestate, applied, guard
    ) -> Dict[str, object]:
        """Re-observes, proves the restoration, and derives the rollback status.

        Tier-1 always runs the authorized restoration once an intervention was
        applied: proving the way back is what the ceremony bought, so restoration
        is never inferred from an observation that merely looks unchanged.
        """
        observation_error = None
        close_state = None
        verified_state = None
        differences = []
        receipt = contract.restoration_receipt

        if prestate is None:
            status = ROLLBACK_FAILED_STATE_DIFF
            reason = "No prestate was captured, so the host cannot be proven restored"
            observation_error = "prestate unavailable"
        else:
            try:
                matches = contract.verify_restoration(prestate)
                close_state = contract.observed_state
            except (OSError, ValueError) as exc:
                matches = False
                observation_error = str(exc)
            differences = [] if matches else _state_differences(prestate, close_state)
            verified_state = close_state

            if applied is not None or not matches or guard["restored"]:
                executed = False
                try:
                    executed = contract.execute_rollback(prestate)
                    guard["restored"] = True
                except (OSError, ValueError) as exc:
                    observation_error = str(exc)
                receipt = contract.restoration_receipt
                if executed:
                    try:
                        matches = contract.verify_restoration(prestate)
                        verified_state = contract.observed_state
                    except (OSError, ValueError) as exc:
                        matches = False
                        observation_error = str(exc)

            if applied is None and not guard["restored"]:
                status = ROLLBACK_VERIFIED_UNCHANGED if matches else ROLLBACK_FAILED_STATE_DIFF
                reason = (
                    "Host state was never changed by this campaign"
                    if matches
                    else "Host state moved without an applied intervention"
                )
            elif matches:
                status = ROLLBACK_RESTORED_VERIFIED
                reason = "Authorized restoration returned the host to the captured prestate"
            elif observation_error is not None and verified_state is None:
                status = ROLLBACK_FAILED_STATE_DIFF
                reason = f"Host state could not be observed at close: {observation_error}"
            elif receipt is not None and not receipt.success:
                status = ROLLBACK_FAILED_STATE_DIFF
                reason = (
                    f"Restoration did not verify: {receipt.error_message or receipt.verified_state}"
                )
            else:
                status = ROLLBACK_FAILED_STATE_DIFF
                reason = "Monitored host state still differs from the captured prestate"

        return {
            "campaign_id": None,
            "plan_sha256": None,
            "recorded_at": _utc_now(),
            "status": status,
            "reason": reason,
            "differences": differences,
            "restoration_executed": guard["restored"],
            "restoration_command": contract.restoration_command,
            "restoration_receipt": None if receipt is None else asdict(receipt),
            "observation_error": observation_error,
            "close_state": close_state,
            "verified_state": verified_state,
            "prestate_sha256": None,
            "poststate_sha256": None,
        }

    def _seal(
        self,
        store,
        campaign_record,
        campaign_id,
        plan_digest,
        record,
        prestate_bytes,
        status,
        verdict,
        reason,
        started_monotonic,
    ):
        poststate_bytes = _canonical_bytes(
            {
                "campaign_id": campaign_id,
                "plan_sha256": plan_digest,
                "recorded_at": _utc_now(),
                "state": record["close_state"],
                "observation_error": record["observation_error"],
            }
        ) + b"\n"
        store.write_bytes("poststate.json", poststate_bytes)
        record["campaign_id"] = campaign_id
        record["plan_sha256"] = plan_digest
        record["prestate_sha256"] = hashlib.sha256(prestate_bytes).hexdigest()
        record["poststate_sha256"] = hashlib.sha256(poststate_bytes).hexdigest()
        store.write_bytes("rollback.json", _canonical_bytes(record) + b"\n")
        campaign_record.update(
            {
                "status": status,
                "verdict": verdict,
                "reason": reason,
                "ended_at": _utc_now(),
                "duration_seconds": time.monotonic() - started_monotonic,
                "rollback_status": record["status"],
            }
        )
        store.write_json("campaign.json", campaign_record)

    def _record_fault(
        self, campaign_id, experiment_id, plan_digest, contract, prestate, record, evidence_dir
    ) -> Path:
        remediation = ["powerprofilesctl get"]
        if record["restoration_command"]:
            remediation.append(record["restoration_command"])
        remediation.append(f"python3 -m core.cli resolve-fault {campaign_id}")
        fields = {
            "schema_version": 1,
            "fault_id": campaign_id,
            "recorded_at": _utc_now(),
            "status": FAULT_UNRESOLVED,
            "reason": record["reason"],
            "rollback_status": record["status"],
            "experiment_id": experiment_id if isinstance(experiment_id, str) else None,
            "campaign_id": campaign_id,
            "plan_sha256": plan_digest,
            "expected_state": prestate,
            "observed_state": record["close_state"],
            "restoration_command": record["restoration_command"],
            "expected_restored_state": contract.prestate_profile,
            "remediation": remediation,
            "evidence_dir": evidence_dir,
            "resolved_at": None,
            "resolved_state": None,
            "resolved_profile": None,
        }
        if set(fields) != _FAULT_FIELDS or not _FAULT_ID.fullmatch(campaign_id):
            raise FaultStateError("Fault record does not match the emergency fault contract")
        return write_fault_record(fields, base=self.fault_base)

    def _write_intent(
        self, campaign_id, experiment_id, plan_digest, contract, prestate, evidence_dir
    ) -> Path:
        fields = {
            "schema_version": 1,
            "fault_id": campaign_id,
            "recorded_at": _utc_now(),
            "status": FAULT_UNRESOLVED,
            "reason": INTENT_REASON,
            "rollback_status": FAULT_PENDING_CLOSE_OUT,
            "experiment_id": experiment_id if isinstance(experiment_id, str) else None,
            "campaign_id": campaign_id,
            "plan_sha256": plan_digest,
            "expected_state": prestate,
            "observed_state": None,
            "restoration_command": contract.restoration_command,
            "expected_restored_state": contract.prestate_profile,
            "remediation": [
                "powerprofilesctl get",
                contract.restoration_command,
                f"python3 -m core.cli resolve-fault {campaign_id}",
            ],
            "evidence_dir": evidence_dir,
            "resolved_at": None,
            "resolved_state": None,
            "resolved_profile": None,
        }
        if set(fields) != _FAULT_FIELDS or not _FAULT_ID.fullmatch(campaign_id):
            raise FaultStateError("Intent record does not match the emergency fault contract")
        return write_fault_record(fields, base=self.fault_base)

    def _install_guard(self, contract: RollbackContract, guard):
        def handler(signum, _frame):
            guard["signal"] = signum
            if guard["prestate"] is not None:
                guard["restored"] = True
                try:
                    contract.execute_rollback(guard["prestate"])
                except (OSError, ValueError) as exc:
                    guard["emergency_error"] = str(exc)
            raise CampaignInterrupted(f"Tier-1 campaign interrupted by signal {signum}")

        previous = {}
        for signum in RESTORE_SIGNALS:
            try:
                previous[signum] = signal.getsignal(signum)
                signal.signal(signum, handler)
            except (OSError, ValueError):
                previous[signum] = None
        return previous

    @staticmethod
    def _remove_guard(previous):
        for signum, handler in previous.items():
            if handler is None:
                continue
            try:
                signal.signal(signum, handler)
            except (OSError, ValueError):
                pass

    def _open_store(self, campaign_id: str) -> _CampaignEvidence:
        base = self.evidence_base or self.default_base()
        return _CampaignEvidence(base, campaign_id, self.repository_root)

    def resolve(self, fault_id: str) -> Dict[str, object]:
        """Closes a fault only against a fresh observation that matches what was owed.

        The remediation commands in the fault record are for a person to run: this
        method verifies the host and never writes to it, so clearing an emergency
        fault state is never an unattended state change.
        """
        if not isinstance(fault_id, str) or not _FAULT_ID.fullmatch(fault_id):
            raise FaultStateError("Invalid fault identifier")
        contract = RollbackContract(sysfs_root=self.sysfs_root, experiment_id=fault_id)
        return resolve_fault(
            fault_id,
            monitored_state(self.sysfs_root),
            contract.read_profile(),
            _utc_now(),
            base=self.fault_base,
        )
