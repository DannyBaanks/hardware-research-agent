import hashlib
import json
import re
from dataclasses import dataclass
from typing import Dict

from .experiment import PlanValidationError, SyntheticExperimentPlan


class ProposalValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SyntheticExperimentProposal:
    schema_version: int
    proposal_id: str
    rationale: str
    plan: SyntheticExperimentPlan

    ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    MAX_RATIONALE_LENGTH = 1024
    MAX_PROPOSAL_BYTES = 16 * 1024

    def validate(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ProposalValidationError("Unsupported proposal schema_version")
        if not isinstance(self.proposal_id, str) or not self.ID_PATTERN.fullmatch(
            self.proposal_id
        ):
            raise ProposalValidationError("Invalid proposal_id")
        if (
            not isinstance(self.rationale, str)
            or not self.rationale.strip()
            or len(self.rationale) > self.MAX_RATIONALE_LENGTH
            or any(ord(char) < 32 and char not in "\t\n\r" for char in self.rationale)
        ):
            raise ProposalValidationError("Invalid proposal rationale")
        try:
            self.plan.validate()
        except PlanValidationError as exc:
            raise ProposalValidationError(str(exc)) from exc

    def to_dict(self) -> Dict[str, object]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "proposal_id": self.proposal_id,
            "rationale": self.rationale,
            "plan": self.plan.to_dict(),
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "proposal_id",
            "rationale",
            "plan",
        }:
            raise ProposalValidationError(
                "Proposal must contain only schema_version, proposal_id, rationale, and plan"
            )
        try:
            plan = SyntheticExperimentPlan.from_dict(value["plan"])
        except PlanValidationError as exc:
            raise ProposalValidationError(str(exc)) from exc
        proposal = cls(
            schema_version=value["schema_version"],
            proposal_id=value["proposal_id"],
            rationale=value["rationale"],
            plan=plan,
        )
        proposal.validate()
        return proposal

    @classmethod
    def from_bytes(cls, data: bytes):
        if len(data) > cls.MAX_PROPOSAL_BYTES:
            raise ProposalValidationError("Proposal exceeds the size limit")

        def unique_object(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ProposalValidationError(f"Duplicate proposal key: {key}")
                value[key] = item
            return value

        try:
            value = json.loads(data, object_pairs_hook=unique_object)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProposalValidationError("Proposal is not valid UTF-8 JSON") from exc
        return cls.from_dict(value)
